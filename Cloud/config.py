"""Explicit, side-effect-free loading of Blynk configuration."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import re

from dotenv import dotenv_values


ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


@dataclass(frozen=True)
class CloudConfig:
    """Immutable Blynk settings; construction raises ValueError on invalid input.

    server and auth_token are required. Timeout, command lifetime, and keepalive
    are seconds; MQTT uses verified TLS on port 8883. The token is hidden in
    repr. Use dataclasses.replace() to override settings loaded by from_env().
    """
    server: str
    auth_token: str = field(repr=False)
    http_timeout: float = 10.0
    command_lifetime: float = 5.0
    mqtt_port: int = 8883
    mqtt_keepalive: int = 45

    def __post_init__(self) -> None:
        # This package targets public Blynk regional hosts, not custom brokers.
        if not isinstance(self.server, str) or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.blynk\.cloud", self.server
        ):
            raise ValueError("BLYNK_SERVER must be a regional hostname such as sgp1.blynk.cloud.")
        if not isinstance(self.auth_token, str) or not self.auth_token or any(
            character.isspace() or not character.isprintable() for character in self.auth_token
        ):
            raise ValueError("BLYNK_AUTH_TOKEN must be a nonempty token without whitespace.")
        for name in ("http_timeout", "command_lifetime"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not 0 < value < math.inf:
                raise ValueError(f"{name} must be a positive finite number.")
        try:
            datetime.now(timezone.utc) + timedelta(seconds=self.command_lifetime)
        except OverflowError:
            raise ValueError("command_lifetime exceeds the supported datetime range.") from None
        if type(self.mqtt_port) is not int or self.mqtt_port != 8883:
            raise ValueError("mqtt_port must be 8883 for verified Blynk MQTT TLS.")
        if type(self.mqtt_keepalive) is not int or not 0 < self.mqtt_keepalive <= 65535:
            raise ValueError("mqtt_keepalive must be an integer between 1 and 65535.")

    @classmethod
    def from_env(cls) -> "CloudConfig":
        """Return config loaded from BLYNK_SERVER and BLYNK_AUTH_TOKEN.

        Process environment overrides the repository-root .env independently
        of cwd. Other settings keep defaults. Does not change os.environ.
        Raises ValueError for missing, empty, or invalid required settings.
        """
        values = {**dotenv_values(ENV_FILE, interpolate=False), **os.environ}
        return cls(
            server=(values.get("BLYNK_SERVER") or "").strip(),
            auth_token=(values.get("BLYNK_AUTH_TOKEN") or "").strip(),
        )
