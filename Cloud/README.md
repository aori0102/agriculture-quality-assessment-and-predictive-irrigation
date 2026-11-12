# Cloud: edge-to-Blynk communication

`Cloud` runs inside the edge Python process. It uploads current measurements,
AI results, and confirmed pump state over HTTPS, and receives dashboard pump
requests over MQTT/TLS. Importing the package or constructing a client does not
connect to Blynk. AI inference, irrigation policy, local MQTT, and hardware
actuation remain the edge application's responsibilities.

## Setup

From the repository root, using the project's virtual environment:

```bash
python -m pip install -r Cloud/requirements.txt
```

Create a root `.env` using `.env.example` as a reference. Set `BLYNK_SERVER` to
the device's regional hostname (for example, `sgp1.blynk.cloud`, without a URL
scheme or path) and `BLYNK_AUTH_TOKEN` to its device token. Existing environment
variables override `.env` values, including an explicitly empty value, which
fails validation. Configuration lookup does not depend on the working directory
and does not modify the process environment.

Use one client for one fixed node and one Blynk device. Every upload requires
the same `node_id`; it is checked locally, not transmitted to Blynk. The edge
must filter incoming sensor readings by node before calling the client. Never
run multiple MQTT clients with the same token simultaneously.

## Public interface

See the [API reference](API.md) for complete signatures, parameter rules,
return values, error categories, and command-handling examples.

```python
from dataclasses import replace
from Cloud import CloudClient, CloudConfig, CloudError, PumpCommand

config = replace(CloudConfig.from_env(), http_timeout=10, command_lifetime=5)
cloud = CloudClient(config, node_id="node-1")
try:
    cloud.send_snapshot(
        node_id="node-1",
        soil_index_pct=42.5,
        temperature_c=28.5,
        air_humidity_pct=65,
        plant_status="Healthy",
        predicted_moisture_pct=40.5,
    )
    # Start background MQTT reception only when commands are needed.
    cloud.start()
    # Your application now continues its own processing loop.
finally:
    cloud.close()
```

| Method/property | Contract |
| --- | --- |
| `send_snapshot(*, node_id, soil_index_pct=None, temperature_c=None, air_humidity_pct=None, plant_status=None, predicted_moisture_pct=None, pump_is_on=None)` | One batch containing supplied fields. `pump_is_on` must be physically confirmed. |
| `send_telemetry(*, node_id, soil_index_pct=None, temperature_c=None, air_humidity_pct=None)` | Sensor-only convenience method. |
| `send_ai_results(*, node_id, plant_status=None, predicted_moisture_pct=None)` | AI-output convenience method. |
| `report_pump_status(*, node_id, is_on)` | Report a boolean state confirmed by the device. |
| `start()` | Start MQTT asynchronously; repeated calls do nothing. |
| `commands_ready` | True after connection and accepted command subscription. |
| `last_command_error` | Sanitized `CloudError`, or `None`; cleared after a successful subscription. |
| `poll_command()` | Immediately consume the latest unexpired request, or return `None`. |
| `close()` | Stop reception and release resources; repeated calls do nothing. |

Upload methods return `None` on success and raise `CloudError` on network/API
failure. The exception provides `category` and optional HTTP `status_code`.
Categories are `http`, `redirect`, `timeout`, `network`, `authentication`,
`connection`, `subscription`, `startup`, and `closed`. Invalid inputs raise
`ValueError`. Starting or uploading after closing is an error; polling after
closing returns `None`. Create a new client to restart after closure or a
terminal MQTT error.

`CloudConfig` is immutable. Defaults are HTTP timeout 10 seconds, command lifetime
5 seconds, MQTT TLS port 8883, and keepalive 45 seconds. Override timeout and
lifetime in the constructor or with `dataclasses.replace`. Both must be positive
and finite, and command expiry must fit Python's datetime range. The HTTP timeout
also bounds MQTT connection establishment. The
public Blynk regional hostname and TLS port are validated; custom brokers are
outside this package's scope.

Uploads are synchronous and serialized. Schedule them outside MQTT callbacks
and time-sensitive edge work, using the caller's worker if necessary. Select an
upload interval that fits your Blynk message quota and batch values from the
same processing cycle. There is no automatic retry, offline buffer, scheduler,
or historical backfill. The edge keeps original sample timestamps and decides
which current readings are valid to upload. `None` readings are omitted; an
empty snapshot sends nothing. Closing waits for any active upload to finish.

## Datastream contract

