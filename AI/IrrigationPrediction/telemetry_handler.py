from edge_logging import get_logger
logger = get_logger("TELEMETRY")

from cloud_reporter import cloud_reporter
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
    on_accepted = None  # Optional same-process Cloud hook; installed by launcher.

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
            logger.info(f"Empty payload on {message.topic}")
            return False
        try:
            data = json.loads(message.payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            logger.warning(f"Invalid JSON on {message.topic}: {error}")
            return False

        if not isinstance(data, dict):
            logger.warning(f"Expected a JSON object on {message.topic}")
            return False

        fields = RAW_TELEMTRY_FIELDS
        if data.get(fc.NODE_ID) != node_id:
            logger.warning(f"Payload node_id does not match topic {message.topic}")
            return False

        if not all(field in data for field in fields):
            logger.warning(f"Missing fields in {message.topic}: {data} ({[missing for missing in fields if missing not in data]})")
            return False

        raw_timestamp, soil_moisture_pct, air_temp_c, humidity_pct, quality = (data[field] for field in fields)
        if any(type(value) not in (int, float) for value in (soil_moisture_pct, air_temp_c, humidity_pct)):
            logger.warning(f"Expected numeric sensor values on {message.topic}")
            return False
        if not float('-inf') < air_temp_c < float('inf'):
            logger.warning(f"Invalid air temperature on {message.topic}: {air_temp_c}")
            return False
    
        # Process data
        timestamp = TimeHelper.process_time(raw_timestamp, require_timezone=True)
        if not timestamp:
            logger.warning(f"Invalid timestamp format on {message.topic}: {raw_timestamp}")
            return False
        if fc.SAMPLED_AT in data and timestamp.tzinfo is None:
            logger.warning(f"Expected a timezone in sampled_at on {message.topic}")
            return False
        timestamp = TimeHelper.floor_to_nearest_ten_minutes(timestamp.astimezone(timezone.utc))
        
        if not isinstance(quality, dict):
            return False

        if "soil" not in quality or quality["soil"] == "invalid":
            logger.warning(f"Invalid or missing soil moisture sensor reading quality.")
            return False
        
        if "dht" not in quality or quality["dht"] == "invalid":
            logger.warning(f"Invalid or missing DHT sensor reading quality.")
            return False

        if not 0 <= soil_moisture_pct <= 100:
            logger.warning(f"Invalid soil moisture percentage on {message.topic}: {soil_moisture_pct}")
            return False
        
        if not 0 <= humidity_pct <= 100:
            logger.warning(f"Invalid air relative humidity percentage on {message.topic}: {humidity_pct}")
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
        logger.info("ACCEPTED node=%s soil=%.1f%% temp=%.1fC humidity=%.1f%% sampled_at=%s",
                    node_id, soil_moisture_pct, air_temp_c, humidity_pct, timestamp.isoformat())
        snapshot = {
            "soil_index_pct": soil_moisture_pct,
            "temperature_c": air_temp_c,
            "air_humidity_pct": humidity_pct,
        }
        if self.on_accepted is not None:
            try:
                self.on_accepted(node_id, snapshot)
            except Exception:
                # Cloud failures must not invalidate accepted Edge telemetry.
                pass
        else:
            cloud_reporter.submit("send_telemetry", node_id=node_id, **snapshot)
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
