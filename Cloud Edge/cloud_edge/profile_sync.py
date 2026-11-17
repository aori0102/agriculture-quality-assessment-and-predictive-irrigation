"""Bounded, per-device profile synchronization outside MQTT callbacks."""

from collections import deque
from threading import Condition, Thread
from time import monotonic

from .blynk_http import BlynkHTTP
from .datastreams import BlynkPins, BlynkTopics, PROFILE_FIELDS
from .errors import CloudError
from .profiles import ActiveProfile, ProfileApplyResult, ProfileBackend, illustrative_preset, validate_snapshot, profiles_match


class ProfileSync:
    """One serial worker. Edge owns durable state; only status retries are cached.

    Reconnect replaces all queued events with restoration of Edge's active
    profile. An already-entered backend transaction cannot be cancelled; its
    confirmed state is reconciled on reconnect. Backend calls must be bounded.
    """

    EVENT_LIFETIME = 30.0
    MAX_EVENTS = 16

    def __init__(self, http: BlynkHTTP, backend: ProfileBackend, *, node_id: str,
                 retry_interval: float = 5.0):
        self._http, self._backend, self._node_id = http, backend, node_id
        self._condition = Condition()
        self._thread: Thread | None = None
        self._closed = False
        self._connected = False
        self._restored = False
        self._epoch = 0
        self._events = deque()
        self._apply_pending = False
        self._selector: int | None = None
        self._pending_status: dict | None = None
        self._retry_at = 0.0
        self._retry_interval = retry_interval
        self._last_status = ""
        self._last_error: CloudError | None = None

    @property
    def ready(self) -> bool:
        with self._condition:
            return self._connected and self._restored and not self._closed

    @property
    def last_status(self) -> str:
        with self._condition:
            return self._last_status

    @property
    def last_error(self) -> CloudError | None:
        with self._condition:
            return self._last_error

    def start(self) -> None:
        with self._condition:
            if self._closed:
                raise CloudError("Profile synchronization is closed.", category="closed")
            if self._thread is None:
                self._thread = Thread(target=self._run, name="cloud-profile-sync", daemon=True)
                self._thread.start()

    def connected(self) -> None:
        self._connection_changed(True)

    def disconnected(self) -> None:
        self._connection_changed(False)

    def _connection_changed(self, connected: bool) -> None:
        with self._condition:
            if self._closed:
                return
            self._epoch += 1
            self._connected = connected
            self._restored = False
            self._events.clear()
            self._apply_pending = False
            self._selector = None
            self._pending_status = None
            self._retry_at = 0
            self._condition.notify_all()

    def receive(self, topic: str, value: str) -> None:
        """Nonblocking receipt; retained/invalid MQTT messages are filtered upstream."""
        if value not in ("0", "1") or topic not in (BlynkTopics.APPLY_PROFILE, BlynkTopics.PROFILE_PRESET):
            return
        is_apply = topic == BlynkTopics.APPLY_PROFILE
        if is_apply and value == "0":
            return  # A momentary button release is not cancellation.
        with self._condition:
            if not self.ready or (is_apply and self._apply_pending):
                return
            if not is_apply and int(value) == self._selector:
                return  # Includes MQTT echoes of restored state and duplicates.
            if len(self._events) >= self.MAX_EVENTS:
                self._last_error = CloudError("Profile queue full; wait and retry.", category="busy")
                return
            self._events.append(("apply" if is_apply else "preset", int(value),
                                 self._epoch, monotonic() + self.EVENT_LIFETIME))
            if is_apply:
                self._apply_pending = True
            else:
                self._selector = int(value)
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._connected = False
            self._epoch += 1
            self._events.clear()
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join()  # No locks held; backend methods must return promptly.

    def _live(self, epoch: int) -> bool:
        with self._condition:
            return not self._closed and self._connected and epoch == self._epoch

    def _run(self) -> None:
        while True:
            with self._condition:
                while True:
                    if self._closed:
                        return
                    delay = self._retry_at - monotonic()
                    if self._connected:
                        if not self._restored and delay <= 0:
                            task = ("restore", 0, self._epoch, float("inf"))
                            self._pending_status = None
                            break
                        if self._restored and self._pending_status is not None and delay <= 0:
                            task = ("status", self._pending_status, self._epoch, float("inf"))
                            break
                        if self._restored and self._events:
                            task = self._events.popleft()
                            break
                    waiting_retry = self._connected and (not self._restored or self._pending_status is not None)
                    self._condition.wait(timeout=max(delay, 0.001) if waiting_retry else None)
            kind, value, epoch, deadline = task
            try:
                if not self._live(epoch):
                    continue
                if monotonic() >= deadline:
                    if kind == "preset":
                        self._forget_selector(epoch, value)
                    self._publish(epoch, "SYNC_FAILED: Apply/preview expired; retry")
                elif kind == "restore":
                    self._restore(epoch)
                elif kind == "status":
                    self._publish(epoch, value[BlynkPins.PROFILE_STATUS])
                elif kind == "preset":
                    self._preview(epoch, value, deadline)
                else:
                    self._apply(epoch, deadline)
            except ValueError as error:
                # Only Cloud's value validators raise these; backend exceptions
                # are sanitized separately below, never displayed verbatim.
                if kind == "preset":
                    self._forget_selector(epoch, value)
                self._publish(epoch, f"REJECTED: node={self._node_id}; {error}")
            except CloudError as error:
                if kind == "preset":
                    self._forget_selector(epoch, value)
                with self._condition:
                    if self._live(epoch):
                        self._last_error = error
                self._publish(epoch, "SYNC_FAILED: " + str(error))
            finally:
                with self._condition:
                    if epoch == self._epoch:
                        if kind == "apply":
                            self._apply_pending = False
                        if kind == "restore" and not self._restored:
                            self._retry_at = monotonic() + self._retry_interval

    def _backend_call(self, method, *args):
        try:
            return method(*args)
        except Exception:
            raise CloudError("Edge profile operation failed; check Edge locally.", category="backend") from None

    def _check_active(self, active) -> None:
        if active is not None and (not isinstance(active, ActiveProfile) or active.profile.node_id != self._node_id):
            raise CloudError("Edge returned an invalid active profile.", category="backend")

    def _describe_active(self, active: ActiveProfile | None) -> str:
        if active is None:
            return f"node={self._node_id}; no active profile"
        mode = "Preset (illustrative, unverified)" if active.profile.mode == "preset" else "Custom"
        revision = f"; rev={active.revision}" if active.revision is not None else ""
        metadata = "; metadata incomplete; complete form and Apply" if not active.metadata_complete else ""
        return f"node={self._node_id}{revision}; {mode}; {active.irrigation_note}{metadata}"

    def _restore(self, epoch: int) -> None:
        active = self._backend_call(self._backend.get_active, self._node_id)
        self._check_active(active)
        if active is None:
            # Zeroes are explicitly UNCONFIGURED placeholders, never a profile.
            values = dict.fromkeys(PROFILE_FIELDS, 0)
            values.update({BlynkPins.NODE_ID: self._node_id,
                           BlynkPins.NODE_DESCRIPTION: "", BlynkPins.PLANT_NAME: "",
                           BlynkPins.PROFILE_PRESET: 0})
            status = "UNCONFIGURED: " + self._describe_active(None)
        else:
            values = active.profile.to_datastreams()
            status = ("SAVED: " if active.saved_only else "ACTIVE: ") + self._describe_active(active)
        status += "; unsaved drafts discarded"
        with self._condition:
            if self._live(epoch):
                self._selector = values[BlynkPins.PROFILE_PRESET]
        if self._publish(epoch, status, values):
            with self._condition:
                if self._live(epoch):
                    self._restored = True

    def _forget_selector(self, epoch: int, selector: int) -> None:
        with self._condition:
            if self._live(epoch) and self._selector == selector:
                self._selector = None

    def _preview(self, epoch: int, selector: int, deadline: float) -> None:
        if selector == 0:
            self._publish(epoch, "DRAFT: Custom; edit fields, then Apply")
            return
        snapshot = self._http.read_profile_snapshot()
        if not self._live(epoch):
            return
        if monotonic() >= deadline:
            raise CloudError("Preview expired during retrieval; retry.", category="timeout")
        if snapshot.get(BlynkPins.NODE_ID) != self._node_id:
            raise ValueError("node_id does not match this device's binding.")
        current_selector = snapshot.get(BlynkPins.PROFILE_PRESET)
        if type(current_selector) is not int or current_selector != selector:
            raise ValueError("Preset changed during preview; select again.")
        profile = illustrative_preset(self._node_id, snapshot.get(BlynkPins.NODE_DESCRIPTION))
        values = profile.to_datastreams()
        # Do not echo the selector or overwrite identity/description.
        for pin in (BlynkPins.NODE_ID, BlynkPins.NODE_DESCRIPTION, BlynkPins.PROFILE_PRESET):
            values.pop(pin)
        if not self._publish(epoch, "DRAFT: Illustrative Lettuce; agronomically unverified; press Apply", values):
            self._forget_selector(epoch, selector)

    def _apply(self, epoch: int, deadline: float) -> None:
        snapshot = self._http.read_profile_snapshot()
        profile = validate_snapshot(snapshot, node_id=self._node_id)
        if not self._live(epoch):
            return
        if monotonic() >= deadline:
            raise CloudError("Apply expired during retrieval; retry.", category="timeout")
        result = self._backend_call(self._backend.apply_profile, profile)
        if not isinstance(result, ProfileApplyResult):
            raise CloudError("Edge returned an invalid application result.", category="backend")
        self._check_active(result.active)
        if result.outcome in ("applied", "saved") and not profiles_match(result.active.profile, profile):
            raise CloudError("Edge confirmed a different profile; inspect active state.", category="backend")
        status = result.outcome.upper() + ": " + self._describe_active(result.active)
        if result.message:
            status += "; " + result.message
        self._publish(epoch, status)

    def _publish(self, epoch: int, status: str, fields: dict | None = None) -> bool:
        if not self._live(epoch):
            return False
        status = status[:512]
        values = {**(fields or {}), BlynkPins.PROFILE_STATUS: status, BlynkPins.APPLY_PROFILE: 0}
        with self._condition:
            if not self._live(epoch):
                return False
            self._last_status = status
        try:
            self._http.write_profile(values)
        except CloudError as error:
            with self._condition:
                if self._live(epoch):
                    self._last_error = error
                    # Never replay a draft preview or an application on retry.
                    if fields is not None and self._restored:
                        self._last_status = "SYNC_FAILED: Draft preview failed; select the preset again"
                    self._pending_status = {BlynkPins.PROFILE_STATUS: self._last_status}
                    self._retry_at = monotonic() + self._retry_interval
            return False
        with self._condition:
            if self._live(epoch):
                self._last_error = None
                self._pending_status = None
        return True
