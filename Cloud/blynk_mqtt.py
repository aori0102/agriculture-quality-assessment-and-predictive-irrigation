"""Receive Blynk pump intent without running edge work on the network thread."""

import ssl
from threading import Lock

import paho.mqtt.client as mqtt

from .commands import PumpCommand
from .config import CloudConfig
from .datastreams import BlynkTopics
from .errors import CloudError


class BlynkMQTT:
    def __init__(self, config: CloudConfig, *, node_id: str):
        self._config = config
        self._node_id = node_id
        self._lock = Lock()
        # Joining the network thread must never hold the callback-state lock.
        self._lifecycle_lock = Lock()
        self._closed = False
        self._started = False
        self._terminal = False
        self._ready = False
        self._subscription_mid: int | None = None
        self._pending: PumpCommand | None = None
        self._error: CloudError | None = None

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv311, clean_session=True,
        )
        self._client.username_pw_set("device", config.auth_token)
        self._client.tls_set_context(ssl.create_default_context())
        self._client.connect_timeout = config.http_timeout
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_subscribe = self._on_subscribe
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    @property
    def commands_ready(self) -> bool:
        with self._lock:
            return self._ready

    @property
    def last_command_error(self) -> CloudError | None:
        with self._lock:
            return self._error

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._closed:
                    raise CloudError("Cloud MQTT client is closed.", category="closed")
                if self._started or self._terminal:
                    return
            try:
                self._client.connect_async(
                    self._config.server, self._config.mqtt_port,
                    keepalive=self._config.mqtt_keepalive,
                )
                result = self._client.loop_start()
            except (OSError, ValueError, RuntimeError):
                self._fail("Could not start Blynk MQTT reception.", "startup")
                raise self.last_command_error from None
            if result != mqtt.MQTT_ERR_SUCCESS:
                self._fail("Could not start the Blynk MQTT network loop.", "startup")
                raise self.last_command_error
            with self._lock:
                self._started = True

    def poll_command(self) -> PumpCommand | None:
        with self._lock:
            command, self._pending = self._pending, None
            if self._closed or not self._ready or command is None or command.is_expired():
                return None
            return command

    def close(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                self._ready = False
                self._pending = None
                self._subscription_mid = None
            try:
                self._client.disconnect()
            finally:
                self._client.loop_stop()

    def _fail(self, message: str, category: str) -> None:
        with self._lock:
            if self._closed or self._terminal:
                return
            self._terminal = True
            self._ready = False
            self._pending = None
            self._subscription_mid = None
            self._error = CloudError(message, category=category)
        self._client.disconnect()
        # Paho permits loop_stop in its own thread (it sets the stop flag without joining).
        self._client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        with self._lock:
            inactive = self._closed or self._terminal
            self._ready = False
            self._pending = None
            self._subscription_mid = None
        if inactive:
            client.disconnect()
            return
        if reason_code.is_failure:
            if reason_code.value in (134, 135):
                self._fail("Blynk MQTT authentication failed; check BLYNK_AUTH_TOKEN.", "authentication")
            elif reason_code.value in (136, 137):
                self._on_connect_fail(client, userdata)
            else:
                self._fail("Blynk MQTT connection refused; check device configuration.", "connection")
            return
        with self._lock:
            if self._closed or self._terminal:
                return
            result, mid = client.subscribe(BlynkTopics.PUMP_COMMAND, qos=1)
            self._subscription_mid = mid
        if result != mqtt.MQTT_ERR_SUCCESS:
            self._fail("Could not subscribe to Blynk pump commands.", "subscription")

    def _on_connect_fail(self, client, userdata) -> None:
        with self._lock:
            if not self._closed and not self._terminal:
                self._ready = False
                self._pending = None
                self._error = CloudError("Blynk MQTT connection failed; retrying.", category="network")

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties) -> None:
        with self._lock:
            if self._closed or self._terminal or mid != self._subscription_mid:
                return
            accepted = len(reason_codes) == 1 and not reason_codes[0].is_failure
            if accepted:
                self._ready = True
                self._error = None
        if not accepted:
            self._fail("Blynk rejected the pump command subscription; check device permissions.", "subscription")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        with self._lock:
            self._ready = False
            self._pending = None
            self._subscription_mid = None
            if not self._closed and not self._terminal:
                self._error = CloudError("Blynk MQTT disconnected; reconnecting.", category="network")

    def _on_message(self, client, userdata, message) -> None:
        if message.topic == BlynkTopics.REDIRECT:
            self._fail(
                "Blynk MQTT requested a redirect; set BLYNK_SERVER to the device's regional host.",
                "redirect",
            )
            return
        if message.topic != BlynkTopics.PUMP_COMMAND or message.retain:
            return
        try:
            value = message.payload.decode("utf-8").strip()
        except UnicodeDecodeError:
            return
        if value not in ("0", "1"):
            return
        with self._lock:
            if self._ready and not self._closed and not self._terminal:
                self._pending = PumpCommand._received(
                    self._node_id, value == "1", self._config.command_lifetime,
                )