| Pin | Blynk datastream name | Upload/input type |
| --- | --- | --- |
| V0 | `soil_index_pct` | Percentage, converted to integer |
| V1 | `temperature_c` | Finite number, sent as float |
| V2 | `Plant Status` | String supplied by the edge |
| V3 | `Pump Command` | Inbound `0`/`1`; never uploaded by this package |
| V4 | `Pump Status` | Confirmed boolean, uploaded as `0`/`1` |
| V5 | `Predicted Moisture` | Percentage, converted to integer |
| V6 | `air_humidity_pct` | Percentage, converted to integer |

Percentages must be within 0–100 before rounding to the nearest integer, with
halves rounded upward. Booleans are not measurements, and NaN/infinity are
rejected. Missing readings never become fabricated zeros. Dashboard temperature
ranges and string limits must accommodate the actual application values;
Blynk rejections surface as HTTP errors.

The HTTP adapter uses the verified
[HTTPS batch API](https://docs.blynk.io/en/blynk.cloud/device-https-api/update-multiple-datastreams-api).
It never follows redirects automatically. A redirect means `BLYNK_SERVER` needs
the correct regional hostname. Errors omit request URLs and credentials.

## Commands: intent versus confirmed execution

The MQTT adapter uses [Blynk Device MQTT authentication](https://docs.blynk.io/en/blynk.cloud-mqtt-api/device-mqtt-api/authentication)
over verified TLS, with a clean session and QoS 1 subscription to
`downlink/ds/Pump Command`. MQTT uses the exact datastream **name**, including
spaces and capitalization, rather than virtual pin `v3`.

The pending slot holds the latest requested state only. ON then OFF before a
poll produces OFF; superseded transitions are not replayed. Valid payloads are
UTF-8 `0` and `1` with optional surrounding whitespace. Retained messages and
malformed commands are ignored. No stored command state is requested on startup
or reconnect. Disconnection clears the pending slot, and offline commands are
not guaranteed to arrive later.

`PumpCommand` is immutable and contains `node_id`, `requested_on`, UTC
`received_at`, UTC `expires_at`, and `is_expired()`. Expiry uses a monotonic clock
and must be checked again immediately before acting, because a request may
expire after polling. The five-second default lifetime starts at local receipt;
Blynk's scalar payload has no user-issued timestamp or command ID, so this is
not a guarantee of server-side freshness or exactly-once delivery. The object
is intended for use within this process, not persistence or cross-process replay.

In a real edge application, the following are integration responsibilities,
not functions supplied by Cloud:

```python
# Inside the edge's existing loop, using its own controller:
command = cloud.poll_command()
if command is not None and not command.is_expired():
    controller.consider_request(command)  # Apply policy; recheck expiry before actuation.

# Separately, when the device reports its actual state:
cloud.report_pump_status(node_id=confirmed_node_id, is_on=confirmed_state)
```

The controller owns authorization, safe duration limits, duplicate-safe handling,
local MQTT delivery, and acknowledgement/timeout handling. Repeated ON requests
must not automatically restart or extend irrigation. Neither receiving a
command nor obtaining an MQTT PUBACK confirms physical pump execution. Never
derive V4 directly from V3. An already-polled command cannot be recalled by
Cloud on a later disconnect; the edge must apply its own execution policy.

Transient MQTT failures retry with exponential backoff from 1 to 30 seconds and
resubscribe after reconnect. Authentication, subscription, and redirect failures
stop retries and are exposed through `last_command_error`. Correct configuration
and create a new client after closing the old one. Normal callbacks only validate
and store requests; they never run inference, HTTP uploads, or hardware control.

## Verification and live examples

Run offline tests (no credentials, broker, or hardware needed):

```bash
python -m unittest discover -s Cloud/tests -v
```

The former manual `tests/test_blynk.py` is now an explicitly invoked example,
excluded from automated test discovery. These commands **do send sample values
to your configured Blynk device**:

```bash
python -m Cloud.examples.smoke_http --node-id node-1
python -m Cloud.examples.live_demo --node-id node-1 --duration 60
```

The smoke example uploads one sensor batch without starting MQTT. The live demo
uploads a sample snapshot, listens for dashboard pump requests, and prints them.
It never controls hardware or writes confirmed pump status. Stop any other MQTT
client using this token before running the demo. Toggle the V3 switch after it
reports that its subscription is ready.

Automated tests cover HTTP and MQTT through mocked transport boundaries. A live
Blynk dashboard check and end-to-end hardware acknowledgements are separate
integration checks; passing the offline suite does not establish either.
