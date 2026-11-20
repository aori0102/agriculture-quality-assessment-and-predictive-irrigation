"""Forward irrigation intent to Edge; never publish confirmed hardware status."""

from dataclasses import dataclass
import logging
from threading import Event, Lock, RLock, Thread
from typing import Literal

from .commands import PumpCommand

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PumpRequestResult:
    node_id: str
    requested_on: bool | None
    outcome: Literal['saved', 'unchanged', 'rejected', 'sync_failed', 'discarded']
    message: str


class EdgePumpBackend:
    """One shared reentrant operation lock for pump and profile adapters.

    Existing AI functions persist a flag, not hardware state. Callables must
    return promptly; ongoing updates are not cancelled or retried automatically.
    """

    def __init__(self, enable, disable, load_log, *, operation_lock=None, control_request=None):
        if not all(callable(fn) for fn in (enable, disable, load_log)):
            raise ValueError('Edge enable, disable, and read functions must be callable.')
        self._enable, self._disable, self._read = enable, disable, load_log
        self._control_request = control_request
        self._lock = operation_lock if operation_lock is not None else RLock()

    def _record(self, node_id):
        log = self._read()
        if not isinstance(log, dict) or not isinstance(log.get('node_profile'), dict):
            raise ValueError('Invalid Edge log.')
        if node_id not in log['node_profile']:
            return None
        record = log['node_profile'][node_id]
        if not isinstance(record, dict) or record.get('node_id') != node_id:
            raise ValueError('Invalid Edge node record.')
        if 'irrigation_status' in record and type(record['irrigation_status']) is not bool:
            raise ValueError('Saved irrigation flag must be Boolean.')
        return record

    def try_dispatch(self, client, node_id: str, *, should_stop=lambda: False) -> PumpRequestResult | None:
        # Do not consume intent while a profile update holds the lock: OFF can
        # still replace ON, and MQTT disconnect can still clear the pending slot.
        if not self._lock.acquire(blocking=False):
            return None
        try:
            connection_id = getattr(client, 'command_connection_id', None)
            command = client.poll_command()
            if command is None:
                return None
            if not isinstance(command, PumpCommand):
                raise ValueError('Client returned an invalid command type.')
            def result(outcome, message):
                return PumpRequestResult(node_id, command.requested_on, outcome, message)
            if command.node_id != node_id or type(command.requested_on) is not bool:
                return result('rejected', 'Request does not match the configured node binding')
            def stale():
                return (should_stop() or not client.commands_ready or command.is_expired()
                        or getattr(client, 'command_connection_id', None) != connection_id)
            if stale():
                return result('discarded', 'Request expired, disconnected, or dispatcher stopped')
            try:
                record = self._record(node_id)
                if record is None:
                    return result('rejected', 'Unknown Edge node; register it in Edge first')
                if stale():
                    return result('discarded', 'Request expired, disconnected, or dispatcher stopped')
                if self._control_request is not None:
                    accepted = self._control_request(node_id, command.control, command.requested_on)
                    return result('saved' if accepted else 'rejected',
                                  'Edge control request accepted; hardware state unconfirmed' if accepted
                                  else 'Edge control request rejected or local MQTT unavailable')
                if record.get('irrigation_status') is command.requested_on:
                    return result('unchanged', 'Requested irrigation flag already saved; hardware state unconfirmed')
                update = self._enable if command.requested_on else self._disable
                update(node_id=node_id)
                saved = self._record(node_id)
                if saved is None or saved.get('irrigation_status') is not command.requested_on:
                    raise ValueError('Irrigation flag readback mismatch.')
                return result('saved', 'Requested irrigation flag saved; hardware state unconfirmed')
            except Exception:
                return result('sync_failed', 'Edge flag update/readback failed; saved state is uncertain')
        finally:
            self._lock.release()


class PumpDispatcher:
    """One background poller, with no HTTP or hardware-status publishing API."""

    def __init__(self, clients, backend: EdgePumpBackend, *, interval: float = .1):
        if type(interval) not in (int, float) or not 0 < interval < float('inf'):
            raise ValueError('Polling interval must be positive and finite.')
        self._clients = clients
        self._backend, self._interval = backend, interval
        self._stop = Event()
        self._lock = Lock()
        self._results: dict[str, PumpRequestResult] = {}
        self._thread = None

    def start(self):
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError('Pump dispatcher is closed.')
            if self._thread is None:
                self._thread = Thread(target=self._run, name='cloud-edge-pump', daemon=True)
                self._thread.start()

    def close(self):
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join()

    def last_result(self, node_id: str) -> PumpRequestResult | None:
        with self._lock:
            return self._results.get(node_id)

    def last_error(self, node_id: str) -> str | None:
        result = self.last_result(node_id)
        if result is not None and result.outcome in ('rejected', 'sync_failed'):
            return result.message
        return None

    def _run(self):
        while not self._stop.is_set():
            clients = self._clients.snapshot() if hasattr(self._clients, 'snapshot') else tuple(self._clients.items())
            for node_id, client in clients:
                if self._stop.is_set():
                    break
                try:
                    result = self._backend.try_dispatch(client, node_id, should_stop=self._stop.is_set)
                except Exception:
                    result = PumpRequestResult(node_id, None, 'sync_failed',
                                               'Command dispatch failed; inspect local integration')
                if result is not None:
                    with self._lock:
                        self._results[node_id] = result
                    log = logger.warning if result.outcome in ('rejected', 'sync_failed') else logger.info
                    log('Irrigation request node=%s outcome=%s: %s', node_id, result.outcome, result.message)
            self._stop.wait(self._interval)
