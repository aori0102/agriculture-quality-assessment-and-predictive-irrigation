"""Validated requests; these objects do not represent hardware execution."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import monotonic


@dataclass(frozen=True)
class PumpCommand:
    """Immutable user intent returned by CloudClient.poll_command().

    node_id identifies the bound node; requested_on is the desired boolean
    state, not confirmed execution. received_at and expires_at are UTC-aware
    datetimes. Use is_expired() for execution checks and leave _deadline private.
    Commands are process-local and must not be persisted for later replay.
    """
    node_id: str
    requested_on: bool
    received_at: datetime
    expires_at: datetime
    _deadline: float = field(repr=False)

    @classmethod
    def _received(cls, node_id: str, requested_on: bool, lifetime: float) -> "PumpCommand":
        now = datetime.now(timezone.utc)
        return cls(node_id, requested_on, now, now + timedelta(seconds=lifetime), monotonic() + lifetime)

    def is_expired(self) -> bool:
        """Check again immediately before execution, even after a successful poll."""
        return monotonic() >= self._deadline
