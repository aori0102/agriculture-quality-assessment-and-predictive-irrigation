import json
import paho.mqtt.client as mqtt
import csv
import pandas as pd
from . import field_constants as fc
from .time_helper import TimeHelper
from time import time
from pathlib import Path
from configparser import ConfigParser
from datetime import timezone

TELEMETRY_FIELDS = (fc.SAMPLED_AT, "soil_index_pct", "temperature_c", "air_humidity_pct")
LEGACY_TELEMETRY_FIELDS = (fc.TIMESTAMP, fc.SOIL_MOISTURE_PERCENTAGE, fc.AIR_TEMP_C, fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE)
SENSORS_FIELDS = [fc.TIMESTAMP, fc.NODE_ID, fc.SOIL_MOISTURE_PERCENTAGE, fc.AIR_TEMP_C, fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE]
IRRIGATION_FIELDS = [fc.TIMESTAMP, fc.NODE_ID, fc.IRRIGATION_10M]

class TelemetryHandler:
    def __init__(self):
        cfg_path = Path(__file__).with_name("config.cfg")
        cfg = ConfigParser()
        with cfg_path.open(encoding="utf-8-sig") as file:
            cfg.read_file(file)

        self.sensors_path = Path(__file__).with_name(cfg.get("sensors", "sensors_history_path"))
        try:
            with self.sensors_path.open("rb") as file:
                first_line = file.readline().decode("utf-8-sig")
                header = next(csv.reader([first_line], strict=True), [])
        except (FileNotFoundError, UnicodeDecodeError, csv.Error):
            header = []

        if header != SENSORS_FIELDS:
            with self.sensors_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=SENSORS_FIELDS)
                writer.writeheader()

        self.irrigation_path = Path(__file__).with_name(cfg.get("irrigation", "irrigation_history_path"))
        try:
            with self.irrigation_path.open("rb") as file:
                first_line = file.readline().decode("utf-8-sig")
                header = next(csv.reader([first_line], strict=True), [])
        except (FileNotFoundError, UnicodeDecodeError, csv.Error):
            header = []
            
        if header != IRRIGATION_FIELDS:
            with self.irrigation_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=IRRIGATION_FIELDS)
                writer.writeheader()
                

    def get_irrigation_data(self, node_id: str) -> dict[str, str | float] | None:
        """
        Get the latest irrigation data of the given node. This includes the latest soil moisture percentage, the change in 
        soil moisture over the last 10 minutes, the latest air temperature, the latest air relative humidity percentage, and the
        irrigation amount over the last 10 minutes. If there is any missing data, return None. The irrigation amount will be 0
        if there is no irrigation data available for the node.
        
        Args:
            node_id (str): The ID of the node to get the irrigation data for.
            
        Returns:
            dict[str, str | float] | None: A dictionary containing the latest irrigation 
            data for the specified node, or None if the node is not found or if there 
            is missing data.
        """
        if node_id is None:
            return None
        
        # Read data history
        try:
            node_id_dtype = {fc.NODE_ID: str}
            sensor_data_frame = pd.read_csv(self.sensors_path, dtype=node_id_dtype)
            irrigation_data_frame = pd.read_csv(self.irrigation_path, dtype=node_id_dtype)
        except pd.errors.EmptyDataError:
            return None
        
        # Filter data for the specified node
        sensor_data = sensor_data_frame.loc[sensor_data_frame[fc.NODE_ID] == node_id]
        irrigation_data = irrigation_data_frame.loc[irrigation_data_frame[fc.NODE_ID] == node_id]
        
        # CSV rows are appended in arrival order, which can differ from sample order.
        sample_times = pd.to_datetime(sensor_data[fc.TIMESTAMP], format="mixed", utc=True, errors="coerce")
        sensor_data = sensor_data.loc[sample_times.dropna().sort_values(kind="stable").index]

        # Check if there is enough data to calculate the change in soil moisture over 
        # the last 10 minutes along with the delta with the previous reading. If not,
        # return None.
        if len(sensor_data) < 2:
            return None

        latest_sensor = sensor_data.iloc[-1]
        previous_sensor = sensor_data.iloc[-2]
        soil_moisture_pct = latest_sensor[fc.SOIL_MOISTURE_PERCENTAGE]
        return {
            fc.TIMESTAMP: latest_sensor[fc.TIMESTAMP],
            fc.SOIL_MOISTURE_PERCENTAGE: soil_moisture_pct,
            fc.SOIL_CHANGE_10M: soil_moisture_pct - previous_sensor[fc.SOIL_MOISTURE_PERCENTAGE],
            fc.AIR_TEMP_C: latest_sensor[fc.AIR_TEMP_C],
            fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE: latest_sensor[fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE],
            fc.IRRIGATION_10M: irrigation_data.iloc[-1][fc.IRRIGATION_10M] if not irrigation_data.empty else 0.0,
            fc.NODE_ID: latest_sensor[fc.NODE_ID],
        }
        
    
    def record_sensors_telemetry(self, message: mqtt.MQTTMessage, node_id: str) -> bool:
        """
        Record the sensor data from the MQTT message payload into the sensors history CSV file
        with the corresponding node ID. The message payload should be a JSON object.
        
        Args:
            message (mqtt.MQTTMessage): The MQTT message containing the sensor data in its payload.
            node_id (str): The ID of the node from which the telemetry data is received.
        
        Returns:
            True if the data was recorded successfully, False otherwise.
        """
        # Check JSON availability and validity
        if not message.payload:
            print(f"Empty payload on {message.topic}")
            return False
        try:
            data = json.loads(message.payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            print(f"Invalid JSON on {message.topic}: {error}")
            return False

        if not isinstance(data, dict):
            print(f"Expected a JSON object on {message.topic}")
            return False

        fields = LEGACY_TELEMETRY_FIELDS
        if fc.SAMPLED_AT in data:
            fields = TELEMETRY_FIELDS
            if data.get(fc.NODE_ID) != node_id:
                print(f"Payload node_id does not match topic {message.topic}")
                return False

        if not all(field in data for field in fields):
            print(f"Missing fields in {message.topic}: {data}")
            return False

        raw_timestamp, soil_moisture_pct, air_temp_c, humidity_pct = (data[field] for field in fields)
        if any(type(value) not in (int, float) for value in (soil_moisture_pct, air_temp_c, humidity_pct)):
            print(f"Expected numeric sensor values on {message.topic}")
            return False
        if not float('-inf') < air_temp_c < float('inf'):
            print(f"Invalid air temperature on {message.topic}: {air_temp_c}")
            return False
    
        # Process data
        timestamp = TimeHelper.process_time(raw_timestamp)
        if not timestamp:
            print(f"Invalid timestamp format on {message.topic}: {raw_timestamp}")
            return False
        if fc.SAMPLED_AT in data and timestamp.tzinfo is None:
            print(f"Expected a timezone in sampled_at on {message.topic}")
            return False
        timestamp = TimeHelper.floor_to_nearest_ten_minutes(timestamp.astimezone(timezone.utc))
        
        if not 0 <= soil_moisture_pct <= 100:
            print(f"Invalid soil moisture percentage on {message.topic}: {soil_moisture_pct}")
            return False
        
        if not 0 <= humidity_pct <= 100:
            print(f"Invalid air relative humidity percentage on {message.topic}: {humidity_pct}")
            return False
        
        # Record data into CSV
        needs_header = not self.sensors_path.exists() or self.sensors_path.stat().st_size == 0
        with self.sensors_path.open("a", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=SENSORS_FIELDS)
            if needs_header:
                writer.writeheader()
            writer.writerow({
                fc.TIMESTAMP: timestamp.isoformat(timespec="seconds"),
                fc.NODE_ID: node_id,
                fc.SOIL_MOISTURE_PERCENTAGE: soil_moisture_pct,
                fc.AIR_TEMP_C: air_temp_c,
                fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE: humidity_pct
            })
            
        from .irrigation_handler import irrigation_handler
        irrigation_handler.record_acquired_node(node_id)
        return True
    

    def record_irrigation_data(self, irrigation_value: float, node_id: str) -> None:
        """
        Record the irrigation data into the irrigation history CSV file with the
        corresponding node ID.
        
        Args:
            irrigation_value (float): The amount of irrigation applied for the next 10 minutes.
            node_id (str): The ID of the node for which the irrigation data is recorded.
        """
        needs_header = not self.irrigation_path.exists() or self.irrigation_path.stat().st_size == 0
        with self.irrigation_path.open("a", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=IRRIGATION_FIELDS)
            if needs_header:
                writer.writeheader()
            writer.writerow({
                fc.TIMESTAMP: TimeHelper.get_current_timestamp(),
                fc.NODE_ID: node_id,
                fc.IRRIGATION_10M: irrigation_value
            })
            
telemetry_handler = TelemetryHandler()