"""Public, bounded navigation catalog for Blynk HTML widgets."""

import json


MAX_CATALOG_BYTES = 1024


def encode_node_catalog(entries) -> str:
    """Encode (node_id, device_id, server) for registered Blynk clients only."""
    nodes = []
    seen = set()
    for node_id, device_id, server in sorted(entries, key=lambda entry: entry[0]):
        if (not isinstance(node_id, str) or not node_id or len(node_id) > 128
                or not node_id.isprintable() or node_id in seen):
            raise ValueError("Invalid catalog node ID.")
        if device_id is not None and (type(device_id) is not int or device_id <= 0):
            raise ValueError("Invalid catalog device ID.")
        if not isinstance(server, str) or not server.endswith(".blynk.cloud"):
            raise ValueError("Invalid catalog server.")
        seen.add(node_id)
        nodes.append({"n": node_id, "d": device_id, "h": server})
    payload = json.dumps({"v": 1, "nodes": nodes}, ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_CATALOG_BYTES:
        return '{"v":1,"error":"catalog_too_large"}'
    return payload
