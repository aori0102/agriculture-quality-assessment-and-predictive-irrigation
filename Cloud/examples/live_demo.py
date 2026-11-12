"""Upload sample readings and print dashboard intent without controlling hardware."""

import argparse
import math
import sys
from time import monotonic, sleep

from Cloud import CloudClient, CloudConfig, CloudError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--duration", type=float, default=60, help="Seconds to listen (default: 60)")
    args = parser.parse_args()
    if not 0 < args.duration < math.inf:
        parser.error("--duration must be a positive finite number")

    client = None
    try:
        client = CloudClient(CloudConfig.from_env(), node_id=args.node_id)
        client.send_snapshot(
            node_id=args.node_id, soil_index_pct=42.5, temperature_c=28.5,
            air_humidity_pct=65, plant_status="Demo sample", predicted_moisture_pct=40.5,
        )
        print("Sample readings uploaded. Listening for pump requests; no hardware will be controlled.")
        client.start()
        deadline = monotonic() + args.duration
        last_error = None
        was_ready = False
        while monotonic() < deadline:
            if client.commands_ready and not was_ready:
                print("Command subscription ready. Toggle Pump Command (V3) in Blynk.")
            was_ready = client.commands_ready
            error = client.last_command_error
            if error is not None and str(error) != last_error:
                print(error, file=sys.stderr)
            last_error = str(error) if error else None
            if error is not None and error.category != "network":
                return 1
            command = client.poll_command()
            if command is not None and not command.is_expired():
                state = "ON" if command.requested_on else "OFF"
                print(f"Request for {command.node_id}: {state}; expires at {command.expires_at.isoformat()}")
            sleep(0.1)
        return 0 if client.commands_ready else 1
    except KeyboardInterrupt:
        return 0
    except (ValueError, CloudError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
