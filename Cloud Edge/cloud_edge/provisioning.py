"""Nonblocking telemetry-to-provisioning bridge with per-node coalescing."""

from hashlib import sha256
import logging
from queue import Empty, Queue
import re
from threading import Event, Lock, Thread

from .config import CloudConfig
from .device_store import CreateJournal, StoredDevice
from .blynk_provisioner import AuthorizationRequired, ProvisioningError

logger = logging.getLogger(__name__)


def stable_name(edge_id: str, node_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", node_id):
        raise ValueError("Node ID cannot be used for provisioning.")
    digest = sha256(f"{edge_id}:{node_id}".encode()).hexdigest()[:20]
    return f"aiot-{digest}-{node_id[:24]}"


class DeviceProvisioningWorker:
    def __init__(self, *, edge_id, server, store, registry, provisioner,
                 client_factory, profile_backend, on_ready=None):
        self.edge_id, self.server = edge_id, server
        self.store, self.registry, self.provisioner = store, registry, provisioner
        self.journal = CreateJournal(store.path.with_name("pending_creates.json"))
        self.client_factory, self.profile_backend = client_factory, profile_backend
        self.on_ready = on_ready
        self._queue = Queue()
        self._pending = set()
        self._lock = Lock()
        self._stop = Event()
        self._thread = None
        self.status = {}

    def status_for(self, node_id):
        state = self.status.get(node_id, "not_observed")
        client = self.registry.get(node_id)
        if client is not None and state == "active_local" and getattr(client, "commands_ready", False):
            return "commands_ready"
        return state

    def _set_status(self, node_id, state):
        self.status[node_id] = state
        if state == "needs_reconciliation":
            logger.warning("Blynk node %s needs manual binding reconciliation; do not retry create", node_id)
        elif state == "active_local":
            logger.info("Blynk node %s activated locally; MQTT readiness is separate", node_id)

    def start(self):
        self._thread = Thread(target=self._run, name="blynk-provisioning", daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def observe_node(self, node_id: str, snapshot: dict | None = None):
        """Called after Edge accepted and registered telemetry; never blocks MQTT."""
        if self.status.get(node_id) == "needs_reconciliation":
            return False
        try:
            stable_name(self.edge_id, node_id)
        except ValueError:
            self.status[node_id] = "invalid_node_id"
            return False
        with self._lock:
            if node_id in self._pending or self._stop.is_set():
                return False
            self._pending.add(node_id)
            self._queue.put_nowait((node_id, snapshot))
        return True

    def _activate(self, record):
        client = self.client_factory(CloudConfig(record.server, record.token), node_id=record.node_id,
                                     profile_backend=self.profile_backend)
        try:
            client.start()
            self.registry.add(record.node_id, client)
        except Exception:
            client.close()
            raise

    def _process(self, node_id, snapshot):
        client = self.registry.get(node_id)
        if client is None:
            bindings = self.store.load()
            record = bindings.get(node_id)
            if record is None:
                if node_id in self.journal.load():
                    self._set_status(node_id, "needs_reconciliation")
                    return
                name = stable_name(self.edge_id, node_id)
                remote = self.provisioner.find_device(name)
                if remote is not None:
                    self._set_status(node_id, "needs_reconciliation")
                    return
                # Existing manually named devices have no recoverable token via get_device.
                # Do not silently allocate a duplicate for the same node ID.
                legacy = self.provisioner.find_device(node_id)
                if legacy is not None:
                    self._set_status(node_id, "needs_reconciliation")
                    return
                try:
                    self.journal.save(self.journal.load() | {node_id})
                    created = self.provisioner.create_device(name)
                except AuthorizationRequired:
                    self.journal.save(self.journal.load() - {node_id})
                    raise
                except Exception:
                    # A failed call may have succeeded remotely. Never retry blindly.
                    self._set_status(node_id, "needs_reconciliation")
                    return
                record = StoredDevice(node_id, name, created["id"], self.server, created["token"])
                bindings[node_id] = record
                self.store.save(bindings)
                self.journal.save(self.journal.load() - {node_id})
                self._activate(record)
                self._set_status(node_id, "active_local")
                if snapshot and self.on_ready is not None:
                    self.on_ready(node_id, snapshot)
                return
            self._activate(record)
            client = self.registry.get(node_id)
        self._set_status(node_id, "active_local")
        if snapshot and self.on_ready is not None:
            self.on_ready(node_id, snapshot)

    def _run(self):
        while not self._stop.is_set():
            try:
                node_id, snapshot = self._queue.get(timeout=.2)
            except Empty:
                continue
            try:
                self._process(node_id, snapshot)
            except AuthorizationRequired:
                self.status[node_id] = "auth_required"
                logger.warning("Blynk OAuth reauthorization required for node %s", node_id)
            except ProvisioningError:
                self.status[node_id] = "provisioning_error"
                logger.warning("Blynk provisioning failed for node %s", node_id)
            except Exception:
                self.status[node_id] = "local_error"
                logger.warning("Blynk provisioning local failure for node %s", node_id)
            finally:
                with self._lock:
                    self._pending.remove(node_id)
                self._queue.task_done()
