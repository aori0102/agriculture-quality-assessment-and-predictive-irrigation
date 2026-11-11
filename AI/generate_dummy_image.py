import argparse
from configparser import ConfigParser
from pathlib import Path
from threading import Event

import paho.mqtt.client as mqtt


def main() -> None:
    parser = argparse.ArgumentParser(description="Send one JPEG image via MQTT.")
    parser.add_argument("source", type=Path, help="JPEG path relative to the working directory.")
    parser.add_argument("--node-id", default="test_node_1", help="Node ID to put in the image topic.")
    args = parser.parse_args()
    if not args.node_id.strip() or any(char in args.node_id for char in "/+#\x00"):
        parser.error("--node-id must be one non-empty MQTT topic segment.")

    try:
        image = args.source.read_bytes()
    except OSError as error:
        parser.error(f"Cannot read {args.source}: {error}")
    if not image.startswith(b"\xff\xd8"):
        parser.error(f"{args.source} must be a JPEG image.")

    config = ConfigParser()
    with Path(__file__).with_name("config.cfg").open(encoding="utf-8-sig") as file:
        config.read_file(file)
    edge_id = config["edge"]["edge_id"].strip()
    if not edge_id:
        parser.error("Start app.py first so it can write the edge ID to config.cfg.")

    host = config["mqtt"]["host"]
    port = config.getint("mqtt", "port")
    image_filter = config["mqtt"]["image_filter"].format(edge_id=edge_id)
    topic = image_filter.replace("+", args.node_id, 1)

    connection_received = Event()
    connection_reason = None

    def on_connect(client, userdata, flags, reason_code, properties):
        nonlocal connection_reason
        connection_reason = reason_code
        connection_received.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    try:
        client.connect(host, port, keepalive=60)
        client.loop_start()
        if not connection_received.wait(timeout=5):
            raise SystemExit("Timed out waiting for the MQTT broker to connect.")
        if connection_reason.is_failure:
            raise SystemExit(f"MQTT connection refused: {connection_reason}")

        result = client.publish(topic, image, qos=1, retain=False)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise SystemExit(f"MQTT publish failed: {mqtt.error_string(result.rc)}")
        result.wait_for_publish(timeout=5)
        if not result.is_published():
            raise SystemExit("Timed out waiting for the broker to acknowledge the image.")
        print(f"SENT: {topic} ({len(image)} bytes)")
    except (OSError, RuntimeError) as error:
        raise SystemExit(f"MQTT error: {error}") from error
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
