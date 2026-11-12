# Project Context and Contributor Guidance

## Project Overview

**Repository:** `aori0102/agriculture-quality-assessment-and-predictive-irrigation`

**Project:** Agriculture Quality Assessment and Predictive Irrigation

This is a university Internet of Things (IoT) project for monitoring growing conditions, assessing plant/seedling quality, predicting soil moisture, and supporting automated irrigation. The system combines ESP32 sensor nodes, a Python-based edge application with AI components, and a Blynk Cloud dashboard.

The intended system must support both autonomous irrigation decisions and user-initiated commands. It is an academic prototype, not a production-certified agricultural control system.

## Architecture and Data Flow

1. **Things (ESP32):** Read sensors, package telemetry, and publish to an MQTT broker.
2. **Edge application (Python):** Consume node telemetry, validate and record measurements, run prediction/irrigation logic, and issue local MQTT commands to nodes.
3. **Cloud integration (Python):** Run **inside the Edge application's process**, forwarding selected telemetry, predictions, and device status to Blynk and receiving user commands from Blynk.
4. **Blynk Cloud:** Display current measurements and predictions and expose user controls through web/mobile dashboards.

### Main communication paths

| Direction | Transport | Purpose |
| --- | --- | --- |
| ESP32 -> Edge | Local MQTT | Sensor telemetry and device events/status |
| Edge -> ESP32 | Local MQTT | Irrigation commands |
| Edge -> Blynk | HTTPS Batch API (current integration choice) | Sensor telemetry, AI results, confirmed pump status |
| Blynk -> Edge | Blynk Device MQTT API over TLS | Receive dashboard commands promptly |

**Do not introduce AWS, an independent Cloud server, or an additional gateway service by default.** Blynk provides the hosted cloud services required for the present scope. The `Cloud/` folder is intended to be an importable Python integration package, not a separately deployed process.

## Repository Layout

The repository's `main` branch currently contains:

```text
AI/
  app.py
  config.cfg
  ComputerVision/
  IrrigationPrediction/
Things/
  include/
  lib/
  src/
README.md
```

The following is the **planned local addition**, not necessarily present on the remote `main` branch yet:

```text
Cloud/
  __init__.py
  config.py            # Read and validate Blynk configuration
  datastreams.py       # Centralize Blynk pin and topic mappings
  blynk_http.py        # Edge -> Blynk HTTP updates
  blynk_mqtt.py        # Blynk -> Edge MQTT subscription
  client.py            # Small facade consumed by the Edge application
  tests/
  requirements.txt
.env.example           # Example only; never include real credentials
```

Preserve existing structures and teammate-owned files unless a change is necessary and coordinated.

## Components and Ownership

### `Things/` — Device firmware

- ESP32 firmware is written in Arduino-compatible C++.
- Current sensor inputs include soil moisture and temperature/air humidity (DHT22).
- Each node has a node identifier and publishes timestamped telemetry through MQTT.
- Device firmware owns physical actuation, hardware safeguards, and reporting the actual pump state.

### `AI/` — Edge processing

- `AI/app.py` is the existing edge MQTT integration entry point.
- `AI/IrrigationPrediction/` contains telemetry handling, model inference, plant thresholds, and irrigation decisions.
- `AI/ComputerVision/` is the computer-vision area for plant/seedling quality assessment.
- The edge application, **not Blynk or the Cloud package**, owns irrigation policy and authorization of hardware actions.
- The existing edge code uses `paho-mqtt`, and its processing loop must remain responsive when Cloud MQTT is integrated.

### `Cloud/` — Blynk integration (in development)

- Send telemetry and AI output to Blynk through the HTTPS Batch API.
- Subscribe to Blynk MQTT command updates using TLS on port `8883`.
- Convert incoming Blynk values into small, validated application callbacks or queued commands.
- Report confirmed execution status back to the dashboard.
- Keep Blynk API details out of `AI/IrrigationPrediction/` and hardware-control logic out of `Cloud/`.

## Current Data Contracts

### ESP32 telemetry

The firmware currently publishes telemetry on a topic shaped like:

```text
iot/{site_id}/{node_id}/telemetry
```

The edge configuration currently subscribes using:

```text
iot/{edge_id}/+/telemetry
```

**Integration check:** `site_id` and `edge_id` must resolve to compatible topic segments. Do not assume they match automatically.

The current telemetry format includes these fields:

```json
{
  "zone_id": "example-zone",
  "node_id": "example-node",
  "sampled_at": "2026-11-11T03:00:00Z",
  "soil_index_pct": 42,
  "temperature_c": 28.5,
  "air_humidity_pct": 65,
  "quality": {
    "soil": "valid",
    "dht": "valid"
  }
}
```

Example values above are illustrative. Sensor values may be `null` when invalid; reject or skip invalid readings rather than publishing fabricated measurements. Preserve units and timestamps. Historical AI features may use different internal names; normalize at an explicit boundary.

### Local irrigation commands

Current Edge code publishes commands using the configured topic shape:

```text
iot/{edge_id}/{node_id}/commands
```

The command payload currently includes `edge_id`, `node_id`, `action` (for example `start_pump`), `issued_at`, and `duration_ms`. The full end-to-end command acknowledgement and timeout behavior must be verified against ESP32 firmware before declaring physical irrigation complete.

### Blynk datastreams

