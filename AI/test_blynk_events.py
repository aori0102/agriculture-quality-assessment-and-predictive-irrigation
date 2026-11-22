"""Offline regression tests for cloud events and monitoring-only forecasts."""
import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from cloud_reporter import CloudReporter
import pump_control


class EventTests(TestCase):
    def test_soil_episode_escalates_and_rearms_without_spam(self):
        reporter = CloudReporter()
        client = Mock()
        reporter.configure({'one': client})
        try:
            for soil in (19, 18, 9, 8, 12):
                reporter.observe_soil(node_id='one', soil_pct=soil, pwp_pct=10, madp_pct=20)
            reporter.queue.join()
            self.assertEqual([c.kwargs['code'] for c in client.log_event.call_args_list],
                             ['soil_moisture_low', 'soil_moisture_critical'])
            reporter.observe_soil(node_id='one', soil_pct=20, pwp_pct=10, madp_pct=20)
            with patch('cloud_reporter.monotonic', return_value=10**12):
                reporter.observe_soil(node_id='one', soil_pct=19, pwp_pct=10, madp_pct=20)
            reporter.queue.join()
            self.assertEqual(client.log_event.call_count, 3)
        finally:
            reporter.close()

    def test_shared_budget_and_minimum_interval(self):
        reporter = CloudReporter()
        reporter.clients = {'one': Mock()}
        self.assertTrue(reporter.emit_event(node_id='one', code='pump_started', description='on'))
        self.assertFalse(reporter.emit_event(node_id='one', code='pump_started', description='on'))
        for index in range(89):
            self.assertTrue(reporter.emit_event(node_id='one', code=f'event_{index}', description='test'))
        self.assertFalse(reporter.emit_event(node_id='one', code='another', description='test'))
        self.assertFalse(reporter.emit_event(node_id='unknown', code='another', description='test'))

    def test_pump_heartbeats_do_not_create_repeated_events(self):
        reporter = Mock()
        log = Mock()
        log.load_log.return_value = {'node_profile': {'one': {}}}
        with patch.object(pump_control, 'cloud_reporter', reporter), patch.object(pump_control, 'operation_log_manager', log):
            coordinator = pump_control.PumpCoordinator()
            for seq, state in enumerate((False, False, True, True, False, False), 1):
                coordinator._accept_event('one', dict(boot_id='boot', sequence=seq,
                    event='pump_started' if state else 'pump_stopped'))
        self.assertEqual([c.kwargs['code'] for c in reporter.emit_event.call_args_list],
                         ['pump_started', 'pump_stopped'])


class MonitoringForecastTests(TestCase):
    def test_auto_off_still_uploads_forecast_and_never_starts_pump(self):
        from time_helper import TimeHelper
        from IrrigationPrediction import field_constants as fc
        source = Path(__file__).parent / 'IrrigationPrediction/irrigation_handler.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'handle_irrigation')
        now = TimeHelper.get_current_slot()
        data = Mock()
        data.has_irrigation_duration.return_value = False
        data.get_irrigation_data.return_value = {fc.TIMESTAMP: now, fc.SOIL_MOISTURE: 35,
            fc.SOIL_CHANGE_10M: -1, fc.WEATHER_TEMPERATURE: 28, fc.WEATHER_HUMIDITY: 65,
            fc.IRRIGATION_PAST_10M: 0}
        log = Mock()
        log.get_node_profile.return_value = SimpleNamespace(auto_irrigation_enabled=False)
        reporter, telemetry = Mock(), Mock()
        handler = SimpleNamespace(current_sample_slot=now, predict=Mock(return_value=34), send_irrigation_command=Mock())
        namespace = dict(logger=Mock(), prediction_allowed=lambda _: True,
            operation_log_manager=log, irrigation_data_handler=data, cloud_reporter=reporter,
            telemetry_handler=telemetry, TimeHelper=TimeHelper, fc=fc)
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
        namespace['handle_irrigation'](handler, 'one')
        reporter.submit.assert_called_once_with('send_ai_results', node_id='one', predicted_moisture_pct=34.0)
        handler.send_irrigation_command.assert_not_called()
        telemetry.record_irrigation_data.assert_called_once_with(0, node_id='one', timestamp=now)
        reporter.reset_mock()
        namespace['prediction_allowed'] = lambda _: False
        self.assertFalse(namespace['handle_irrigation'](handler, 'one'))
        reporter.submit.assert_not_called()
