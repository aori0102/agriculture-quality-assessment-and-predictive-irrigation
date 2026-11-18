import json
import paho.mqtt.client as mqtt
from .irrigation_data_handler import irrigation_data_handler
from operation_log_manager import operation_log_manager
from . import field_constants as fc
from time_helper import TimeHelper
from datetime import timezone, datetime

RAW_TELEMTRY_FIELDS = (
    fc.SAMPLED_AT,
    fc.SOIL_INDEX_PCT,
    fc.TEMPERATURE_C,
    fc.AIR_HUMIDITY_PCT,
    fc.QUALITY
)


class TelemetryHandler:
    def record_sensors_telemetry(self, message: mqtt.MQTTMessage, node_id: str) -> bool:
        """
        Record the sensor data from the MQTT message payload into data_log.csv
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

        fields = RAW_TELEMTRY_FIELDS
        if data.get(fc.NODE_ID) != node_id:
            print(f"Payload node_id does not match topic {message.topic}")
            return False

        if not all(field in data for field in fields):
            print(f"Missing fields in {message.topic}: {data} ({[missing for missing in fields if missing not in data]})")
            return False

        raw_timestamp, soil_moisture_pct, air_temp_c, humidity_pct, quality = (data[field] for field in fields)
        if any(type(value) not in (int, float) for value in (soil_moisture_pct, air_temp_c, humidity_pct)):
            print(f"Expected numeric sensor values on {message.topic}")
            return False
        if not float('-inf') < air_temp_c < float('inf'):
            print(f"Invalid air temperature on {message.topic}: {air_temp_c}")
            return False
    
        # Process data
        timestamp = TimeHelper.process_time(raw_timestamp, require_timezone=True)
        if not timestamp:
            print(f"Invalid timestamp format on {message.topic}: {raw_timestamp}")
            return False
        if fc.SAMPLED_AT in data and timestamp.tzinfo is None:
            print(f"Expected a timezone in sampled_at on {message.topic}")
            return False
        timestamp = TimeHelper.floor_to_nearest_ten_minutes(timestamp.astimezone(timezone.utc))
        
        if "soil" not in quality or quality["soil"] == "invalid":
            print(f"Invalid or missing soil moisture sensor reading quality.")
            return False
        
        if "dht" not in quality or quality["dht"] == "invalid":
            print(f"Invalid or missing DHT sensor reading quality.")
            return False

        if not 0 <= soil_moisture_pct <= 100:
            print(f"Invalid soil moisture percentage on {message.topic}: {soil_moisture_pct}")
            return False
        
        if not 0 <= humidity_pct <= 100:
            print(f"Invalid air relative humidity percentage on {message.topic}: {humidity_pct}")
            return False
        
        if not irrigation_data_handler.log_data(
            timestamp=timestamp,
            node_id=node_id,
            soil_moisture=soil_moisture_pct,
            weather_temperature=air_temp_c,
            weather_humidity=humidity_pct,
        ):
            return False

        operation_log_manager.log_node(node_id)  # Record the node ID as acquired
        return True
    

    def record_irrigation_data(self, irrigation_value: float, node_id: str, timestamp: datetime) -> bool:
        """
        Record the irrigation duration for the node and window in data_log.csv.
        
        Args:
            irrigation_value (float): The irrigation duration in minutes.
            node_id (str): The ID of the node for which the irrigation data is recorded.
            timestamp (datetime): The timestamp of the irrigation data.
        """
        return irrigation_data_handler.log_data(timestamp=timestamp, node_id=node_id, irrigation_duration=irrigation_value)
            
telemetry_handler = TelemetryHandler()