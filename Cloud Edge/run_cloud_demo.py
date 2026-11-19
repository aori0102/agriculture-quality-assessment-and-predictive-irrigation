"""Independent Blynk demo: fake sensors, fake AI and a simulated pump only.

From Cloud Edge/: python -m run_cloud_demo
Uses repository-root .env; never imports AI/app.py or contacts the node MQTT broker.
Stop the main Edge process using the same Blynk device before running this demo.
"""
import argparse
import math
import sys
from time import monotonic, sleep

from cloud_edge import CloudClient, CloudError, load_device_bindings
from cloud_edge.config import ENV_FILE


def fake_snapshot(step: int, pump_is_on: bool = False) -> dict:
    """Generate repeatable dummy values in the dashboard's expected ranges."""
    phase = step / 3
    soil = round(45 + 10 * math.sin(phase), 1)
    return dict(
        soil_index_pct=soil,
        temperature_c=round(28 + 3 * math.sin(phase / 2), 1),
        air_humidity_pct=round(65 + 10 * math.cos(phase), 1),
        plant_status="SIMULATED: Healthy" if step % 6 < 4 else "SIMULATED: Inspect leaves",
        predicted_moisture_pct=round(max(0, min(100, soil + (4 if pump_is_on else -2))), 1),
        pump_is_on=pump_is_on,
    )


def run_demo(binding, *, duration=120.0, interval=5.0, client_factory=CloudClient,
             clock=monotonic, pause=sleep, require_command=False) -> int:
    client = client_factory(binding.config, node_id=binding.node_id)
    uploaded = received = failures = 0
    ready_once = False
    pump_is_on = False
    interrupted = False
    try:
        client.start()
        started = clock()
        next_upload = started
        print(f"SIMULATION ONLY | node={binding.node_id} | cloud={binding.config.server}")
        print("Dummy readings update V0/V1/V2/V5/V6; V4 is a SIMULATED pump.")
        print("Wait for MQTT READY, then toggle pump_command (V3) on Blynk to test downlink.")
        last_error = None
        while duration == 0 or clock() - started < duration:
            if client.commands_ready and not ready_once:
                ready_once = True
                print("[MQTT READY] Subscribed to Cloud commands.")
            error = client.last_command_error
            category = error.category if error is not None else None
            if category is not None and category != last_error:
                print(f"[MQTT ERROR] category={category}", file=sys.stderr)
            last_error = category
            if category is not None and category != "network":
                failures += 1
                break
            command = client.poll_command()
            if command is not None and not command.is_expired():
                received += 1
                pump_is_on = command.requested_on
                print(f"[CLOUD -> DEMO] V3={'ON' if pump_is_on else 'OFF'}; simulated pump state updated.")
                try:
                    client.report_pump_status(node_id=binding.node_id, is_on=pump_is_on)
                    print(f"[DEMO -> CLOUD] V4={int(pump_is_on)} (simulated confirmation)")
                except CloudError as error:
                    failures += 1
                    print(f"[HTTP ERROR] pump status category={error.category}; status={error.status_code}", file=sys.stderr)
            now = clock()
            if now >= next_upload:
                values = fake_snapshot(uploaded, pump_is_on)
                try:
                    client.send_snapshot(node_id=binding.node_id, **values)
                    uploaded += 1
                    print(f"[DEMO -> CLOUD #{uploaded}] {values}")
                except CloudError as error:
                    failures += 1
                    print(f"[HTTP ERROR] snapshot category={error.category}; status={error.status_code}", file=sys.stderr)
                # Schedule from completion; a slow HTTP call must not cause a burst.
                next_upload = clock() + interval
            pause(0.1)
    except KeyboardInterrupt:
        interrupted = True
        print("Stopped by user.")
    except CloudError as error:
        failures += 1
        print(f"[CLOUD ERROR] category={error.category}; status={error.status_code}", file=sys.stderr)
    finally:
        client.close()
        print(f"Summary: uploaded={uploaded}, commands_received={received}, failures={failures}")
        if received == 0:
            print("Downlink not verified: no unexpired V3 command received during this run.")
    if interrupted:
        return 0
    return 0 if uploaded and ready_once and not failures and (received or not require_command) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-id", help="Node from BLYNK_DEVICES_JSON (default: first configured node)")
    parser.add_argument("--duration", type=float, default=120, help="Seconds to run; 0 means until Ctrl+C (default: 120)")
    parser.add_argument("--interval", type=float, default=5, help="Seconds between snapshots (default: 5)")
    parser.add_argument("--require-command", action="store_true", help="Exit with failure unless a valid V3 command is received")
    parser.add_argument("--dry-run", action="store_true", help="Print three dummy snapshots; no config or network needed")
    args = parser.parse_args(argv)
    if not math.isfinite(args.duration) or args.duration < 0:
        parser.error("--duration must be finite and >= 0")
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval must be finite and > 0")
    if args.dry_run:
        for step in range(3):
            print(fake_snapshot(step))
        return 0
    try:
        bindings = load_device_bindings()
        binding = next((b for b in bindings if b.node_id == args.node_id), None) if args.node_id else bindings[0]
        if binding is None:
            print("Node not found in BLYNK_DEVICES_JSON.", file=sys.stderr)
            return 2
        return run_demo(binding, duration=args.duration, interval=args.interval,
                        require_command=args.require_command)
    except ValueError:
        print(f"Invalid Cloud configuration. Edit BLYNK_DEVICES_JSON, server and referenced tokens in {ENV_FILE}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
