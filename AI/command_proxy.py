import json
import paho.mqtt.client as mqtt
from configparser import ConfigParser
from action_index import ActionIndex
from time_helper import TimeHelper


class CommandProxy:
    def __init__(self) -> None:
        self.mqtt_client: mqtt.Client | None = None

    def configure(self, client: mqtt.Client, config: ConfigParser) -> None:
        self.edge_id = config["edge"]["edge_id"].strip()
        self.command_topic = config["mqtt"]["command_topic"]
        self.command_qos = config.getint("mqtt", "command_qos")
        self.command_retain = config.getboolean("mqtt", "command_retain")
        self.max_pump_duration_ms = config.getint("pump", "max_duration_ms")
        self.mqtt_client = client

    def send_command(
        self,
        node_id: str,
        action: ActionIndex,
        ttl_ms: int = 60_000,
        duration_ms: int = 0,
    ) -> bool:
        """Queue a command on the Edge MQTT client."""
        if not isinstance(node_id, str) or not node_id.strip() or any(
            char in node_id for char in "/+#\x00"
        ):
            raise ValueError("node_id must be a non-empty MQTT topic segment")

        if not isinstance(action, ActionIndex):
            raise ValueError("action must be an ActionIndex")

        if type(ttl_ms) is not int or ttl_ms <= 0:
            raise ValueError("ttl_ms must be a positive integer")

        if type(duration_ms) is not int or duration_ms < 0:
            raise ValueError("duration_ms must be a non-negative integer")

        client = self.mqtt_client
        if client is None:
            print("Cannot send command: MQTT publisher is not configured")
            return False

        if action is ActionIndex.StartPump:
            if duration_ms == 0:
                return False
            if duration_ms > self.max_pump_duration_ms:
                raise ValueError(
                    f"duration_ms must not exceed {self.max_pump_duration_ms}"
                )

        if not client.is_connected():
            print("Cannot send command: MQTT is disconnected")
            return False

        topic = self.command_topic.format(edge_id=self.edge_id, node_id=node_id)
        payload = {
            "edge_id": self.edge_id,
            "node_id": node_id,
            "action": action.value,
            "issued_at": TimeHelper.get_current_timestamp(),
            "ttl_ms": ttl_ms,
            "duration_ms": duration_ms,
        }
        
        print(f"Sending command to topic {topic}: {payload}")

        result = client.publish(
            topic,
            json.dumps(payload),
            qos=self.command_qos,
            retain=self.command_retain,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"Could not queue command on {topic}: {mqtt.error_string(result.rc)}")
            return False

        return True


command_proxy = CommandProxy()