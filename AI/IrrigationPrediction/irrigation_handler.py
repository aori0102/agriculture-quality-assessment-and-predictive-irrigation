from cloud_reporter import cloud_reporter
from .model_retrainer import load_or_train_initial_model
import pandas as pd
import paho.mqtt.client as mqtt
import json
from .irrigation_data_handler import irrigation_data_handler
from . import field_constants as fc
from .telemetry_handler import telemetry_handler
from time_helper import TimeHelper
from pathlib import Path
from configparser import ConfigParser
from datetime import datetime, timezone
from time import monotonic
from action_index import ActionIndex
from command_proxy import command_proxy
from operation_log_manager import operation_log_manager

class IrrigationHandler:
    def __init__(self):
        cfg_path = Path(__file__).with_name("config.cfg")
        cfg = ConfigParser()

        with cfg_path.open(encoding="utf-8-sig") as file:
            cfg.read_file(file)
            
        self.current_unsampled_nodes = set([])
        self.current_sample_slot = None
        self.last_handling = monotonic()
        
        self.sample_delay_seconds = cfg.getfloat("sampling", "sample_delay_seconds", fallback=1.0)

        self.irrigation_sampling_duration = json.loads(cfg.get("irrigation", "irrigation_sampling", fallback="[0.1, 0.5, 1.0, 2.0, 5.0, 8.0]"))
        self.max_irrigation_duration = self.irrigation_sampling_duration[-1]
        self.irrigation_sampling_duration = self.irrigation_sampling_duration[:-1]
                
        self.model = load_or_train_initial_model(cfg_path.parent, operation_log_manager)
            
        
    def predict(
        self,
        soil_moisture_percentage: float,
        soil_moisture_change_last_10m: float,
        air_temperature_celsius: float,
        air_relative_humidity_percentage: float,
        irrigation_last_10m: float,
        irrigation_duration_minutes: float
    ) -> float:
        """Predict soil moisture (%) after 10 minutes using the trained pipeline.

        Irrigation inputs are durations in minutes for the previous and upcoming
        10-minute windows. Feature names match random_forest_train.py.
        """
        sample = pd.DataFrame([{
            fc.SOIL_MOISTURE: soil_moisture_percentage,
            fc.WEATHER_TEMPERATURE: air_temperature_celsius,
            fc.WEATHER_HUMIDITY: air_relative_humidity_percentage,
            fc.IRRIGATION_PAST_10M: irrigation_last_10m,
            fc.IRRIGATION_DURATION_MINUTES: irrigation_duration_minutes,
            fc.SOIL_CHANGE_10M: soil_moisture_change_last_10m,
        }])

        model_features = self.model.feature_names_in_
        return self.model.predict(sample[model_features])[0]


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
            fc.SOIL_MOISTURE: soil_moisture_pct,
            fc.SOIL_CHANGE_10M: soil_change_10m,
            fc.WEATHER_TEMPERATURE: air_temp_c,
            fc.WEATHER_HUMIDITY: air_rh_pct,
            fc.IRRIGATION_PAST_10M: irrigation_10m,
        }])

        model_features = self.model.feature_names_in_
        legacy_names = {
            fc.SOIL_MOISTURE: "soil_moisture_pct",
            fc.WEATHER_TEMPERATURE: "air_temp_c",
            fc.WEATHER_HUMIDITY: "air_rh_pct",
        }
        sample = sample.rename(columns={
            current: legacy for current, legacy in legacy_names.items()
            if current not in model_features and legacy in model_features
        })
        return self.model.predict(sample[model_features])[0]
                
                
    def loop(self) -> None:
        """Try pending nodes throughout the current window and close older windows."""
        current_time = datetime.now(timezone.utc)
        sample_slot = TimeHelper.get_slot(current_time)
        if sample_slot != self.current_sample_slot:
            closed_slot = self.current_sample_slot
            if closed_slot is None:
                closed_slot = TimeHelper.get_slot(sample_slot, offset_windows=-1)
            irrigation_data_handler.fill_expired_irrigation(
                current_time,
                closed_slot=closed_slot,
                node_ids=set(operation_log_manager.read_all_recorded_nodes()),
            )
            self.current_sample_slot = sample_slot

        # Check if enough time has passed since the last handling to avoid excessive sampling        
        now = monotonic()
        elapse = now - self.last_handling
        if elapse < self.sample_delay_seconds:
            return
        
        self.last_handling = now
        
        # Refresh each pass so newly discovered nodes are included. Persisted
        # decisions are skipped, including after restarting within a window.
        self.current_unsampled_nodes = {
            node_id for node_id in operation_log_manager.read_all_recorded_nodes()
            if not irrigation_data_handler.has_irrigation_duration(node_id, sample_slot)
        }

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
        
        print(f"Handling irrigation for node: {node_id}")
        
        if node_id is None:
            return False
        
        target_sample_time = self.current_sample_slot
        if target_sample_time is None or target_sample_time != TimeHelper.get_current_slot():
            return False
        if irrigation_data_handler.has_irrigation_duration(node_id, target_sample_time):
            return True
        data = irrigation_data_handler.get_irrigation_data(node_id=node_id, timestamp=target_sample_time)
        if data is None:
            print(f"Cannot fetch irrigation data: {irrigation_data_handler.last_fetch_error}")
            return False

        # Unmatched timestamp
        sample_time = TimeHelper.process_time(data[fc.TIMESTAMP])
        if sample_time is None or sample_time != target_sample_time:
            return False

        # Record current moisture and predict future one
        current_soil_moisture = data[fc.SOIL_MOISTURE]
        next_moisture_without_irrigation = self.predict(
            soil_moisture_percentage=current_soil_moisture,
            soil_moisture_change_last_10m=data[fc.SOIL_CHANGE_10M],
            air_temperature_celsius=data[fc.WEATHER_TEMPERATURE],
            air_relative_humidity_percentage=data[fc.WEATHER_HUMIDITY],
            irrigation_last_10m=data[fc.IRRIGATION_PAST_10M],
            irrigation_duration_minutes=0.0
        )
        cloud_reporter.submit(
            "send_ai_results", node_id=node_id,
            predicted_moisture_pct=float(next_moisture_without_irrigation),
        )
        plant_profile = operation_log_manager.get_node_profile(node_id)

        skip_irrigation = (
            current_soil_moisture > plant_profile.fc or
            next_moisture_without_irrigation > plant_profile.fc
        )

        if skip_irrigation:
            telemetry_handler.record_irrigation_data(0, node_id=node_id, timestamp=target_sample_time)
            return True

        # Determine the irrigation duration needed to reach the target soil moisture
        target_duration = self.max_irrigation_duration
        for duration in self.irrigation_sampling_duration:
            predicted_moisture = self.predict(
                soil_moisture_percentage=current_soil_moisture,
                soil_moisture_change_last_10m=data[fc.SOIL_CHANGE_10M],
                air_temperature_celsius=data[fc.WEATHER_TEMPERATURE],
                air_relative_humidity_percentage=data[fc.WEATHER_HUMIDITY],
                irrigation_last_10m=data[fc.IRRIGATION_PAST_10M],
                irrigation_duration_minutes=duration
            )
            if plant_profile.madp <= predicted_moisture <= plant_profile.fc:
                target_duration = duration
                break
            
        # Inference may finish after the window has changed; do not send a stale decision.
        if TimeHelper.get_current_slot() != target_sample_time:
            return False
        if not telemetry_handler.record_irrigation_data(target_duration, node_id=node_id, timestamp=target_sample_time):
            return False
        self.send_irrigation_command(node_id, irrigation_minutes=target_duration)
        return True
    
    
    def configure_command_publisher(self, client: mqtt.Client, config: ConfigParser) -> None:
        self.edge_id = config['edge']['edge_id'].strip()
        self.command_topic = config['mqtt']['command_topic']
        self.command_qos = config.getint('mqtt', 'command_qos')
        self.command_retain = config.getboolean('mqtt', 'command_retain')
        self.max_duration_ms = config.getint('pump', 'max_duration_ms')
        self.mqtt_client = client


    def send_irrigation_command(self, node_id: str, irrigation_minutes: int) -> None:
        """
        Send an irrigation command to the specified node via MQTT. The command will instruct
        the node to start the pump for the specified duration in minutes.

        Args:
            node_id (str): The ID of the node to which the command will be sent.
            irrigation_minutes (int): The duration for which the pump should run, in minutes.
        """
        if irrigation_minutes <= 0:
            print(f"Invalid irrigation duration: {irrigation_minutes} minutes. Command not sent.")
            return

        duration_ms = int(irrigation_minutes * 60 * 1000)
        if duration_ms > self.max_duration_ms:
            print(f"Requested irrigation duration {duration_ms} ms exceeds maximum allowed {self.max_duration_ms} ms. Command not sent.")
            return

        command_proxy.send_command(
            node_id=node_id,
            action=ActionIndex.StartPump,
            ttl_ms=300_000,
            duration_ms=duration_ms
        )


irrigation_handler = IrrigationHandler()
