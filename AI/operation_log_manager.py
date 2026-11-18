import json
from copy import deepcopy
from tempfile import NamedTemporaryFile
from pathlib import Path
from configparser import ConfigParser
from node_profile import NodeProfile

CONFIG_FILE = "config.cfg"
LOG_FILE = "operation_log.json"
LOG_STRUCTURE = {
    "model_status": {
        "new_model_pending": False,
        "new_model_ready": False,
        "new_model_path": ""
    },
    "node_profile": {}
}

def atomic_write_text(path: Path, text: str) -> None:
    """Replace a config or log without exposing a partially written file."""
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                delete=False) as file:
            temporary = Path(file.name)
            file.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class OperationLogManager:
    def __init__(self):
        config_path = Path(__file__).with_name(CONFIG_FILE)
        self.config = ConfigParser()
        self.config.read(config_path)
        self.default_profile = json.loads(self.config.get('nodes', 'default_node_profile'))
        self.log_file_path = Path(__file__).with_name(LOG_FILE)
        

    def verify_log_integrity(self, log: dict) -> bool:
        for key in LOG_STRUCTURE:
            if key not in log:
                return False
            if type(log[key]) is not type(LOG_STRUCTURE[key]):
                return False
            if not all(subkey in log[key] for subkey in LOG_STRUCTURE[key]):
                return False
        return True
    

    def load_log(self) -> dict:
        if self.log_file_path.exists():
            with open(self.log_file_path, 'r', encoding='utf-8') as file:
                log = json.load(file)
            if self.verify_log_integrity(log):
                return log
        return deepcopy(LOG_STRUCTURE)
    

    def save_log(self, log: dict):
        if not self.verify_log_integrity(log):
            raise ValueError("Log structure is invalid.")
        atomic_write_text(self.log_file_path, json.dumps(log, indent=4))


    def log_node(self, node_id: str):
        log = self.load_log()

        if node_id in log['node_profile']:
            return

        log['node_profile'][node_id] = {
            "node_id": node_id,
            "plant": self.default_profile["plant"],
            "pwp": self.default_profile["pwp"],
            "madp": self.default_profile["madp"],
            "fc": self.default_profile["fc"],
            "sp": self.default_profile["sp"]
        }
        self.save_log(log)


    def get_model_status(self) -> dict:
        return self.load_log()["model_status"]

    def initialize_retrain_period(self, period: str) -> None:
        log = self.load_log()
        log["model_status"]["last_retrain_period"] = period
        self.save_log(log)

    def log_model_training(self, period: str) -> None:
        log = self.load_log()
        log["model_status"].update(new_model_pending=True, new_model_ready=False,
                                   new_model_path="", last_retrain_period=period)
        self.save_log(log)

    def log_model_ready(self, model_path: str) -> None:
        log = self.load_log()
        status = log["model_status"]
        if not status["new_model_pending"]:
            raise ValueError("No model training is pending.")
        status.update(new_model_pending=False, new_model_ready=True, new_model_path=model_path)
        self.save_log(log)

    def clear_model_status(self, *, retry: bool = False) -> None:
        log = self.load_log()
        status = log["model_status"]
        status.update(new_model_pending=False, new_model_ready=False, new_model_path="")
        if retry:
            status.pop("last_retrain_period", None)
        self.save_log(log)

    def calculate_madp(self, pwp: float, mad: float, fc: float) -> float:
        """
        Calculate the Management Allowed Depletion Point (MADP) based on the provided parameters.
        
        Args:
            pwp (float): Permanent Wilting Point.
            mad (float): Management Allowed Depletion.
            fc (float): Field Capacity.
        
        Returns:
            float: The calculated MADP value, or None if any parameter is missing.
        """
        if not all(isinstance(param, (int, float)) for param in [pwp, mad, fc]):
            return None  # Return None if any parameter is missing or not a number

        madp = fc - mad * (fc - pwp)
        return madp


    def log_node_profile(
        self,
        node_id: str,
        permanent_wilting_point: float = None,
        management_allowed_depletion_point: float = None,
        management_allowable_depletion: float = None,
        field_capacity: float = None,
        saturation_point: float = None,
        plant: str = None):
        log = self.load_log()

        if node_id not in log['node_profile']:
            raise ValueError(f"Node ID {node_id} not found in log.")
        
        # Calculate MADP if not present
        if management_allowed_depletion_point is None:
                management_allowed_depletion_point = self.calculate_madp(
                    pwp=permanent_wilting_point,
                    mad=management_allowable_depletion,
                    fc=field_capacity
                )

        updates = {
            "plant": plant,
            "pwp": permanent_wilting_point,
            "madp": management_allowed_depletion_point,
            "fc": field_capacity,
            "sp": saturation_point,
        }   

        profile = log["node_profile"][node_id]
        profile.update({
            key: value for key, value in updates.items() if value is not None
        })
        self.save_log(log)


    def get_node_profile(self, node_id: str) -> NodeProfile | None:
        """Return the node's profile, or None if it is absent or incomplete."""
        if not isinstance(node_id, str) or not node_id:
            return None

        profile = self.load_log()["node_profile"].get(node_id)
        if not isinstance(profile, dict):
            return None

        fields = ("node_id", "plant", "pwp", "madp", "fc", "sp")
        if any(profile.get(field) is None for field in fields):
            return None

        return NodeProfile(**{field: profile[field] for field in fields})


    def read_all_recorded_nodes(self) -> list[str]:
        log = self.load_log()
        recorded_nodes = [node for node in log['node_profile']]
        return recorded_nodes
    
    
    def log_irrigation_status(self, node_id: str, status: bool):
        log = self.load_log()

        if node_id not in log['node_profile']:
            raise ValueError(f"Node ID {node_id} not found in log.")

        log['node_profile'][node_id]['irrigation_status'] = status
        self.save_log(log)
    
    
operation_log_manager = OperationLogManager()