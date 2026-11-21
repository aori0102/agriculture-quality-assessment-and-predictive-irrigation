from edge_logging import get_logger
logger = get_logger("CLOUD")

"""Bounded asynchronous uploads and validated device pump confirmations."""
from datetime import datetime, timezone
import json
from queue import Queue, Full
from threading import Thread, Lock

from time_helper import TimeHelper


class CloudReporter:
    def __init__(self):
        self.clients = {}
        self.queue = Queue(maxsize=256)
        self.worker = None
        self.last_pump_event = {}
        self.last_capture_event = {}
        self.lock = Lock()
        self.on_pump_event = None

    def configure(self, clients):
        if self.worker is not None:
            raise RuntimeError("CloudReporter is already configured.")
        # RuntimeDeviceRegistry exposes get(); keep dict callers compatible.
        self.clients = clients if hasattr(clients, "get") else dict(clients)
        self.worker = Thread(target=self._run, name="cloud-uploads", daemon=True)
        self.worker.start()

    def submit(self, method, *, node_id, **values):
        if self.clients.get(node_id) is None:
            return False
        try:
            self.queue.put_nowait((method, node_id, values))
            return True
        except Full:
            logger.warning("Cloud upload queue full; update dropped")
            return False

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
            except Exception as error:
                # Do not log transport exception text, which may contain credentials.
                logger.error(f"Cloud upload failed: {type(error).__name__}")
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
            if not isinstance(event, str) or event not in {*states, "capture_succeeded"}:
                return False
            sent = TimeHelper.process_time(data.get("sent_at"), require_timezone=True)
            until = TimeHelper.process_time(data.get("valid_until"), require_timezone=True)
            now = now or datetime.now(timezone.utc)
            if sent is None or until is None or not sent <= now < until:
                return False
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
