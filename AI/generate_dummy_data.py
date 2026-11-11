import argparse
import json
import random
from configparser import ConfigParser
from datetime import datetime, timedelta
from itertools import cycle
from pathlib import Path
from threading import Event
from time import sleep, time

import paho.mqtt.client as mqtt

from IrrigationPrediction import field_constants as fc


TEST_NODE_IDS = ("test_node_1", "test_node_2", "test_node_3", "test_node_4")


def log(message: str) -> None:
    print(f"[{datetime.now().astimezone():%Y-%m-%d %H:%M:%S %z}] {message}", flush=True)


def next_sample_slot(now: datetime) -> datetime:
    start = now.replace(minute=now.minute // 10 * 10, second=0, microsecond=0)
    return start + timedelta(minutes=10)


def send_sample(client, connected: Event, topic: str, loss_rate: float) -> None:
    if not connected.is_set():
        log("SKIPPED: MQTT broker is offline")
        return
    if random.random() < loss_rate:
        log("SKIPPED: simulated network loss")
        return

    payload = json.dumps({
        fc.SOIL_MOISTURE_PERCENTAGE: round(random.uniform(0, 100), 2),
        fc.AIR_TEMP_C: round(random.uniform(5, 20), 2),
        fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE: round(random.uniform(0, 100), 2),
    })
    result = client.publish(topic, payload, qos=1, retain=False)
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        log(f"SEND FAILED: {mqtt.error_string(result.rc)}")
        return
    try:
        result.wait_for_publish(timeout=5)
        if result.is_published():
            log(f"SENT: {topic} {payload}")
        else:
            log("UNCONFIRMED: broker did not acknowledge the sample within 5 seconds")
    except RuntimeError as error:
        log(f"SEND FAILED: {error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send simulated MQTT telemetry every 10 minutes.")
    parser.add_argument("--node-id", help="Use one node for every sample.")
    parser.add_argument("--loss-rate", type=float, default=0.0, help="Chance of dropping a sample (0 to 1).")
    args = parser.parse_args()
    if args.node_id is not None and (
        not args.node_id.strip() or any(char in args.node_id for char in "/+#\x00")
    ):
        parser.error("--node-id must be one non-empty MQTT topic segment.")
    if not 0 <= args.loss_rate <= 1:
        parser.error("--loss-rate must be between 0 and 1.")

    config = ConfigParser()
    with Path(__file__).with_name("config.cfg").open(encoding="utf-8-sig") as file:
        config.read_file(file)
    edge_id = config["edge"]["edge_id"].strip()
    if not edge_id:
        parser.error("Start app.py first so it can write the edge ID to config.cfg.")
    host = config["mqtt"]["host"]
    port = config.getint("mqtt", "port")
    topic_filter = config["mqtt"]["telemetry_filter"].format(edge_id=edge_id)
    node_ids = (args.node_id,) if args.node_id is not None else TEST_NODE_IDS
    node_rotation = cycle(node_ids)

    connected = Event()

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log(f"CONNECTION REFUSED: {reason_code}")
            return
        connected.set()
        log(f"CONNECTED: {host}:{port}")

    def on_connect_fail(client, userdata):
        log(f"OFFLINE: could not connect to {host}:{port}; retrying")

    def on_disconnect(client, userdata, flags, reason_code, properties):
        connected.clear()
        log(f"DISCONNECTED: {reason_code}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_connect_fail = on_connect_fail
    client.on_disconnect = on_disconnect
    log(f"START: broker={host}:{port}, nodes={", ".join(node_ids)}, simulated loss={args.loss_rate:.0%}")
    client.connect_async(host, port, keepalive=60)
    client.loop_start()

    try:
        while True:
            slot = next_sample_slot(datetime.now().astimezone())
            log(f"WAITING: next sample at {slot:%H:%M:%S %z}")
            while (remaining := slot.timestamp() - time()) > 0:
                sleep(min(remaining, 1))
            log(f"SAMPLE: {slot:%H:%M %z}")
            topic = topic_filter.replace("+", next(node_rotation), 1)
            send_sample(client, connected, topic, args.loss_rate)
    except KeyboardInterrupt:
        log("STOPPED")
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
