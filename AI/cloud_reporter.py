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
        self.lock = Lock()

    def configure(self, clients):
        self.clients = dict(clients)
        self.worker = Thread(target=self._run, name="cloud-uploads", daemon=True)
        self.worker.start()

    def submit(self, method, *, node_id, **values):
        if node_id not in self.clients:
            return False
        try:
            self.queue.put_nowait((method, node_id, values))
            return True
        except Full:
            print("Cloud upload queue full; update dropped")
            return False

    def _run(self):
        while True:
            item = self.queue.get()
            try:
                if item is None:
                    return
                method, node_id, values = item
                getattr(self.clients[node_id], method)(node_id=node_id, **values)
            except Exception as error:
                # Do not log transport exception text, which may contain credentials.
                print(f"Cloud upload failed: {type(error).__name__}")
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
            if not isinstance(event, str) or event not in states:
                return False
            sent = TimeHelper.process_time(data.get("sent_at"), require_timezone=True)
            until = TimeHelper.process_time(data.get("valid_until"), require_timezone=True)
            now = now or datetime.now(timezone.utc)
            if sent is None or until is None or not sent <= now < until:
                return False
            with self.lock:
                previous = self.last_pump_event.get(node_id)
                if previous is not None and sent <= previous:
                    return False
                self.last_pump_event[node_id] = sent
                self.submit("report_pump_status", node_id=node_id, is_on=states[event])
            return True
        except (ValueError, TypeError, UnicodeDecodeError):
            return False


cloud_reporter = CloudReporter()
