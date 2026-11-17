"""Offline example and Edge integration skeleton; no connections on import.

MemoryBackend is NOT durable and must not be used as the actual Edge store.
"""

from contextlib import ExitStack, contextmanager
from dataclasses import replace
from threading import Lock

from cloud_edge import (
    ActiveProfile, CloudClient, ProfileApplyResult, ProfileBackend,
    PlantProfile, illustrative_preset, load_device_bindings,
)


class MemoryBackend:
    """Fake for demonstration only: restarting loses all profiles."""

    def __init__(self):
        self._lock = Lock()
        self._active: dict[str, ActiveProfile] = {}

    def get_active(self, node_id: str) -> ActiveProfile | None:
        with self._lock:
            return self._active.get(node_id)

    def apply_profile(self, profile: PlantProfile) -> ProfileApplyResult:
        with self._lock:
            old = self._active.get(profile.node_id)
            if old is not None and old.profile == profile:
                return ProfileApplyResult("applied", old, "Unchanged")
            active = ActiveProfile(
                profile, 1 if old is None else old.revision + 1,
                "Automatic irrigation blocked: VWC calibration required",
            )
            # Real Edge backend: atomically persist before changing active state.
            self._active[profile.node_id] = active
            return ProfileApplyResult("applied", active)


@contextmanager
def connected_profile_clients(backend: ProfileBackend):
    """LIVE skeleton: explicitly invoking this connects to Blynk.

    Supply the real Edge backend. Telemetry and pump authorization remain the
    caller's responsibility. All bindings are validated before connecting.
    """
    bindings = load_device_bindings()
    with ExitStack() as resources:
        clients = {}
        for binding in bindings:
            client = CloudClient(binding.config, node_id=binding.node_id, profile_backend=backend)
            resources.callback(client.close)
            client.start()
            clients[binding.node_id] = client
        yield clients


def main() -> int:
    backend = MemoryBackend()
    example = illustrative_preset("demo-node", "Offline example")
    result = backend.apply_profile(example)
    print(f"In-memory example only: revision={result.active.revision}, MADP={example.madp:.3f}")
    custom = replace(example, mode="custom", preset_key=None, field_capacity=0.25)
    result = backend.apply_profile(custom)
    print(f"Custom revision={result.active.revision}, MADP={custom.madp:.3f}")
    print(result.active.irrigation_note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
