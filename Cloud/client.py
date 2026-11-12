"""The public interface consumed by the edge application."""

from threading import Lock

from .blynk_http import BlynkHTTP
from .blynk_mqtt import BlynkMQTT
from .commands import PumpCommand
from .config import CloudConfig
from .errors import CloudError


class CloudClient:
    """One Blynk device bound to one edge node; construction does not connect.

    Args:
        config: Validated Blynk credentials and transport settings.
        node_id: Nonempty edge node identifier, matched exactly on every upload.

    Raises:
        ValueError: If node_id is not a nonempty string.

    HTTP uploads work without start(). Call start() for background command
    reception and close() in a finally block. See Cloud/API.md for examples.
    """

    def __init__(self, config: CloudConfig, *, node_id: str):
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("node_id must be a nonempty string.")
        self._node_id = node_id
        self._mqtt = BlynkMQTT(config, node_id=node_id)
        self._http = BlynkHTTP(config)
        self._lock = Lock()
        self._closed = False

    @property
    def commands_ready(self) -> bool:
        """Whether MQTT is connected and its command subscription is accepted."""
        return self._mqtt.commands_ready

    @property
    def last_command_error(self) -> CloudError | None:
        """Latest MQTT failure, cleared on successful subscription; not HTTP status."""
        return self._mqtt.last_command_error

    def start(self) -> None:
        """Start background MQTT reception and return None without waiting.

        Check commands_ready before assuming the subscription is active.
        Repeated calls do nothing; terminal errors require a new client.
        Raises CloudError if closed or the network loop cannot start.
        """
        with self._lock:
            self._ensure_open()
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
            pump_is_on: Physically confirmed boolean pump state for V4.

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

    def report_pump_status(self, *, node_id: str, is_on: bool) -> None:
        """Upload confirmed hardware state to V4; return None.

        node_id must match the bound node. is_on must be bool, not None or an
        integer. Call after device confirmation, never just on command receipt.
        Raises ValueError for invalid inputs and CloudError for upload failures.
        """
        if type(is_on) is not bool:
            raise ValueError("is_on must be a boolean confirmed by the device.")
        self.send_snapshot(node_id=node_id, pump_is_on=is_on)

    def poll_command(self) -> PumpCommand | None:
        """Consume the latest unexpired PumpCommand without waiting on network I/O.

        Returns None if no valid request is pending or the client is closed.
        Latest state wins: ON then OFF before polling returns only OFF. Check
        command.is_expired() again before execution; receipt is not an ack.
        """
        return self._mqtt.poll_command()

    def close(self) -> None:
        """Release resources; any in-flight synchronous upload finishes first."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._mqtt.close()
            finally:
                self._http.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise CloudError("Cloud client is closed.", category="closed")
