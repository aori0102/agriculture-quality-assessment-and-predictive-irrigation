import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import os
AI_ROOT = Path(os.environ.get('EDGE_AI_PATH', str(Path(__file__).resolve().parent)))
if not (AI_ROOT / 'time_helper.py').exists():
    AI_ROOT = Path('D:/Uni/IoT/Final/AI')
sys.path.append(str(AI_ROOT))
import unittest
from unittest.mock import Mock
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
from cloud_reporter import CloudReporter

class ReporterTests(unittest.TestCase):
    def event(self, event='pump_started', **changes):
        now = datetime.now(timezone.utc)
        data = dict(node_id='esp01', sent_at=(now-timedelta(seconds=1)).isoformat(),
                    valid_until=(now+timedelta(seconds=2)).isoformat(), event=event)
        data.update(changes)
        return SimpleNamespace(payload=json.dumps(data).encode(), retain=False)

    def test_uploads_and_lifecycle(self):
        r = CloudReporter(); client = Mock(); r.configure({'esp01': client})
        self.assertTrue(r.submit('send_telemetry', node_id='esp01', soil_index_pct=42))
        self.assertTrue(r.submit('send_ai_results', node_id='esp01', plant_status='healthy'))
        self.assertFalse(r.submit('send_telemetry', node_id='other'))
        r.close()
        client.send_telemetry.assert_called_once_with(node_id='esp01', soil_index_pct=42)
        client.send_ai_results.assert_called_once_with(node_id='esp01', plant_status='healthy')

    def test_pump_start_stop_and_duplicate(self):
        r = CloudReporter(); client = Mock(); r.configure({'esp01': client})
        msg = self.event()
        self.assertTrue(r.receive_pump_event(msg, 'esp01'))
        self.assertFalse(r.receive_pump_event(msg, 'esp01'))
        self.assertTrue(r.receive_pump_event(self.event('pump_stopped'), 'esp01'))
        r.close()
        self.assertEqual([call.kwargs['is_on'] for call in client.report_pump_status.call_args_list], [True, False])

    def test_invalid_events(self):
        r = CloudReporter()
        for changes in [dict(node_id='other'), dict(sent_at='bad'), dict(sent_at='2026-11-07T01:00:00'),
                        dict(valid_until='2026-11-07T01:00:03+00:00'), dict(event='unknown')]:
            self.assertFalse(r.receive_pump_event(self.event(**changes), 'esp01'))
        retained = self.event(); retained.retain=True
        self.assertFalse(r.receive_pump_event(retained, 'esp01'))
        for payload in [b'[]', b'null', b'{', b'\xff']:
            self.assertFalse(r.receive_pump_event(SimpleNamespace(payload=payload, retain=False), 'esp01'))

    def test_upload_failure_does_not_stop_worker(self):
        r=CloudReporter(); client=Mock(); client.send_telemetry.side_effect=RuntimeError()
        r.configure({'esp01':client})
        r.submit('send_telemetry',node_id='esp01')
        r.submit('send_ai_results',node_id='esp01',predicted_moisture_pct=40)
        r.close()
        client.send_ai_results.assert_called_once()




