# Cloud Edge: Blynk communication and AI adapter

`Cloud` runs inside the edge Python process. It uploads current measurements,
AI results, and confirmed pump state over HTTPS, and receives dashboard pump
requests over MQTT/TLS. Importing the package or constructing a client does not
connect to Blynk. AI inference, irrigation policy, local MQTT, and hardware
actuation remain the edge application's responsibilities.

Plant profile synchronization is available through an optional Edge-supplied
backend. See [Plant profiles and the AI handoff](PROFILES.md) for V7–V16 setup,
Preset/Custom editing, multi-device bindings, and the persistence contract.
Cloud Edge includes an adapter to the existing AI proxy; it does not change irrigation policy.

## Setup

For optional open Node enrollment, see [Dynamic provisioning MVP](docs/DYNAMIC_PROVISIONING.md). It uses the independent Python MCP OAuth client and a separate private device store.

From the repository root, using the project's virtual environment:

```bash
python -m pip install -r "Cloud Edge/requirements.txt"
```

Create a root `.env` using `.env.example` as a reference. The combined launcher
uses `BLYNK_DEVICES_JSON` with a regional host and referenced token variable for
each static node. Dynamic-only mode uses `BLYNK_DEVICES_JSON='[]'` and an
operator-selected regional host; hybrid mode keeps the static
entries while enabling dynamic provisioning. `CloudConfig.from_env()` still
supports a standalone single client through `BLYNK_SERVER` and
`BLYNK_AUTH_TOKEN`. Existing environment variables override `.env` values.
Configuration lookup does not depend on the working directory and does not
modify the process environment.

With `BLYNK_PROVISIONING_ENABLED=1`, every Node whose telemetry Edge accepts
and whose ID matches the provisioning format is eligible for automatic Blynk
Device creation. Enrollment does not authenticate the MQTT sender; a publisher
able to impersonate a Node on the configured broker can trigger creation.

Use one client for one fixed node and one Blynk device. Every upload requires
the same `node_id`; it is checked locally, not transmitted to Blynk. The edge
must filter incoming sensor readings by node before calling the client. Never
run multiple MQTT clients with the same token simultaneously.

## Public interface

See the [API reference](API.md) for complete signatures, parameter rules,
return values, error categories, and command-handling examples.

