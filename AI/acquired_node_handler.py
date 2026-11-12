from pathlib import Path
from configparser import ConfigParser
import json

config_path = Path(__file__).with_name("config.cfg")
config = ConfigParser()
with config_path.open(encoding="utf-8-sig") as file:
    config.read_file(file)
acquired_nodes_path = Path(__file__).with_name(config.get("nodes", "acquired_nodes_path"))

class AcquiredNodeHandler:
    @staticmethod
    def record_acquired_node(node_id: str) -> None:
        """
        Record a new acquired node ID to the acquired nodes list.
        """
        try:
            with acquired_nodes_path.open("r", encoding="utf-8") as file:
                acquired_nodes = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            acquired_nodes = []

        if node_id not in acquired_nodes:
            acquired_nodes.append(node_id)
            with acquired_nodes_path.open("w", encoding="utf-8") as file:
                json.dump(acquired_nodes, file, indent=4)

    @staticmethod
    def read_all_nodes() -> set[str]:
        """
        Read all acquired node IDs from the acquired nodes list.
        """
        try:
            with acquired_nodes_path.open("r", encoding="utf-8") as file:
                acquired_nodes = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            acquired_nodes = []
        return set(acquired_nodes)