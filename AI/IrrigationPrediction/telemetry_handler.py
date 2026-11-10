import json
from time import time
import paho.mqtt.client as mqtt
from pathlib import Path
import csv
import pandas as pd
from .time_helper import TimeHelper
from . import field_constants as fc
from configparser import ConfigParser

SENSORS_RAW = [fc.SOIL_MOISTURE_PERCENTAGE, fc.AIR_TEMP_C, fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE]
SENSORS_FIELDS = [fc.TIMESTAMP, fc.SOIL_MOISTURE_PERCENTAGE, fc.AIR_TEMP_C, fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE]
IRRIGATION_FIELDS = [fc.TIMESTAMP, fc.IRRIGATION_10M]

class TelemetryHandler:
    def __init__(self):
        cfg_path = Path(__file__).with_name("config.cfg")
        cfg = ConfigParser()
        with cfg_path.open(encoding="utf-8-sig") as file:
            cfg.read_file(file)

        self.sensors_path = Path(cfg.get("sensors", "sensors_history_path"))
        if not self.sensors_path.exists():
            self.sensors_path.touch()
            with self.sensors_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=SENSORS_FIELDS)
                writer.writeheader()
                
        self.irrigation_path = Path(cfg.get("irrigation", "irrigation_history_path"))
        if not self.irrigation_path.exists():
            self.irrigation_path.touch()
            with self.irrigation_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=IRRIGATION_FIELDS)
                writer.writeheader()

    def get_irrigation_data(self) -> dict[str, str | float] | None:
        """
        Get the latest irrigation data. This includes the latest soil moisture percentage, the change in soil moisture
        over the last 10 minutes, the latest air temperature, the latest air relative humidity percentage, and the
        irrigation amount over the last 10 minutes. If there is any missing data, return None.
        """
        try:
            sensor_data = pd.read_csv(self.sensors_path)
            irrigation_data = pd.read_csv(self.irrigation_path)
        except pd.errors.EmptyDataError:
            return None
        if len(sensor_data) < 2 or len(irrigation_data) < 1:
            return None

        latest_sensor = sensor_data.iloc[-1]
        latest_irrigation = irrigation_data.iloc[-1]
        previous_sensor = sensor_data.iloc[-2]
        soil_moisture_pct = latest_sensor[fc.SOIL_MOISTURE_PERCENTAGE]
        return {
            fc.TIMESTAMP: latest_sensor[fc.TIMESTAMP],
            fc.SOIL_MOISTURE_PERCENTAGE: soil_moisture_pct,
            fc.SOIL_CHANGE_10M: soil_moisture_pct - previous_sensor[fc.SOIL_MOISTURE_PERCENTAGE],
            fc.AIR_TEMP_C: latest_sensor[fc.AIR_TEMP_C],
            fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE: latest_sensor[fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE],
            fc.IRRIGATION_10M: latest_irrigation[fc.IRRIGATION_10M],
        }

    def record_sensors_telemetry(self, message: mqtt.MQTTMessage):
        try:
            data = json.loads(message.payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            print(f"Invalid JSON on {message.topic}: {error}")
            return None

        if not all(field in data for field in SENSORS_RAW):
            print(f"Missing fields in {message.topic}: {data}")
            return None

        timestamp, soil_moisture_pct, air_temp_c, humidity_pct = (
            TimeHelper.get_current_timestamp(),
            data[fc.SOIL_MOISTURE_PERCENTAGE],
            data[fc.AIR_TEMP_C],
            data[fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE]
        )
    
        needs_header = not self.sensors_path.exists() or self.sensors_path.stat().st_size == 0
        with self.sensors_path.open("a", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=SENSORS_FIELDS)
            if needs_header:
                writer.writeheader()
            writer.writerow({
                fc.TIMESTAMP: timestamp,
                fc.SOIL_MOISTURE_PERCENTAGE: soil_moisture_pct,
                fc.AIR_TEMP_C: air_temp_c,
                fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE: humidity_pct
            })
       
            
    def record_irrigation_data(self, irrigation_value: float) -> None:
        needs_header = not self.irrigation_path.exists() or self.irrigation_path.stat().st_size == 0
        with self.irrigation_path.open("a", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=IRRIGATION_FIELDS)
            if needs_header:
                writer.writeheader()
            writer.writerow({
                fc.TIMESTAMP: TimeHelper.get_current_timestamp(),
                fc.IRRIGATION_10M: irrigation_value
            })
            
telemetry_handler = TelemetryHandler()