```python
from dataclasses import replace
from cloud_edge import CloudClient, CloudConfig, CloudError, PumpCommand

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
| `send_snapshot(*, node_id, soil_index_pct=None, temperature_c=None, air_humidity_pct=None, plant_status=None, predicted_moisture_pct=None, pump_is_on=None)` | One batch containing supplied fields. `pump_is_on` requires a firmware state event. |
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
same processing cycle. Telemetry uploads have no automatic retry, offline buffer, scheduler,
or historical backfill. The edge keeps original sample timestamps and decides
which current readings are valid to upload. `None` readings are omitted; an
empty snapshot sends nothing. Closing waits for any active upload to finish.

## Datastream contract

| Pin | Blynk datastream name | Upload/input type |
| --- | --- | --- |
| V0 | `soil_moisture` | Percentage, converted to integer |
| V1 | `weather_temp` | Finite number, sent as float |
| V2 | `plant_status` | String supplied by the edge |
| V3 | `pump_command` | Inbound `0`/`1`; never uploaded by this package |
| V4 | `pump_status` | Firmware-confirmed controller state, uploaded as `0`/`1` |
| V5 | `predicted_moisture` | Percentage, converted to integer |
| V6 | `weather_humidity` | Percentage, converted to integer |
| V17 | `auto_irrigation_enabled` | Inbound Auto switch, `0`/`1`; Edge also publishes saved mode |
| V18 | `pump_status_detail` | Pending, confirmed, or unknown text from Edge |
| V19 | `node_catalog` | Read-only JSON list of registered Blynk-bound nodes for the profile widget |

These names match the current BTL template export. The Edge-facing Python
arguments remain `soil_index_pct`, `temperature_c`, `air_humidity_pct`, and
`predicted_moisture_pct`; HTTPS uploads still address the same virtual pins.

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
`downlink/ds/pump_command`. MQTT uses the exact datastream **name**, including
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

For a guided test of **all V0–V16** with a fake Edge dataset, an in-memory
profile backend, and explicit simulated pump acknowledgements, follow
[the live-test runbook](LIVE_TEST.md). Launch it explicitly with:

```bash
PYTHONPATH="Cloud Edge" python -m examples.simulated_edge --node-id demo-node
```

This writes simulated values to the configured test device. It does not import
the AI application or connect to hardware. Dashboard observations are recorded
manually; offline test success alone does not establish live coverage.

Run offline tests (no credentials, broker, or hardware needed):

```bash
PYTHONPATH="Cloud Edge" python -m unittest discover -s "Cloud Edge/tests" -t "Cloud Edge" -v
```

The former manual `tests/test_blynk.py` is now an explicitly invoked example,
excluded from automated test discovery. These commands **do send sample values
to your configured Blynk device**:

```bash
PYTHONPATH="Cloud Edge" python -m examples.smoke_http --node-id node-1
PYTHONPATH="Cloud Edge" python -m examples.live_demo --node-id node-1 --duration 60
```

The smoke example uploads one sensor batch without starting MQTT. The live demo
uploads a sample snapshot, listens for dashboard pump requests, and prints them.
It never controls hardware or writes confirmed pump status. Stop any other MQTT
client using this token before running the demo. Toggle the V3 switch after it
reports that its subscription is ready.

Automated tests cover HTTP and MQTT through mocked transport boundaries. A live
Blynk dashboard check and end-to-end hardware acknowledgements are separate
integration checks; passing the offline suite does not establish either.

## Run with the existing AI application

```bash
python "Cloud Edge/run_edge.py" --check
python "Cloud Edge/run_edge.py"
```

See [PROFILES.md](PROFILES.md) for the existing AI configuration prerequisite,
saved-versus-active semantics, metadata storage, and the adapter API.

The launcher routes V3 and V17 requests through the Edge pump coordinator.
See [irrigation control and confirmation](PUMP_COMMANDS.md) for the mode,
firmware event, acknowledgement, and timeout behavior.

## Outbound data and device events

The combined launcher now forwards accepted telemetry, AI results and confirmed pump events using a background HTTP worker. See [Cloud uploads and events](../AI/CLOUD_UPLOADS.md) for routing, configuration and verification details.


## First-run configuration template

If the repository-root `.env` is missing, `python -m run_edge` creates a placeholder
file and prints its absolute path, then exits before AI initialization or network
connections. Edit the node IDs, Blynk regional hosts and referenced Auth Token
variables, then run the command again. Existing `.env` files are never overwritten.
`--check` performs the same first-run template creation. Invalid existing settings
report the configuration path for correction.

## Standalone Cloud end-to-end demo

From `Cloud Edge/` with the shared virtual environment activated:

```powershell
python -m run_cloud_demo
python -m run_cloud_demo --node-id esp01 --duration 180 --require-command
python -m run_cloud_demo --dry-run
```

This independent script uses the first device from repository-root `.env` by default.
It sends synthetic sensor/AI values every 5 seconds for 120 seconds. Use `--interval`
to change cadence and `--duration 0` to keep running until Ctrl+C.
After `[MQTT READY]`, toggle Blynk `pump_command` (V3). The demo changes its simulated
pump and reports simulated status to V4. All data and confirmations are fake.
No AI app, model, ESP32 broker, command proxy or operational logs are loaded/modified.
The CloudClient/Blynk transport and credentials are shared library/configuration only.
Stop any other Edge process using the same Blynk token before starting the demo.
Live runs update the selected device's dashboard; prefer a test device binding.
`--require-command` requires a received unexpired V3 command for a successful exit.
Without it, uplink/MQTT success can finish with "Downlink not verified".

Validation: six mocked tests cover uplink/downlink, expired/missing commands, error
handling, cleanup, fake value ranges and dry-run isolation. Live Cloud operation
must be checked by running the script and observing the dashboard.

## Windows and POSIX compatibility

The launcher and offline tests support Windows and POSIX (Linux/macOS).
The single-instance lock uses `msvcrt.locking` on Windows and `fcntl.flock`
on POSIX; closing the descriptor or exiting the process releases it.
Do not delete `runtime/edge.lock` while an Edge process is running.

POSIX stores retain 0700 directory / 0600 file checks. Windows uses inherited
filesystem ACLs instead of POSIX mode bits: keep the repository, `.env`, and
`runtime` in a directory accessible only to the intended account. The code does
not configure or validate Windows ACLs. Both platforms flush files and replace
stores atomically; parent-directory fsync is performed only on POSIX.

Run offline tests from `Cloud Edge` (with the project's dependencies installed):

```powershell
# Windows PowerShell
$env:PYTHONPATH = "../AI"
python -m unittest discover -s tests -t .
```

```bash
# Linux / macOS
PYTHONPATH=../AI python -m unittest discover -s tests -t .
```

POSIX permission-bit tests are skipped on Windows; lock contention, release,
and store round trips run on both platforms. These tests do not validate live
Blynk connections or AI model dependencies.

## Automatic irrigation permission and device events

V3 controls `node_profile[node_id].auto_irrigation_enabled`, not immediate pump
power. OFF prevents future Edge irrigation commands; an active run finishes its
own timer. ON allows AI to decide whether irrigation is needed. New nodes default
to OFF. Legacy `irrigation_status` is migrated and persisted on read (only Boolean true enables);
the next flag update persists the new field and removes the legacy flag.
Read/modify/write operations share the Edge manager's reentrant lock. Edge checks
the flag before inference and again before publishing. Durations are capped at the
firmware's 60-second limit; model candidates beyond that limit are excluded.

Firmware sets raw 8-bit PWM duty to **100/255**, not 100 percent. Reflash Things to
apply this change. Node events use QoS 1, retain=false on
`iot/{edge_id}/{node_id}/events` with `node_id`, `event`, `sent_at`, `valid_until`.
`pump_started` is emitted after the controller accepts and starts the run;
`pump_stopped` after an active run stops. `capture_succeeded` includes `sample_seq`
and is emitted only after a complete UART image is received and its MQTT upload
is successfully queued. Image bytes still use `/image`. Events have a 60-second
TTL. Edge validates timestamps, node identity, and duplicates; pump events update
V4, while capture confirmation is stored in memory separately.

These are controller/software confirmations, not flow-sensor measurements or
confirmation that the cloud received the image. Failed/disconnected event
publishes are not replayed. Offline tests/builds do not replace a hardware test.
