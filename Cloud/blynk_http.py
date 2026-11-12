"""Synchronous uploads of current datastream values through Blynk HTTPS."""

import math
from threading import Lock

import requests

from .config import CloudConfig
from .datastreams import BlynkPins
from .errors import CloudError


def _number(name: str, value: int | float, *, percentage: bool = False) -> int | float:
    if type(value) not in (int, float) or not -math.inf < value < math.inf:
        raise ValueError(f"{name} must be a finite number, not a boolean.")
    if percentage:
        if not 0 <= value <= 100:
            raise ValueError(f"{name} must be between 0 and 100.")
        whole = math.floor(value)
        return whole + int(value - whole >= 0.5)
    try:
        return float(value)
    except OverflowError:
        raise ValueError(f"{name} must fit in a finite float.") from None


class BlynkHTTP:
    def __init__(self, config: CloudConfig):
        self._config = config
        self._session = requests.Session()
        self._lock = Lock()
        self._closed = False

    def send_snapshot(
        self, *, soil_index_pct: int | float | None = None,
        temperature_c: int | float | None = None,
        air_humidity_pct: int | float | None = None,
        plant_status: str | None = None,
        predicted_moisture_pct: int | float | None = None,
        pump_is_on: bool | None = None,
    ) -> None:
        """Send one batch; omit unknown readings rather than inventing values."""
        with self._lock:
            if self._closed:
                raise CloudError("Cloud HTTP client is closed.", category="closed")
            params = {}
            for name, pin, value, percentage in (
                ("soil_index_pct", BlynkPins.SOIL_MOISTURE, soil_index_pct, True),
                ("temperature_c", BlynkPins.TEMPERATURE, temperature_c, False),
                ("air_humidity_pct", BlynkPins.AIR_HUMIDITY, air_humidity_pct, True),
                ("predicted_moisture_pct", BlynkPins.PREDICTED_MOISTURE, predicted_moisture_pct, True),
            ):
                if value is not None:
                    params[pin] = _number(name, value, percentage=percentage)
            if plant_status is not None:
                if not isinstance(plant_status, str):
                    raise ValueError("plant_status must be a string.")
                params[BlynkPins.PLANT_STATUS] = plant_status
            if pump_is_on is not None:
                if type(pump_is_on) is not bool:
                    raise ValueError("pump_is_on must be a boolean confirmed by the device.")
                params[BlynkPins.PUMP_STATUS] = int(pump_is_on)
            if not params:
                return
            params["token"] = self._config.auth_token
            try:
                with self._session.get(
                    f"https://{self._config.server}/external/api/batch/update",
                    params=params, timeout=self._config.http_timeout,
                    verify=True, allow_redirects=False,
                ) as response:
                    status = response.status_code
                    if 300 <= status < 400:
                        raise CloudError(
                            "Blynk redirected the request; set BLYNK_SERVER to the device's regional host.",
                            category="redirect", status_code=status,
                        )
                    if not 200 <= status < 300:
                        raise CloudError(
                            f"Blynk returned HTTP {status}; check credentials, datastreams, and quota.",
                            category="http", status_code=status,
                        )
            except requests.Timeout:
                raise CloudError("Blynk HTTP request timed out.", category="timeout") from None
            except requests.RequestException:
                raise CloudError("Blynk HTTP connection failed.", category="network") from None

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._session.close()
