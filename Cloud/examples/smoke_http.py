"""Run explicitly: python -m Cloud.examples.smoke_http --node-id YOUR_NODE."""

import argparse
import sys

from Cloud import CloudClient, CloudConfig, CloudError


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload one set of sample sensor values to Blynk.")
    parser.add_argument("--node-id", required=True, help="The edge node represented by this Blynk device")
    args = parser.parse_args()
    client = None
    try:
        client = CloudClient(CloudConfig.from_env(), node_id=args.node_id)
        client.send_telemetry(
            node_id=args.node_id, soil_index_pct=36, temperature_c=36.5, air_humidity_pct=36,
        )
        print("Sample telemetry sent successfully!")
        return 0
    except (ValueError, CloudError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
