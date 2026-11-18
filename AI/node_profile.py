from dataclasses import dataclass


@dataclass
class NodeProfile:
    """Plant thresholds for a node, matching operation_log.json."""

    node_id: str
    plant: str
    pwp: float
    madp: float
    fc: float
    sp: float
