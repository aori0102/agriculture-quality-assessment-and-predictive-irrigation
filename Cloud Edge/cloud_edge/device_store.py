"""Durable, private node-to-Blynk bindings."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat
import tempfile

from .config import CloudConfig


@dataclass(frozen=True)
class StoredDevice:
    node_id: str
    stable_name: str
    device_id: int
    server: str
    token: str = field(repr=False)
    template_id: str = "TMPL6FGqaSpR3"

    def __post_init__(self):
        if (not self.node_id or not self.stable_name or type(self.device_id) is not int
                or self.device_id <= 0 or self.template_id != "TMPL6FGqaSpR3"):
            raise ValueError("Invalid device binding identity.")
        CloudConfig(self.server, self.token)


class DeviceStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, StoredDevice]:
        if self.path.is_symlink():
            raise PermissionError("Device store must not be a symlink.")
        if not self.path.exists():
            return {}
        mode = self.path.lstat().st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise PermissionError("Device store must be a private regular file (0600).")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("devices"), dict):
            raise ValueError("Invalid device store schema.")
        devices = {node: StoredDevice(**value) for node, value in data["devices"].items()}
        if any(node != device.node_id for node, device in devices.items()):
            raise ValueError("Device store node identity mismatch.")
        if len({device.device_id for device in devices.values()}) != len(devices):
            raise ValueError("Duplicate Blynk device ID in store.")
        if len({device.token for device in devices.values()}) != len(devices):
            raise ValueError("Duplicate Blynk token in store.")
        return devices

    def save(self, devices: dict[str, StoredDevice]) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.path.parent.stat().st_mode & 0o077:
            raise PermissionError("Device store directory must be private (0700).")
        # Refuse to replace an unsafe file, including a symlink.
        self.load()
        payload = {"schema_version": 1, "devices": {node: vars(device) for node, device in devices.items()}}
        fd, name = tempfile.mkstemp(prefix=".devices-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)


class CreateJournal:
    """Durable create intent; uncertain outcomes require operator reconciliation."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> set[str]:
        if self.path.is_symlink():
            raise PermissionError("Create journal must not be a symlink.")
        if not self.path.exists():
            return set()
        if not stat.S_ISREG(self.path.lstat().st_mode) or self.path.lstat().st_mode & 0o077:
            raise PermissionError("Create journal must be private (0600).")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or any(not isinstance(node, str) for node in data):
            raise ValueError("Invalid create journal.")
        return set(data)

    def save(self, nodes: set[str]) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.path.parent.stat().st_mode & 0o077:
            raise PermissionError("Create journal directory must be private (0700).")
        self.load()
        fd, name = tempfile.mkstemp(prefix=".create-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(sorted(nodes), stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)
