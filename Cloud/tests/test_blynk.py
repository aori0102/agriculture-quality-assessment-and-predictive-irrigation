"""Send one set of simulated sensor readings using the project-root .env."""

import os
from pathlib import Path
import sys

import requests
from dotenv import load_dotenv


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    server = os.getenv("BLYNK_SERVER", "").strip()
    token = os.getenv("BLYNK_AUTH_TOKEN", "").strip()
    if not server or not token:
        print(
            "Error: Set BLYNK_SERVER and BLYNK_AUTH_TOKEN in the project-root "
            ".env or environment. BLYNK_SERVER should be a hostname "
            "such as sgp1.blynk.cloud.",
            file=sys.stderr,
        )
        return 1

    # Dữ liệu cảm biến giả lập
    params = {
        "token": token,
        "v0": 36,  # Soil moisture (%)
        "v1": 36.5,  # Temperature (°C)
        "v6": 36,  # Air humidity (%)
    }

    # Gửi dữ liệu lên Blynk Cloud
    url = f"https://{server}/external/api/batch/update"
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
    except requests.HTTPError as error:
        # Request exception text can contain the auth token in the URL.
        print(
            f"Error: Blynk returned HTTP {error.response.status_code}. "
            "Check the server, auth token, and datastream configuration.",
            file=sys.stderr,
        )
        return 1
    except requests.RequestException as error:
        print(f"Error: Blynk request failed ({type(error).__name__}).", file=sys.stderr)
        return 1

    print("Telemetry sent successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
