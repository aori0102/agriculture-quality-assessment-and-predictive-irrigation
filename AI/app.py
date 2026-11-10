import json
from configparser import ConfigParser
from pathlib import Path
from uuid import uuid4
from IrrigationPrediction.telemetry_handler import telemetry_handler
from IrrigationPrediction.irrigation_handler import irrigation_handler

import paho.mqtt.client as mqtt

CONFIG_FILE = "config.cfg"


def on_message(client, userdata, message: mqtt.MQTTMessage):
    try:
        data = json.loads(message.payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        print(f"Invalid JSON on {message.topic}: {error}")
        return

    print(f"Received message on topic {message.topic}: {data}")
    telemetry_handler.record_sensors_telemetry(message)
    irrigation_handler.handle_irrigation()


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
    client.loop_forever()


if __name__ == "__main__":
    main()