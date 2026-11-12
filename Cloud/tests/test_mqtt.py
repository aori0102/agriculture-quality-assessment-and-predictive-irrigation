from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import ssl
import threading
import traceback
import unittest
from unittest.mock import Mock, patch

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from Cloud.blynk_mqtt import BlynkMQTT
from Cloud.config import CloudConfig
from Cloud.datastreams import BlynkTopics
from Cloud.errors import CloudError


def reason(packet, value=0):
    return ReasonCode(packet, identifier=value)


class MQTTTests(unittest.TestCase):
    def setUp(self):
        self.network = Mock(spec=mqtt.Client)
        self.network.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 7)
        self.network.loop_start.return_value = mqtt.MQTT_ERR_SUCCESS
        patcher = patch("Cloud.blynk_mqtt.mqtt.Client", return_value=self.network)
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)
        self.adapter = BlynkMQTT(CloudConfig("sgp1.blynk.cloud", "test-secret"), node_id="node-1")
        self.addCleanup(self.adapter.close)

    def connect(self):
        self.adapter.start()
        self.network.on_connect(self.network, None, None, reason(PacketTypes.CONNACK), None)

    def ready(self):
        self.connect()
        self.network.on_subscribe(self.network, None, 7, [reason(PacketTypes.SUBACK, 1)], None)

    def receive(self, payload=b"1", topic=BlynkTopics.PUMP_COMMAND, retain=False):
        message = mqtt.MQTTMessage(topic=topic.encode())
        message.payload = payload
        message.retain = retain
        self.network.on_message(self.network, None, message)

    def disconnect(self):
        self.network.on_disconnect(self.network, None, None, reason(PacketTypes.DISCONNECT, 128), None)

    def test_secure_nonblocking_start_and_subscription_readiness(self):
        self.network.connect_async.assert_not_called()
        self.network.loop_start.assert_not_called()
        self.factory.assert_called_once_with(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv311, clean_session=True,
        )
        self.network.username_pw_set.assert_called_once_with("device", "test-secret")
        context = self.network.tls_set_context.call_args.args[0]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.network.reconnect_delay_set.assert_called_once_with(min_delay=1, max_delay=30)
        self.connect()
        self.network.connect_async.assert_called_once_with("sgp1.blynk.cloud", 8883, keepalive=45)
        self.network.subscribe.assert_called_once_with(BlynkTopics.PUMP_COMMAND, qos=1)
        self.assertFalse(self.adapter.commands_ready)
        self.network.on_subscribe(self.network, None, 999, [reason(PacketTypes.SUBACK, 1)], None)
        self.assertFalse(self.adapter.commands_ready)
        self.network.on_subscribe(self.network, None, 7, [reason(PacketTypes.SUBACK, 1)], None)
        self.assertTrue(self.adapter.commands_ready)
        self.assertIsNone(self.adapter.last_command_error)
        self.network.publish.assert_not_called()

    def test_latest_state_wins_and_poll_consumes(self):
        self.ready()
        self.receive(b"1")
        self.receive(b" 0\n")
        command = self.adapter.poll_command()
        self.assertEqual(command.node_id, "node-1")
        self.assertFalse(command.requested_on)
        self.assertEqual(command.received_at.tzinfo, timezone.utc)
        self.assertEqual((command.expires_at - command.received_at).total_seconds(), 5)
        self.assertFalse(command.is_expired())
        self.assertIsNone(self.adapter.poll_command())
        with self.assertRaises(FrozenInstanceError):
            command.requested_on = True

    def test_only_fresh_valid_command_messages_are_accepted(self):
        self.receive()
        self.assertIsNone(self.adapter.poll_command())
        self.ready()
        for value in (b"", b"true", b"2", b"1.0", b"01", b"\xff", b"1\x000"):
            with self.subTest(value=value):
                self.receive(value)
                self.assertIsNone(self.adapter.poll_command())
        self.receive(retain=True)
        self.receive(topic="downlink/ds/Pump Status")
        self.assertIsNone(self.adapter.poll_command())
        self.receive(b"1")
        self.assertTrue(self.adapter.poll_command().requested_on)

    def test_expiry_before_and_after_poll_uses_monotonic_time(self):
        self.ready()
        with patch("Cloud.commands.monotonic", return_value=100) as clock:
            self.receive()
            command = self.adapter.poll_command()
            clock.return_value = 104.99
            self.assertFalse(command.is_expired())
            clock.return_value = 105
            self.assertTrue(command.is_expired())
            self.receive()
            clock.return_value = 110
            self.assertIsNone(self.adapter.poll_command())

    def test_command_expiry_survives_wall_clock_adjustments(self):
        self.ready()
        with patch("Cloud.commands.monotonic", return_value=100) as clock, \
                patch("Cloud.commands.datetime") as wall_clock:
            wall_clock.now.return_value = datetime(2026, 1, 1, tzinfo=timezone.utc)
            self.receive()
            command = self.adapter.poll_command()
            wall_clock.now.return_value = datetime(2025, 1, 1, tzinfo=timezone.utc)
            clock.return_value = 106
            self.assertTrue(command.is_expired())

    def test_command_lifetime_override(self):
        adapter = BlynkMQTT(
            CloudConfig("sgp1.blynk.cloud", "test-secret", command_lifetime=2), node_id="node-1"
        )
        self.addCleanup(adapter.close)
        adapter.start()
        self.network.on_connect(self.network, None, None, reason(PacketTypes.CONNACK), None)
        self.network.on_subscribe(self.network, None, 7, [reason(PacketTypes.SUBACK, 1)], None)
        with patch("Cloud.commands.monotonic", return_value=10) as clock:
            self.receive()
            command = adapter.poll_command()
            self.assertEqual((command.expires_at - command.received_at).total_seconds(), 2)
            clock.return_value = 12
            self.assertTrue(command.is_expired())

    def test_startup_failure_is_sanitized_and_terminal(self):
        self.network.loop_start.side_effect = RuntimeError("test-secret")
        try:
            self.adapter.start()
        except CloudError as error:
            self.assertEqual(error.category, "startup")
            self.assertNotIn("test-secret", "".join(traceback.format_exception(error)))
        else:
            self.fail("Expected CloudError")
        self.adapter.start()
        self.network.loop_start.assert_called_once()

    def test_disconnect_clears_pending_and_reconnect_resubscribes(self):
        self.ready()
        self.receive()
        self.disconnect()
        self.assertFalse(self.adapter.commands_ready)
        self.assertIsNone(self.adapter.poll_command())
        self.assertIsNotNone(self.adapter.last_command_error)
        self.ready()
        self.assertEqual(self.network.subscribe.call_count, 2)
        self.assertTrue(self.adapter.commands_ready)
        self.assertIsNone(self.adapter.last_command_error)
        self.assertIsNone(self.adapter.poll_command())

    def test_transient_failures_are_reported_without_stopping_retries(self):
        self.adapter.start()
        self.network.on_connect_fail(self.network, None)
        self.assertIsNotNone(self.adapter.last_command_error)
        self.network.on_connect(self.network, None, None, reason(PacketTypes.CONNACK, 136), None)
        self.network.disconnect.assert_not_called()
        self.network.loop_stop.assert_not_called()
        self.assertFalse(self.adapter.commands_ready)

    def test_authentication_failure_is_terminal(self):
        for code in (134, 135):
            with self.subTest(code=code):
                self.network.on_connect(self.network, None, None, reason(PacketTypes.CONNACK, code), None)
                self.assertEqual(self.adapter.last_command_error.category, "authentication")
                self.assertFalse(self.adapter.commands_ready)
        self.assertTrue(self.network.disconnect.called)
        self.assertTrue(self.network.loop_stop.called)
        original = self.adapter.last_command_error
        self.disconnect()
        self.assertIs(self.adapter.last_command_error, original)

    def test_subscription_rejection_is_terminal(self):
        self.connect()
        self.network.on_subscribe(self.network, None, 7, [reason(PacketTypes.SUBACK, 128)], None)
        self.assertEqual(self.adapter.last_command_error.category, "subscription")
        self.assertFalse(self.adapter.commands_ready)
        self.network.disconnect.assert_called_once()
        self.network.loop_stop.assert_called_once()

    def test_subscribe_queue_failure_is_terminal(self):
        self.network.subscribe.return_value = (mqtt.MQTT_ERR_NO_CONN, None)
        self.connect()
        self.assertEqual(self.adapter.last_command_error.category, "subscription")
        self.assertFalse(self.adapter.commands_ready)

    def test_redirect_is_terminal_and_never_exposes_or_follows_payload(self):
        self.ready()
        self.receive()
        self.receive(b"mqtts://device:test-secret@evil.example:1883", topic="downlink/redirect")
        self.assertFalse(self.adapter.commands_ready)
        self.assertIsNone(self.adapter.poll_command())
        self.assertEqual(self.adapter.last_command_error.category, "redirect")
        self.assertNotIn("test-secret", str(self.adapter.last_command_error))
        self.assertNotIn("evil.example", str(self.adapter.last_command_error))
        self.assertEqual(self.network.connect_async.call_count, 1)
        self.network.disconnect.assert_called_once()
        self.network.loop_stop.assert_called_once()

    def test_lifecycle_and_late_callbacks(self):
        self.ready()
        self.adapter.start()
        self.network.loop_start.assert_called_once()
        self.receive()
        self.adapter.close()
        self.adapter.close()
        self.network.disconnect.assert_called_once()
        self.network.loop_stop.assert_called_once()
        self.receive()
        self.network.on_subscribe(self.network, None, 7, [reason(PacketTypes.SUBACK, 1)], None)
        self.assertIsNone(self.adapter.poll_command())
        self.assertFalse(self.adapter.commands_ready)
        with self.assertRaises(CloudError):
            self.adapter.start()

    def test_receiving_and_polling_concurrently_does_not_duplicate_commands(self):
        self.ready()
        barrier = threading.Barrier(2)
        received = []

        def producer():
            barrier.wait()
            for _ in range(200):
                self.receive()

        def consumer():
            barrier.wait()
            for _ in range(200):
                command = self.adapter.poll_command()
                if command is not None:
                    received.append(command)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(producer), executor.submit(consumer)]
            for future in futures:
                future.result(timeout=5)
        command = self.adapter.poll_command()
        if command is not None:
            received.append(command)
        self.assertTrue(received)
        self.assertEqual(len(received), len({id(command) for command in received}))
        self.assertLessEqual(len(received), 200)


if __name__ == "__main__":
    unittest.main()
