"""Edge-owned pump policy and command acknowledgement state."""

from configparser import ConfigParser
from pathlib import Path
from queue import Empty, Queue
from threading import RLock
from time import monotonic
from uuid import uuid4

from action_index import ActionIndex
from command_proxy import command_proxy
from operation_log_manager import operation_log_manager
from cloud_reporter import cloud_reporter
from node_fallback import control_ready


class PumpCoordinator:
    def __init__(self):
        cfg = ConfigParser()
        cfg.read(Path(__file__).with_name("config.cfg"), encoding="utf-8-sig")
        self.manual_duration_ms = cfg.getint("pump", "manual_duration_ms", fallback=30000)
        self.max_duration_ms = cfg.getint("pump", "max_duration_ms", fallback=60000)
        self.ttl_ms = cfg.getint("pump", "command_ttl_ms", fallback=10000)
        self.ack_timeout = cfg.getint("pump", "start_ack_timeout_ms", fallback=2000) / 1000
        if not 0 < self.manual_duration_ms <= self.max_duration_ms <= 60000:
            raise ValueError("Pump durations must fit the firmware's 60000 ms limit")
        self.lock = RLock()
        self.events = Queue()
        self.pending = {}
        self.confirmed = {}
        self.last_sequence = {}
        self.last_event_at = {}
        self.restore_auto = set()
        self.timeline_state = {}

    def auto_enabled(self, node_id):
        record = operation_log_manager.load_log()["node_profile"].get(node_id)
        if not isinstance(record, dict):
            return False
        value = record.get("auto_irrigation_enabled", record.get("irrigation_status", False))
        return value is True

    def _mode(self, node_id, enabled):
        if self.auto_enabled(node_id) is enabled:
            return
        operation_log_manager.log_irrigation_status(node_id, enabled)
        cloud_reporter.submit("report_control_mode", node_id=node_id, enabled=enabled)

    def _detail(self, node_id, value):
        cloud_reporter.submit("report_pump_detail", node_id=node_id, detail=value)

    def _send(self, node_id, action, duration_ms=0):
        if action is ActionIndex.StartPump and not control_ready(node_id):
            self._detail(node_id, "unknown: awaiting fallback configuration and audit reconciliation")
            return False
        command_id = uuid4().hex
        if not command_proxy.send_command(node_id, action, ttl_ms=self.ttl_ms,
                                          duration_ms=duration_ms, command_id=command_id):
            self._detail(node_id, "unknown: local MQTT command was not queued")
            return False
        self.pending[node_id] = (command_id, action.value, monotonic() + self.ack_timeout)
        self._detail(node_id, "pending: " + action.value)
        return True

    def request_manual(self, node_id, requested_on):
        if type(requested_on) is not bool:
            return False
        with self.lock:
            if node_id not in operation_log_manager.load_log()["node_profile"]:
                return False
            self._mode(node_id, False)
            if requested_on:
                if node_id in self.pending:
                    self._detail(node_id, "pending: previous command awaits device state")
                    return False
                if self.confirmed.get(node_id) is True:
                    self._detail(node_id, "confirmed: already running; request did not extend run")
                    return True
                if self.confirmed.get(node_id) is not False:
                    self._detail(node_id, "unknown: waiting for a fresh device state")
                    return False
                return self._send(node_id, ActionIndex.StartPump, self.manual_duration_ms)
            return self._send(node_id, ActionIndex.StopPump)

    def request_auto(self, node_id, enabled):
        if type(enabled) is not bool:
            return False
        with self.lock:
            if node_id not in operation_log_manager.load_log()["node_profile"]:
                return False
            if enabled:
                if self.auto_enabled(node_id):
                    return True
                if self.confirmed.get(node_id) is True or (
                    node_id in self.pending and self.pending[node_id][1] == ActionIndex.StartPump.value
                ):
                    self.restore_auto.add(node_id)
                    return self._send(node_id, ActionIndex.StopPump)
                self._mode(node_id, True)
                if self.confirmed.get(node_id) is not False:
                    self._detail(node_id, "unknown: automatic mode awaits device state")
                return True
            self.restore_auto.discard(node_id)
            self._mode(node_id, False)
            return self._send(node_id, ActionIndex.StopPump)

    def request_automatic_start(self, node_id, duration_ms):
        with self.lock:
            if (not self.auto_enabled(node_id) or self.confirmed.get(node_id) is not False
                    or node_id in self.pending):
                return False
            if not 0 < duration_ms <= self.max_duration_ms:
                return False
            return self._send(node_id, ActionIndex.StartPump, duration_ms)

    def enqueue_event(self, node_id, data):
        self.events.put_nowait((node_id, data))

    def disconnected(self):
        with self.lock:
            for node_id in set(self.confirmed) | set(self.pending):
                self._detail(node_id, "unknown: local MQTT disconnected")
            self.confirmed.clear()
            self.pending.clear()
            self.last_event_at.clear()
            self.restore_auto.clear()

    def tick(self):
        while True:
            try:
                node_id, data = self.events.get_nowait()
            except Empty:
                break
            try:
                self._accept_event(node_id, data)
            finally:
                self.events.task_done()
        with self.lock:
            for node_id, (_, _, deadline) in tuple(self.pending.items()):
                if monotonic() >= deadline:
                    self.pending.pop(node_id)
                    self.confirmed.pop(node_id, None)
                    self.restore_auto.discard(node_id)
                    self._detail(node_id, "unknown: device acknowledgement timed out")
                    cloud_reporter.emit_event(node_id=node_id, code="pump_ack_timeout",
                                              description=f"Node {node_id}: pump command acknowledgement timed out; state unknown.")
            for node_id, observed_at in tuple(self.last_event_at.items()):
                if monotonic() - observed_at >= 15:
                    self.last_event_at.pop(node_id)
                    self.confirmed.pop(node_id, None)
                    self._detail(node_id, "unknown: device state is stale")

    def _accept_event(self, node_id, data):
        with self.lock:
            if node_id not in operation_log_manager.load_log()["node_profile"]:
                return
            boot, sequence = data.get("boot_id"), data.get("sequence")
            if not isinstance(boot, str) or not boot or type(sequence) is not int or sequence < 1:
                return
            previous = self.last_sequence.get(node_id)
            if previous is not None and previous[0] == boot and sequence <= previous[1]:
                return
            self.last_sequence[node_id] = (boot, sequence)
            state = data["event"] == "pump_started"
            pending = self.pending.get(node_id)
            command_id = data.get("command_id")
            if pending is not None and command_id == pending[0]:
                expected = pending[1] == ActionIndex.StartPump.value
                if state != expected:
                    return
                self.pending.pop(node_id)
            elif pending is not None and state:
                return
            self.last_event_at[node_id] = monotonic()
            self.confirmed[node_id] = state
            previous_state = self.timeline_state.get(node_id)
            # Firmware repeats state every five seconds; only log transitions.
            # Initial OFF is a baseline, initial ON is useful ongoing activity.
            if state != previous_state and (previous_state is not None or state):
                cloud_reporter.emit_event(node_id=node_id, code=data["event"],
                                          description=f"Node {node_id}: pump confirmed {'ON' if state else 'OFF'}.")
            self.timeline_state[node_id] = state
            cloud_reporter.submit("report_pump_status", node_id=node_id, is_on=state)
            self._detail(node_id, "confirmed: " + ("on" if state else "off"))
            if not state and node_id in self.restore_auto:
                self.restore_auto.remove(node_id)
                self._mode(node_id, True)


pump_coordinator = PumpCoordinator()
