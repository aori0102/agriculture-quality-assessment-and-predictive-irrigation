import json
import paho.mqtt.client as mqtt
from configparser import ConfigParser
from pathlib import Path
from time import sleep
from uuid import uuid4
from IrrigationPrediction.telemetry_handler import telemetry_handler
from IrrigationPrediction.irrigation_handler import irrigation_handler

CONFIG_FILE = "config.cfg"

def strip_topic_to_node_id(topic: str, expected_topic_suffix: str = None) -> str:
    """
    Extract the node ID from the MQTT topic string.
    The node ID is expected to be the last segment of the topic after the last '/' character.
    
    Args:
        topic (str): The MQTT topic string.
        expected_topic_suffix (str, optional): If provided, the function will only return
            the node ID if the last segment of the topic matches this suffix. Defaults to None.
        
    Returns:
        str: The extracted node ID.
    """
    parts = topic.rsplit("/")
    if len(parts) < 2:
        return None
    
    if expected_topic_suffix and parts[-1] != expected_topic_suffix:
        return None

    return parts[-2]


def on_message(client, userdata, message: mqtt.MQTTMessage):
    """
    Callback function for handling incoming MQTT messages. This function attempts to strip the
    node ID from the topic and parse the message payload as JSON. If successful, it records
    the telemetry data using the telemetry_handler.
    """
    try:
        data = json.loads(message.payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        print(f"Invalid JSON on {message.topic}: {error}")
        return

    print(f"Received message on topic {message.topic}: {data}")
    node_id = strip_topic_to_node_id(message.topic, "telemetry")
    if not node_id:
        print(f"Could not extract node ID from topic {message.topic}")
        return
    
    telemetry_handler.record_sensors_telemetry(message, node_id=node_id)


def main():
    cfg_path = Path(__file__).with_name(CONFIG_FILE)
    cfg = ConfigParser()

    with cfg_path.open(encoding="utf-8-sig") as file:
        cfg.read_file(file)

    edge_id = cfg["edge"]["edge_id"].strip()
    if not edge_id:
        edge_id = str(uuid4())
        cfg["edge"]["edge_id"] = edge_id
        with cfg_path.open("w", encoding="utf-8") as file:
            cfg.write(file)
            
    print(f"Edge UUID: {edge_id}")

    host = cfg["mqtt"]["host"]
    port = cfg.getint("mqtt", "port")
    topic_filter = cfg["mqtt"]["telemetry_filter"].format(edge_id=edge_id)
    test_topic = topic_filter.replace("+", "test_node", 1)
    test_sent = False

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"Connection refused: {reason_code}")
            return

        print(f"Connected to {host}:{port}")
        client.subscribe(topic_filter, qos=1)

    def on_subscribe(client, userdata, mid, reason_codes, properties):
        nonlocal test_sent

        if any(code.is_failure for code in reason_codes):
            print(f"Subscription rejected: {reason_codes}")
            return

        print(f"Subscribed to {topic_filter}")
        if not test_sent:
            result = client.publish(
                test_topic,
                payload=json.dumps({"message": "Hello from the edge!"}),
                qos=1,
                retain=False,
            )
            test_sent = result.rc == mqtt.MQTT_ERR_SUCCESS
            if not test_sent:
                print(f"Could not queue test message: {result.rc}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    print(f"Connecting to {host}:{port}...")
    client.connect(host, port, keepalive=60)
    try:
        while True:
            result = client.loop(timeout=1.0)
            irrigation_handler.loop()
            if result != mqtt.MQTT_ERR_SUCCESS:
                sleep(1)
                try:
                    client.reconnect()
                except OSError as error:
                    print(f"MQTT reconnect failed: {error}")
    except KeyboardInterrupt:
        print("Stopping Edge AI.")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()