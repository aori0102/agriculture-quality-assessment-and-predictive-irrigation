"""Explicit, side-effect-free loading of Blynk configuration."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
import json
import os
from pathlib import Path
import re
from collections.abc import Mapping

from dotenv import dotenv_values


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


ENV_PLACEHOLDER = '# Fill in these values, then restart: python -m run_edge\n# node_id must match the ESP32 MQTT node_id.\n# server must be the Blynk device regional hostname.\n# auth_token_env names the variable containing that device\'s Auth Token.\nBLYNK_DEVICES_JSON=\'[{"node_id":"esp01","server":"sgp1.blynk.cloud","auth_token_env":"BLYNK_ESP01_TOKEN"}]\'\nBLYNK_ESP01_TOKEN=\n\n# Add one entry and a separate token variable for each additional Blynk device.\n# Keep this file private; do not commit real tokens.\n'


def ensure_env_file() -> bool:
    """Create the startup template exclusively; never overwrite an existing file."""
    try:
        with ENV_FILE.open("x", encoding="utf-8") as stream:
            stream.write(ENV_PLACEHOLDER)
    except FileExistsError:
        return False
    return True


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


@dataclass(frozen=True)
class DeviceBinding:
    node_id: str
    config: CloudConfig


def load_device_bindings(values: Mapping[str, str] | None = None) -> tuple[DeviceBinding, ...]:
    """Load BLYNK_DEVICES_JSON and referenced tokens, without connecting.

    Each entry has node_id, server, auth_token_env. Explicit values are useful
    for embedding/tests; otherwise process environment overrides root .env.
    Reject the entire configuration before callers construct any clients.
    """
    if values is None:
        values = {**dotenv_values(ENV_FILE, interpolate=False), **os.environ}
    try:
        entries = json.loads(values.get("BLYNK_DEVICES_JSON", ""))
    except (TypeError, ValueError):
        raise ValueError("BLYNK_DEVICES_JSON must be a nonempty JSON array.") from None
    if not isinstance(entries, list) or not entries:
        raise ValueError("BLYNK_DEVICES_JSON must be a nonempty JSON array.")
    bindings, nodes, tokens = [], set(), set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"node_id", "server", "auth_token_env"}:
            raise ValueError("Each device binding requires node_id, server, auth_token_env only.")
        node_id, token_env = entry["node_id"], entry["auth_token_env"]
        if not isinstance(node_id, str) or not node_id.strip() or len(node_id) > 128 or not node_id.isprintable():
            raise ValueError("Binding node_id must be nonempty printable text of at most 128 characters.")
        if not isinstance(token_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env):
            raise ValueError("auth_token_env must name an environment variable.")
        config = CloudConfig(server=entry["server"], auth_token=values.get(token_env, ""))
        if node_id in nodes or config.auth_token in tokens:
            raise ValueError("Device bindings must have unique node IDs and unique tokens.")
        nodes.add(node_id)
        tokens.add(config.auth_token)
        bindings.append(DeviceBinding(node_id, config))
    return tuple(bindings)