The existing Blynk template is named `BTL` and uses these mappings:

| Virtual pin | Datastream name | Type | Meaning |
| --- | --- | --- | --- |
| `V0` | `soil_index_pct` | Integer | Soil moisture percentage |
| `V1` | `temperature_c` | Double | Temperature in Celsius |
| `V2` | `Plant Status` | String | Plant assessment/status |
| `V3` | `Pump Command` | Integer (`0`/`1`) | Requested pump state |
| `V4` | `Pump Status` | Integer (`0`/`1`) | Confirmed pump state |
| `V5` | `Predicted Moisture` | Integer | Predicted soil moisture percentage |
| `V6` | `air_humidity_pct` | Integer | Relative air humidity percentage |

- Blynk HTTPS APIs identify datastreams by virtual pins such as `v0` and `v1`.
- Blynk Device MQTT API identifies them by **exact datastream names**, including spaces and capitalization.
- Subscribe to `downlink/ds/Pump Command` for command changes.
- `V3` is a requested state; **never equate it with actual status `V4`** without a verified device acknowledgement.
- Do not silently change pins or names without coordinating changes in the Blynk template and all clients.
- Validate values against the configured datastream types and ranges. An integer dashboard stream may require explicit rounding of floating-point predictions.

## Credentials and Environment

- Store `BLYNK_SERVER` (the device's regional Blynk host) and `BLYNK_AUTH_TOKEN` outside source code, normally in a root `.env` file.
- Commit `.env.example` containing placeholders only.
- Ensure `.env`, `.venv/`, generated datasets, model artifacts where appropriate, and local caches are covered by relevant `.gitignore` rules.
- Never commit, echo, log, or paste real auth tokens, Wi-Fi credentials, or other secrets.
- Use a Python virtual environment for dependencies; on Arch Linux, do not bypass PEP 668 with `--break-system-packages`.
- Use TLS verification for Blynk MQTT and HTTPS. Prefer the correct regional Blynk hostname to avoid broker redirects.
- Blynk Device MQTT does not permit multiple simultaneous connections with the same device token; avoid running parallel test and production clients against one token.

## Reliability and Safety Rules

1. **Fail safely:** Network loss, stale telemetry, invalid readings, or missing acknowledgements must never implicitly start a pump.
2. **Separate intent and fact:** Record a user's command separately from the physical pump status; update `Pump Status` only after an appropriate confirmation.
3. **Validate command inputs:** Accept only documented actions, values, targets, and durations. Apply irrigation safety limits at the Edge/device layers.
4. **Prevent stale execution:** MQTT clean sessions do not guarantee delivery while disconnected. Reconnection/state-sync handling must not replay an old `ON` command blindly.
5. **Keep callbacks short:** Network callbacks should validate and enqueue work; avoid blocking them with inference, HTTP requests, sleeps, or physical actuation.
6. **Bound operations:** Use network timeouts, explicit error handling, and useful logs without exposing secrets.
7. **Respect node identity:** Multiple nodes may exist; do not send a single-node dashboard command to every device without an explicit mapping.

## Coding and Collaboration Guidance

- Inspect the current implementation before modifying it; this repository is developed collaboratively.
- Make focused changes. Do not restructure `AI/` or `Things/` solely to accommodate the Cloud package.
- Keep protocol adapters (`Cloud/`) separate from domain decisions (`AI/`) and firmware (`Things/`).
- Centralize cross-component field names, MQTT topic shapes, and Blynk pin mappings instead of duplicating literals.
- Use clear Python modules, type hints where helpful, and small functions with explicit inputs and outputs.
- Prefer integration through imports and callbacks rather than an unnecessary intermediary HTTP server.
- Do not introduce deployment infrastructure, a database, or new paid cloud services without a demonstrated requirement.
- Distinguish **implemented and tested** features from **planned** interfaces in documentation and summaries.
- Preserve uncommitted work, avoid destructive Git operations, and do not push changes or change shared interfaces without the requested scope and team coordination.

## Verification Checklist

- [ ] ESP32 publishes a valid telemetry JSON message on the expected local MQTT topic.
- [ ] Edge receives it, verifies identity/timestamp/quality, and records accepted readings.
- [ ] Edge can send `V0`, `V1`, and `V6` to Blynk with one HTTPS Batch request.
- [ ] Edge can publish AI outputs to `V2` and `V5` with their configured types.
- [ ] A Blynk switch bound to `V3` produces `downlink/ds/Pump Command` messages received by the Edge process.
- [ ] Edge applies command validation and forwards an authorized command on the correct local MQTT topic.
- [ ] The ESP32 acknowledges or reports the actual pump state before Edge updates `V4`.
- [ ] Disconnect/reconnect, malformed payloads, invalid values, and timeout cases are tested.

## Known Open Integration Questions

- Confirm the mapping between firmware `SITE_ID` and Edge `edge_id`.
- Decide how a Blynk command selects a specific node/irrigation zone when multiple nodes are connected.
- Align requested irrigation durations with the maximum allowed duration in Edge configuration and with firmware behavior.
- Define the end-to-end command acknowledgement, timeout, and cancellation behavior.
- Decide how often telemetry should be sent to Blynk to stay within the plan's message quota.
- Confirm which computer-vision outputs are ready for the `Plant Status` datastream.

Treat these as unresolved design decisions, not already implemented guarantees.