class WiringTests(unittest.TestCase):
    def load_functions(self, name, namespace):
        import ast
        source = Path(__file__).with_name(name)
        if not source.exists():
            source = Path(__file__).parent / 'IrrigationPrediction' / name
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        tree.body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
        exec(compile(tree, str(source), 'exec'), namespace)
        return namespace

    def test_mqtt_event_subscription_and_routing(self):
        from configparser import ConfigParser
        import paho.mqtt.client as mqtt
        from unittest.mock import patch
        reporter = Mock()
        client = Mock()
        client.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 7)
        client.connect.return_value = mqtt.MQTT_ERR_SUCCESS
        callbacks = {}
        client.message_callback_add.side_effect = lambda topic, callback: callbacks.update({topic: callback})
        def loop(**kwargs):
            client.on_connect(client, None, None, SimpleNamespace(is_failure=False), None)
            client.on_subscribe(client, None, 7, [SimpleNamespace(is_failure=False)]*3, None)
            event_filter = next(t for t in callbacks if t.endswith('/events'))
            message = SimpleNamespace(topic=event_filter.replace('+', 'esp01'), payload=b'{}', retain=False)
            callbacks[event_filter](client, None, message)
            raise KeyboardInterrupt
        client.loop.side_effect = loop
        root = AI_ROOT
        namespace = dict(__file__=str(root/'app.py'), json=json, mqtt=mqtt,
            ConfigParser=ConfigParser, Path=Path, sleep=Mock(), uuid4=Mock(),
            cloud_reporter=reporter, CONFIG_FILE='config.cfg',
            ThreadPoolExecutor=Mock(), ModelRetrainer=Mock(),
            operation_log_manager=Mock(), irrigation_handler=Mock(),
            irrigation_data_handler=Mock(), command_proxy=Mock(), capture_handler=Mock(),
            telemetry_handler=Mock())
        self.load_functions('app.py', namespace)
        with patch.object(mqtt, 'Client', return_value=client):
            namespace['main']()
        subscriptions = client.subscribe.call_args.args[0]
        self.assertEqual(len(subscriptions), 3)
        self.assertIn(('/'.join(subscriptions[0][0].split('/')[:-1])+'/events', 1), subscriptions)
        self.assertEqual(reporter.receive_pump_event.call_args.args[1], 'esp01')

    def test_only_accepted_telemetry_enqueued(self):
        import importlib.util
        import paho.mqtt.client as mqtt
        from time_helper import TimeHelper
        spec = importlib.util.spec_from_file_location('fields', str(AI_ROOT / 'IrrigationPrediction/field_constants.py'))
        fc=importlib.util.module_from_spec(spec); spec.loader.exec_module(fc)
        reporter=Mock(); data=Mock(); log=Mock()
        ns=dict(mqtt=mqtt, json=json, cloud_reporter=reporter, irrigation_data_handler=data,
                operation_log_manager=log, fc=fc, TimeHelper=TimeHelper,
                timezone=timezone, datetime=datetime,
                RAW_TELEMTRY_FIELDS=(fc.SAMPLED_AT,fc.SOIL_INDEX_PCT,fc.TEMPERATURE_C,fc.AIR_HUMIDITY_PCT,fc.QUALITY))
        self.load_functions('telemetry_handler.py',ns)
        handler=ns['TelemetryHandler']()
        payload=dict(node_id='esp01',sampled_at='2026-11-19T01:00:00+00:00',soil_index_pct=42,
                     temperature_c=28.5,air_humidity_pct=65,quality=dict(soil='valid',dht='valid'))
        msg=SimpleNamespace(topic='iot/edge/esp01/telemetry',payload=json.dumps(payload).encode())
        self.assertTrue(handler.record_sensors_telemetry(msg,'esp01'))
        reporter.submit.assert_called_once_with('send_telemetry',node_id='esp01',soil_index_pct=42,temperature_c=28.5,air_humidity_pct=65)
        reporter.reset_mock();payload['node_id']='wrong';msg.payload=json.dumps(payload).encode()
        self.assertFalse(handler.record_sensors_telemetry(msg,'esp01'))
        reporter.submit.assert_not_called()

class LauncherWiringTests(unittest.TestCase):
    def test_reporter_lifecycle_with_clients(self):
        import ast
        from contextlib import ExitStack
        root=Path(__file__).resolve().parent
        source=root/'launcher.py'
        if not source.exists():
            source=root.parent/'Cloud Edge/cloud_edge/launcher.py'
        tree=ast.parse(source.read_text(encoding='utf-8-sig'))
        tree.body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_with_clients']
        ns=dict(ExitStack=ExitStack,CloudClient=Mock(),PumpDispatcher=Mock())
        exec(compile(tree,str(source),'exec'),ns)
        client,reporter=Mock(),Mock();events=[]
        reporter.configure.side_effect=lambda clients: events.append(('configured',clients))
        reporter.close.side_effect=lambda: events.append('reporter_closed')
        client.close.side_effect=lambda: events.append('client_closed')
        ns['run_with_clients'](lambda: events.append('edge_running'),object(),
            [SimpleNamespace(node_id='esp01',config=object())],
            client_factory=Mock(return_value=client),reporter=reporter)
        self.assertEqual(events,[('configured',{'esp01':client}),'edge_running','reporter_closed','client_closed'])

if __name__ == '__main__': unittest.main()
