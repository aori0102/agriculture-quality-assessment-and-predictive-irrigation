"""Private, atomic OAuth state for the standalone MCP experiment."""

import json
import os
from pathlib import Path
import stat
import tempfile
import time


class FileTokenStorage:
    def __init__(self, path: Path, token_type, client_info_type):
        self.path = path
        self.token_type = token_type
        self.client_info_type = client_info_type

    def _read(self):
        if not self.path.exists():
            return {}
        mode = self.path.lstat().st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise PermissionError("OAuth store must be a private regular file (0600).")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) - {"tokens", "client_info", "stored_at"}:
            raise ValueError("Invalid OAuth store structure.")
        return data

    def _write(self, data):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.stat().st_mode & 0o077:
            raise PermissionError("OAuth store directory must be private (0700).")
        fd, name = tempfile.mkstemp(prefix=".oauth-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(data, stream)
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

    async def get_tokens(self):
        value = self._read().get("tokens")
        return self.token_type.model_validate(value) if value is not None else None

    async def set_tokens(self, tokens):
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json")
        data["stored_at"] = time.time()
        self._write(data)

    def seconds_until_expiry(self):
        data = self._read()
        tokens = data.get("tokens", {})
        lifetime = tokens.get("expires_in")
        stored_at = data.get("stored_at")
        if not isinstance(lifetime, (int, float)) or not isinstance(stored_at, (int, float)):
            return None
        return max(0, stored_at + lifetime - time.time())

    async def get_client_info(self):
        value = self._read().get("client_info")
        return self.client_info_type.model_validate(value) if value is not None else None

    async def set_client_info(self, client_info):
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json")
        self._write(data)
