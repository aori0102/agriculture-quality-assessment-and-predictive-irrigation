from edge_logging import get_logger
logger = get_logger("CLOUD")

"""Bounded asynchronous uploads and validated device pump confirmations."""
from datetime import datetime, timezone
import json
from queue import Queue, Full
from threading import Thread, Lock

from time_helper import TimeHelper
from time import monotonic
from collections import deque


class CloudReporter:
    def __init__(self):
        self.clients = {}
        self.queue = Queue(maxsize=256)
        self.worker = None
        self.last_pump_event = {}
        self.last_capture_event = {}
        self.lock = Lock()
        self.on_pump_event = None
        self.event_times = {}
        self.event_budget = {}
        self.soil_alert = {}
        self.unbound_nodes = set()

    def configure(self, clients):
        if self.worker is not None:
            raise RuntimeError("CloudReporter is already configured.")
        # RuntimeDeviceRegistry exposes get(); keep dict callers compatible.
        self.clients = clients if hasattr(clients, "get") else dict(clients)
        self.worker = Thread(target=self._run, name="cloud-uploads", daemon=True)
        self.worker.start()

    def submit(self, method, *, node_id, **values):
        if self.clients.get(node_id) is None:
            if node_id not in self.unbound_nodes:
                logger.warning("UPLOAD SKIPPED node=%s | no Blynk binding; run Cloud Edge/run_edge.py and check provisioning", node_id)
                self.unbound_nodes.add(node_id)
            return False
        self.unbound_nodes.discard(node_id)
        try:
            self.queue.put_nowait((method, node_id, values))
            return True
        except Full:
            logger.warning("Cloud upload queue full; update dropped")
            return False

    def emit_event(self, *, node_id, code, description):
        # Shared budget across all event types. Reserve before queueing, never
        # retry ambiguous HTTP outcomes (which may already have reached Blynk).
        now = monotonic()
        with self.lock:
            history = self.event_budget.setdefault(node_id, deque())
            while history and now - history[0] >= 86400:
                history.popleft()
            key = (node_id, code)
            if len(history) >= 90 or now - self.event_times.get(key, -float('inf')) < 60:
                return False
            if not self.submit("log_event", node_id=node_id, code=code,
                               description=description[:300]):
                return False
            history.append(now)
            self.event_times[key] = now
            return True

    def observe_soil(self, *, node_id, soil_pct, pwp_pct, madp_pct):
        """One warning per dry episode, escalating once below PWP; rearm at MADP."""
        if not 0 <= pwp_pct < madp_pct <= 100 or not 0 <= soil_pct <= 100:
            return
        level = self.soil_alert.get(node_id, 0)
        if soil_pct >= madp_pct:
            self.soil_alert[node_id] = 0
            return
        target = 2 if soil_pct <= pwp_pct else 1
        if target > level:
            code = "soil_moisture_critical" if target == 2 else "soil_moisture_low"
            if self.emit_event(node_id=node_id, code=code, description=(
                    f"Node {node_id}: soil {soil_pct:.1f}%, PWP {pwp_pct:.1f}%, MADP {madp_pct:.1f}%")):
                self.soil_alert[node_id] = target

    def _run(self):
        while True:
            item = self.queue.get()
            try:
                if item is None:
                    return
                method, node_id, values = item
                client = self.clients.get(node_id)
                if client is not None:
                    getattr(client, method)(node_id=node_id, **values)
                    logger.info("UPLOAD node=%s method=%s | Blynk accepted", node_id, method)
            except Exception as error:
                # Do not log transport exception text, which may contain credentials.
                logger.error("UPLOAD FAILED node=%s method=%s type=%s category=%s status=%s",
                             node_id, method, type(error).__name__,
                             getattr(error, "category", "unknown"), getattr(error, "status_code", None))
            finally:
                self.queue.task_done()

    def close(self):
        if self.worker is not None:
            self.queue.put(None)
            self.worker.join()
            self.worker = None
        self.clients = {}

    def receive_pump_event(self, message, node_id, *, now=None):
        if message.retain or not node_id:
            return False
        try:
            data = json.loads(message.payload)
            if not isinstance(data, dict) or data.get("node_id") != node_id:
                return False
            states = {"pump_started": True, "pump_stopped": False}
            event = data.get("event")
            if not isinstance(event, str) or event not in {*states, "capture_succeeded", "capture_failed", "pump_start_rejected"}:
                return False
            sent = TimeHelper.process_time(data.get("sent_at"), require_timezone=True)
            until = TimeHelper.process_time(data.get("valid_until"), require_timezone=True)
            now = now or datetime.now(timezone.utc)
            if sent is None or until is None or not sent <= now < until:
                return False
            if event == "capture_failed":
                sequence, reason, code = data.get("sample_seq"), data.get("reason"), data.get("error_code")
                if (type(sequence) is not int or not 0 <= sequence <= 0xffffffff
                        or not isinstance(reason, str) or not 1 <= len(reason) <= 96 or not reason.isprintable()
                        or type(code) is not int or not 0 <= code <= 0xffffffff):
                    return False
                with self.lock:
                    previous = self.last_capture_event.get(node_id)
                    if previous is not None and sent <= previous["sent_at"]:
                        return False
                    self.last_capture_event[node_id] = {"sent_at": sent, "sample_seq": sequence, "reason": reason}
                logger.warning("CAPTURE FAILED node=%s seq=%s code=%s reason=%s", node_id, sequence, code, reason)
                return True
            if event == "capture_succeeded":
                sequence = data.get("sample_seq")
                if type(sequence) is not int or not 0 <= sequence <= 0xffffffff:
                    return False
                with self.lock:
                    previous = self.last_capture_event.get(node_id)
                    if previous is not None and sent <= previous["sent_at"]:
                        return False
                    self.last_capture_event[node_id] = {"sent_at": sent, "sample_seq": sequence}
                logger.info("CAPTURE node=%s seq=%s | node confirmed capture/upload queued", node_id, sequence)
                return True
            if self.on_pump_event is not None:
                self.on_pump_event(node_id, data)
                return True
            if event == "pump_start_rejected":
                return False  # Needs coordinator correlation; never publish as OFF.
            with self.lock:
                previous = self.last_pump_event.get(node_id)
                if previous is not None and sent <= previous:
                    return False
                if not self.submit("report_pump_status", node_id=node_id, is_on=states[event]):
                    return False
                self.last_pump_event[node_id] = sent
            logger.info("PUMP node=%s state=%s | node event accepted", node_id, "ON" if states[event] else "OFF")
            return True
        except (ValueError, TypeError, UnicodeDecodeError):
            return False


cloud_reporter = CloudReporter()
