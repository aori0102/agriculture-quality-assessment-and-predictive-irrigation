"""The public interface consumed by the edge application."""

from threading import Lock

from .blynk_http import BlynkHTTP
from .datastreams import BlynkPins
from .blynk_mqtt import BlynkMQTT
from .commands import PumpCommand
from .config import CloudConfig
from .errors import CloudError
from .profiles import ProfileBackend
from .profile_sync import ProfileSync


class CloudClient:
    """One Blynk device bound to one edge node; construction does not connect.

    Args:
        config: Validated Blynk credentials and transport settings.
        node_id: Nonempty edge node identifier, matched exactly on every upload.
        profile_backend: Optional Edge-owned profile persistence/activation
            backend. Enables a serial worker and profile subscriptions on start.

    Raises:
        ValueError: If node_id is not a nonempty string.

    HTTP uploads work without start(). Call start() for background command
    reception and close() in a finally block. See Cloud/API.md for examples.
    """

    def __init__(self, config: CloudConfig, *, node_id: str,
                 profile_backend: ProfileBackend | None = None):
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("node_id must be a nonempty string.")
        if profile_backend is not None:
            if len(node_id) > 128 or not node_id.isprintable():
                raise ValueError("Profile node_id must be printable text of at most 128 characters.")
            if not all(callable(getattr(profile_backend, method, None))
                       for method in ("get_active", "apply_profile")):
                raise ValueError("profile_backend must implement get_active and apply_profile.")
        self._node_id = node_id
        self._http = BlynkHTTP(config)
        self._profiles = (ProfileSync(self._http, profile_backend, node_id=node_id)
                          if profile_backend is not None else None)
        self._mqtt = BlynkMQTT(config, node_id=node_id, profile_sync=self._profiles)
        self._lock = Lock()
        self._closed = False

    @property
    def command_connection_id(self) -> int:
        """Connection generation used to invalidate in-flight request reads."""
        return self._mqtt.command_connection_id

    @property
    def commands_ready(self) -> bool:
        """Whether MQTT is connected and its command subscription is accepted."""
        return self._mqtt.commands_ready

    @property
    def last_command_error(self) -> CloudError | None:
        """Latest MQTT failure, cleared on successful subscription; not HTTP status."""
        return self._mqtt.last_command_error

    @property
    def profiles_ready(self) -> bool:
        """True after subscription and successful restoration of Edge state."""
        return self._profiles.ready if self._profiles is not None else False

    @property
    def last_profile_status(self) -> str:
        """Latest local outcome, even when dashboard delivery failed."""
        return self._profiles.last_status if self._profiles is not None else ""

    @property
    def last_profile_error(self) -> CloudError | None:
        """Latest profile transport error; inspect last_command_error for MQTT."""
        return self._profiles.last_error if self._profiles is not None else None

    def start(self) -> None:
        """Start background MQTT reception and return None without waiting.

        Check commands_ready before assuming the subscription is active.
        Repeated calls do nothing; terminal errors require a new client.
        Raises CloudError if closed or the network loop cannot start.
        """
        with self._lock:
            self._ensure_open()
            if self._profiles is not None:
                self._profiles.start()
            self._mqtt.start()

    def send_snapshot(
        self, *, node_id: str,
        soil_index_pct: int | float | None = None,
        temperature_c: int | float | None = None,
        air_humidity_pct: int | float | None = None,
        plant_status: str | None = None,
        predicted_moisture_pct: int | float | None = None,
        pump_is_on: bool | None = None,
    ) -> None:
        """Upload supplied current values in one HTTP batch; return None.

        Args:
            node_id: Must exactly match this client's bound node.
            soil_index_pct: Soil percentage, 0–100; rounded half upward to V0.
            temperature_c: Finite Celsius measurement, sent as a float to V1.
            air_humidity_pct: Humidity percentage, 0–100; rounded to V6.
            plant_status: Edge-generated assessment string for V2.
            predicted_moisture_pct: Prediction, 0–100; rounded to V5.
            pump_is_on: Firmware-confirmed controller state for V4.

        None omits a field; an empty snapshot makes no request. Booleans are
        not measurements. Uploads block until completion/failure and do not
        retry automatically; keep them outside network callbacks.

        Raises:
            ValueError: Node mismatch, invalid type, or invalid numeric value.
            CloudError: Closed client or HTTP/connection/timeout/redirect failure.
        """
        with self._lock:
            self._ensure_open()
            if node_id != self._node_id:
                raise ValueError("node_id does not match this CloudClient's binding.")
            self._http.send_snapshot(
                soil_index_pct=soil_index_pct, temperature_c=temperature_c,
                air_humidity_pct=air_humidity_pct, plant_status=plant_status,
                predicted_moisture_pct=predicted_moisture_pct, pump_is_on=pump_is_on,
            )

    def send_telemetry(
        self, *, node_id: str,
        soil_index_pct: int | float | None = None,
        temperature_c: int | float | None = None,
        air_humidity_pct: int | float | None = None,
    ) -> None:
        """Upload supplied soil, temperature, and humidity values; return None.

        Arguments, validation, omission rules, and exceptions match
        send_snapshot(). node_id is required; all measurements are optional.
        """
        self.send_snapshot(
            node_id=node_id, soil_index_pct=soil_index_pct,
            temperature_c=temperature_c, air_humidity_pct=air_humidity_pct,
        )

    def send_ai_results(
        self, *, node_id: str, plant_status: str | None = None,
        predicted_moisture_pct: int | float | None = None,
    ) -> None:
        """Upload optional assessment text and predicted percentage; return None.

        Arguments, validation, omission rules, and exceptions match
        send_snapshot(). node_id is required; both result fields are optional.
        """
        self.send_snapshot(
            node_id=node_id, plant_status=plant_status,
            predicted_moisture_pct=predicted_moisture_pct,
        )

    def log_event(self, *, node_id: str, code: str, description: str = "") -> None:
        """Send a Timeline event; its code must already exist in the template."""
        with self._lock:
            self._ensure_open()
            if node_id != self._node_id:
                raise ValueError("node_id does not match this CloudClient's binding.")
            self._http.log_event(code=code, description=description)

    def report_pump_status(self, *, node_id: str, is_on: bool) -> None:
        """Upload firmware-confirmed controller state to V4; return None.

        node_id must match the bound node. is_on must be bool, not None or an
        integer. Call after a device event, never just on command receipt.
        Raises ValueError for invalid inputs and CloudError for upload failures.
        """
        if type(is_on) is not bool:
            raise ValueError("is_on must be a boolean confirmed by the device.")
        self.send_snapshot(node_id=node_id, pump_is_on=is_on)

    def report_control_mode(self, *, node_id: str, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean.")
        with self._lock:
            self._ensure_open()
            if node_id != self._node_id:
                raise ValueError("node_id does not match this CloudClient's binding.")
            self._mqtt.expect_mode_echo(enabled)
            self._http.write_control(BlynkPins.AUTO_IRRIGATION_ENABLED, int(enabled))

    def report_pump_detail(self, *, node_id: str, detail: str) -> None:
        with self._lock:
            self._ensure_open()
            if node_id != self._node_id:
                raise ValueError("node_id does not match this CloudClient's binding.")
            self._http.write_control(BlynkPins.PUMP_STATUS_DETAIL, detail)

    def report_node_catalog(self, *, node_id: str, catalog: str) -> None:
        with self._lock:
            self._ensure_open()
            if node_id != self._node_id:
                raise ValueError("node_id does not match this CloudClient's binding.")
            self._http.write_control(BlynkPins.NODE_CATALOG, catalog)

    def poll_command(self) -> PumpCommand | None:
        """Consume the latest unexpired PumpCommand without waiting on network I/O.

        Returns None if no valid request is pending or the client is closed.
        Latest state wins: ON then OFF before polling returns only OFF. Check
        command.is_expired() again before execution; receipt is not an ack.
        """
        return self._mqtt.poll_command()

    def close(self) -> None:
        """Release resources after in-flight uploads and bounded backend calls."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._mqtt.close()
            finally:
                try:
                    if self._profiles is not None:
                        self._profiles.close()
                finally:
                    self._http.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise CloudError("Cloud client is closed.", category="closed")
