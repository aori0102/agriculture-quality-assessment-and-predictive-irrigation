import json
import paho.mqtt.client as mqtt
from concurrent.futures import ThreadPoolExecutor
from configparser import ConfigParser
from pathlib import Path
from time import sleep
from uuid import uuid4
from ComputerVision.capture_handler import capture_handler
from IrrigationPrediction.telemetry_handler import telemetry_handler
from IrrigationPrediction.irrigation_handler import irrigation_handler
from command_proxy import command_proxy
from IrrigationPrediction.model_retrainer import ModelRetrainer
from IrrigationPrediction.irrigation_data_handler import irrigation_data_handler
from operation_log_manager import operation_log_manager

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


def handle_image(node_id: str, jpeg: bytes) -> None:
    try:
        from ComputerVision.image_prediction import process_image

        process_image(node_id, jpeg)
    except Exception as error:
        print(f"Could not process image from {node_id}: {error}")


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
    image_filter = cfg["mqtt"]["image_filter"].format(edge_id=edge_id)
    test_topic = topic_filter.replace("+", "test_node", 1)
    test_sent = False
    ready = False
    subscription_mid = None
    image_future = None
    retrainer = ModelRetrainer(
        operation_log_manager, irrigation_handler, irrigation_data_handler.data_path,
        debug_hourly=cfg.getboolean("retrain", "debug_hourly", fallback=False),
    )
    image_worker = ThreadPoolExecutor(max_workers=1)

    def on_telemetry(client, userdata, message: mqtt.MQTTMessage):
        if ready:
            on_message(client, userdata, message)

    def on_image(client, userdata, message: mqtt.MQTTMessage):
        nonlocal image_future
        if not ready or retrainer.waiting_for_swap:
            return
        node_id = strip_topic_to_node_id(message.topic, "image")
        if not node_id:
            print(f"Could not extract node ID from {message.topic}")
            return
        if image_future is not None and not image_future.done():
            print(f"Skipping image from {node_id}: image worker is busy")
            return
        image_future = image_worker.submit(handle_image, node_id, bytes(message.payload))

    def on_connect(client, userdata, flags, reason_code, properties):
        nonlocal ready, subscription_mid
        ready = False
        subscription_mid = None
        if reason_code.is_failure:
            print(f"Connection refused: {reason_code}")
            return

        print(f"Connected to {host}:{port}")
        result, mid = client.subscribe([(topic_filter, 1), (image_filter, 1)])
        if result != mqtt.MQTT_ERR_SUCCESS:
            print(f"Could not request subscriptions: {result}")
            return
        subscription_mid = mid

    def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
        nonlocal ready, subscription_mid
        ready = False
        subscription_mid = None

    def on_subscribe(client, userdata, mid, reason_codes, properties):
        nonlocal test_sent, ready, subscription_mid

        if mid != subscription_mid:
            return
        subscription_mid = None
        if len(reason_codes) != 2 or any(code.is_failure for code in reason_codes):
            print(f"Subscription rejected: {reason_codes}")
            return

        ready = True
        print(f"Edge AI ready. Subscribed to {topic_filter} and {image_filter}")
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
    client.on_disconnect = on_disconnect
    client.message_callback_add(topic_filter, on_telemetry)
    client.message_callback_add(image_filter, on_image)
    irrigation_handler.configure_command_publisher(client, cfg)
    command_proxy.configure(client, cfg)

    print(f"Connecting to {host}:{port}...")
    need_connection = True
    try:
        while True:
            if need_connection:
                try:
                    result = client.connect(host, port, keepalive=60)
                except OSError as error:
                    print(f"MQTT connection failed: {error}")
                    sleep(1)
                    continue
                if result != mqtt.MQTT_ERR_SUCCESS:
                    print(f"Could not start MQTT connection: {result}")
                    sleep(1)
                    continue
                need_connection = False

            result = client.loop(timeout=1.0)
            if result != mqtt.MQTT_ERR_SUCCESS:
                ready = False
                subscription_mid = None
                need_connection = True
                sleep(1)
                continue

            if ready and client.is_connected():
                if retrainer.tick(can_swap=image_future is None or image_future.done()):
                    continue
                irrigation_handler.loop()
                capture_handler.loop()
    except KeyboardInterrupt:
        print("Stopping Edge AI.")
    finally:
        try:
            client.disconnect()
        finally:
            try:
                retrainer.close()
            finally:
                image_worker.shutdown()


if __name__ == "__main__":
    main()