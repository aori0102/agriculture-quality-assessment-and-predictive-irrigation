import joblib
import pandas as pd
import json
from . import field_constants as fc
from .telemetry_handler import telemetry_handler
from .time_helper import TimeHelper
from pathlib import Path
from configparser import ConfigParser
from datetime import datetime, timezone
from time import monotonic

class IrrigationHandler:
    def __init__(self):
        cfg_path = Path(__file__).with_name("config.cfg")
        cfg = ConfigParser()

        with cfg_path.open(encoding="utf-8-sig") as file:
            cfg.read_file(file)
            
        self.current_unsampled_nodes = set([])
        self.current_sample_slot = None
        self.has_handled_this_timestamp = False
        self.last_handling = monotonic()
                
        self.acquired_nodes_path = Path(__file__).with_name(cfg.get("nodes", "acquired_nodes_path"))

        self.default_irrigation_minutes = cfg.getint("irrigation", "default_irrigation_minutes")
        self.reduced_irrigation_minutes = cfg.getint("irrigation", "reduced_irrigation_minutes")
        self.sample_delay_seconds = cfg.getint("sampling", "sample_delay_seconds")
        self.sample_timeout_minutes = cfg.getint("sampling", "sample_timeout_minutes")
                
        model_path = Path(__file__).with_name(cfg.get("model", "model_path"))
        self.model = joblib.load(model_path)
        
        plant_path = Path(__file__).with_name(cfg.get("plant", "plant_profile_path"))
        with plant_path.open("r", encoding="utf-8") as file:
            self.plant_profile = json.load(file)
    
    
    def record_acquired_node(self, node_id: str) -> None:
        """
        Record a new acquired node ID to the acquired nodes list. This is used to
        keep track of which nodes will be sampled for irrigation prediction. If the 
        node ID is already in the list, it will not be added again.
        """
        try:
            with self.acquired_nodes_path.open("r", encoding="utf-8") as file:
                acquired_nodes = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            acquired_nodes = []
            
        if node_id not in acquired_nodes:
            acquired_nodes.append(node_id)
            with self.acquired_nodes_path.open("w", encoding="utf-8") as file:
                json.dump(acquired_nodes, file, indent=4)


    def predict_soil_moisture(
        self,
        soil_moisture_pct: float,
        soil_change_10m: float,
        air_temp_c: float,
        air_rh_pct: float,
        irrigation_10m: float
        ) -> float:
        """
        Using the trained machine learning model, predict the soil moisture percentage in
        10 minutes based on the provided features.
        """
        sample = pd.DataFrame([{
            fc.SOIL_MOISTURE_PERCENTAGE: soil_moisture_pct,
            fc.SOIL_CHANGE_10M: soil_change_10m,
            fc.AIR_TEMP_C: air_temp_c,
            fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE: air_rh_pct,
            fc.IRRIGATION_10M: irrigation_10m,
        }])

        model_features = self.model.feature_names_in_
        legacy_names = {
            fc.SOIL_MOISTURE_PERCENTAGE: "soil_moisture_pct",
            fc.AIR_TEMP_C: "air_temp_c",
            fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE: "air_rh_pct",
        }
        sample = sample.rename(columns={
            current: legacy for current, legacy in legacy_names.items()
            if current not in model_features and legacy in model_features
        })
        return self.model.predict(sample[model_features])[0]
                
                
    def loop(self) -> None:
        """
        Main loop for the irrigation handler. Every 10 minutes, the handler attempts to sample
        all acquired nodes for irrigation prediction. The samples are processed every 1 second,
        with a 5 minutes timeout. Beyond which, the unsampled nodes are ignored and will have to
        wait for the next window.
        The handler only samples nodes that have the correct data for the current timestamp. It
        will be ignored otherwise.
        """
        current_time = datetime.now(timezone.utc)
        sample_slot = TimeHelper.floor_to_nearest_ten_minutes(current_time)
        if sample_slot != self.current_sample_slot:
            self.current_sample_slot = sample_slot
            self.current_unsampled_nodes.clear()
            self.has_handled_this_timestamp = False

        # All unsampled nodes in the current window will be recorded as 0 irrigation.
        current_minute_in_window = current_time.minute % 10
        if current_minute_in_window > self.sample_timeout_minutes:
            if not self.has_handled_this_timestamp:
                return
            
            for remaining_node in self.current_unsampled_nodes:
                telemetry_handler.record_irrigation_data(0, node_id=remaining_node)
            self.current_unsampled_nodes.clear()
            self.has_handled_this_timestamp = False
            return

        # Check if enough time has passed since the last handling to avoid excessive sampling        
        now = monotonic()
        elapse = now - self.last_handling
        if elapse < self.sample_delay_seconds:
            return
        
        self.last_handling = now
        
        # Rebuild the sampling list if not already
        if not self.has_handled_this_timestamp:
            self.current_unsampled_nodes = set([])
            try:
                with self.acquired_nodes_path.open("r", encoding="utf-8") as file:
                    self.current_unsampled_nodes = set(json.load(file))
            except (FileNotFoundError, json.JSONDecodeError):
                return
            self.has_handled_this_timestamp = True

        # Sample all nodes
        sampling = self.current_unsampled_nodes.copy()
        for node_id in sampling:
            if self.handle_irrigation(node_id):
                print(f"Node {node_id} successfully sampled!")
                self.current_unsampled_nodes.remove(node_id)
                

    def handle_irrigation(self, node_id: str = None) -> bool:
        """
        Handle the irrigation prediction for a specific node. This function retrieves the latest
        telemetry data for the node, predicts the soil moisture in 10 minutes, and determines if
        irrigation is needed. If irrigation is needed, it records the appropriate irrigation
        duration using the telemetry_handler.
        
        Args:
            node_id (str): The ID of the node for which to handle irrigation.

        Returns:
            bool: True if the irrigation was handled successfully, False otherwise.
        """
        
        if node_id is None:
            return False
        
        data = telemetry_handler.get_irrigation_data(node_id=node_id)
        if data is None:
            return False

        # Unmatched timestamp
        sample_time = TimeHelper.process_time(data[fc.TIMESTAMP])
        target_sample_time = TimeHelper.get_current_slot()
        if sample_time is None or sample_time.astimezone(timezone.utc) != target_sample_time:
            return False

        # Record current moisture and predict future one
        current_soil_moisture = data[fc.SOIL_MOISTURE_PERCENTAGE]
        predicted_soil_moisture = self.predict_soil_moisture(
            data[fc.SOIL_MOISTURE_PERCENTAGE],
            data[fc.SOIL_CHANGE_10M],
            data[fc.AIR_TEMP_C],
            data[fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE],
            data[fc.IRRIGATION_10M]
            )
        
        # Saturated moisture range. No irrigation needed.
        if current_soil_moisture >= self.plant_profile[fc.SATURATION_POINT]:
            print(f"Current soil moisture {current_soil_moisture:.2f}% is above saturation threshold. No irrigation needed.")
            telemetry_handler.record_irrigation_data(0, node_id=node_id)
            return True
        
        # Excessive moisture range. No irrigation needed.
        if current_soil_moisture >= self.plant_profile[fc.FIELD_CAPACITY]:
            print(f"Current soil moisture {current_soil_moisture:.2f}% is above field capacity threshold. No irrigation needed.")
            telemetry_handler.record_irrigation_data(0, node_id=node_id)
            return True
        
        # Dangerous moisture range. Irrigation is highly needed.
        pwp = self.plant_profile[fc.PERMANENT_WILTING_POINT]
        if current_soil_moisture <= pwp or predicted_soil_moisture <= pwp:
            print(f"Predicted soil moisture {predicted_soil_moisture:.2f}% or current soil moisture {current_soil_moisture:.2f}% is below permanent wilting point. Irrigation needed.")
            telemetry_handler.record_irrigation_data(self.default_irrigation_minutes, node_id=node_id)
            return True
        
        # Stressed moisture ranged. Default or reduced irrigation depending on the predicted
        # moisture.
        madp = self.plant_profile[fc.MANAGEMENT_ALLOWED_DEPLETION_POINT]
        if current_soil_moisture < madp:
            print(f"Current soil moisture {current_soil_moisture:.2f}% is below management allowed depletion point. Irrigation needed.")
            telemetry_handler.record_irrigation_data(self.default_irrigation_minutes, node_id=node_id)
            return True
        
        if predicted_soil_moisture < madp:
            print(f"Predicted soil moisture {predicted_soil_moisture:.2f}% is below management allowed depletion point. Irrigation needed.")
            telemetry_handler.record_irrigation_data(self.reduced_irrigation_minutes, node_id=node_id)
            return True
        
        # Optimal moisture
        print(f"Predicted soil moisture {predicted_soil_moisture:.2f}% is optimal. No irrigation needed.")
        telemetry_handler.record_irrigation_data(0, node_id=node_id)
        return True
        
irrigation_handler = IrrigationHandler()