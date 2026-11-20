"""Live CloudClient lookup without holding locks across network calls."""

from threading import RLock


class RuntimeDeviceRegistry:
    def __init__(self):
        self._clients = {}
        self._lock = RLock()

    def get(self, node_id):
        with self._lock:
            return self._clients.get(node_id)

    def add(self, node_id, client):
        with self._lock:
            if node_id in self._clients:
                raise ValueError("Node already has an active CloudClient.")
            self._clients[node_id] = client

    def snapshot(self):
        with self._lock:
            return tuple(self._clients.items())

    def close(self):
        with self._lock:
            clients = tuple(self._clients.values())
            self._clients.clear()
        first_error = None
        for client in reversed(clients):
            try:
                client.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
