"""Synchronous uploads of current datastream values through Blynk HTTPS."""

import math
from threading import Lock

import requests

from .config import CloudConfig
from .datastreams import BlynkPins, PROFILE_FIELDS
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
                    raise ValueError("pump_is_on must be a device-confirmed boolean.")
                params[BlynkPins.PUMP_STATUS] = int(pump_is_on)
            if not params:
                return
            self._request("batch/update", params)

    def read_profile_snapshot(self) -> dict:
        """Fetch all editable profile streams in one HTTPS request."""
        with self._lock:
            params = dict.fromkeys((*PROFILE_FIELDS, BlynkPins.PROFILE_PRESET), "")
            values = self._request("get", params, read_json=True)
            # Blynk may return null for an empty string even when V8 is bound.
            # Empty node_description is explicitly valid in the profile contract.
            if values.get(BlynkPins.NODE_DESCRIPTION) is None:
                values[BlynkPins.NODE_DESCRIPTION] = ""
            return values

    def write_profile(self, values: dict) -> None:
        """Write only profile fields/status; Apply may only be reset to zero."""
        allowed = set(PROFILE_FIELDS) | {
            BlynkPins.APPLY_PROFILE, BlynkPins.PROFILE_STATUS, BlynkPins.PROFILE_PRESET,
        }
        if not isinstance(values, dict) or not values.keys() <= allowed:
            raise ValueError("Only profile datastream writes are permitted.")
        for pin, value in values.items():
            if pin in (BlynkPins.APPLY_PROFILE, BlynkPins.PROFILE_PRESET):
                choices = (0,) if pin == BlynkPins.APPLY_PROFILE else (0, 1)
                if type(value) is not int or value not in choices:
                    raise ValueError("Invalid profile control value.")
            elif pin in (BlynkPins.NODE_ID, BlynkPins.NODE_DESCRIPTION,
                         BlynkPins.PLANT_NAME, BlynkPins.PROFILE_STATUS):
                limit = 512 if pin == BlynkPins.PROFILE_STATUS else 128
                if not isinstance(value, str) or len(value) > limit or any(not c.isprintable() for c in value):
                    raise ValueError("Invalid profile text value.")
            elif type(value) not in (int, float) or not 0 <= value <= 1:
                raise ValueError("Profile thresholds must be finite numbers between 0 and 1.")
        with self._lock:
            self._request("batch/update", dict(values))

    def write_control(self, pin: str, value: int | str) -> None:
        if pin == BlynkPins.AUTO_IRRIGATION_ENABLED:
            if type(value) is not int or value not in (0, 1):
                raise ValueError("Invalid automatic irrigation value.")
        elif pin == BlynkPins.PUMP_STATUS_DETAIL:
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ValueError("Invalid pump status detail.")
        elif pin == BlynkPins.NODE_CATALOG:
            if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
                raise ValueError("Invalid node catalog.")
        else:
            raise ValueError("Invalid control datastream.")
        with self._lock:
            self._request("batch/update", {pin: value})

    def _request(self, path: str, params: dict, *, read_json: bool = False):
        # Caller holds the session lock. Never include URLs or response bodies
        # in errors: Blynk credentials travel in query parameters.
        if self._closed:
            raise CloudError("Cloud HTTP client is closed.", category="closed")
        params["token"] = self._config.auth_token
        try:
            with self._session.get(
                f"https://{self._config.server}/external/api/{path}",
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
                if read_json:
                    try:
                        data = response.json()
                    except ValueError:
                        raise CloudError("Invalid Blynk profile response.", category="response") from None
                    if not isinstance(data, dict) or "error" in data:
                        raise CloudError("Invalid Blynk profile response.", category="response")
                    return data
        except requests.Timeout:
            raise CloudError("Blynk HTTP request timed out.", category="timeout") from None
        except requests.RequestException:
            raise CloudError("Blynk HTTP connection failed.", category="network") from None

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._session.close()
