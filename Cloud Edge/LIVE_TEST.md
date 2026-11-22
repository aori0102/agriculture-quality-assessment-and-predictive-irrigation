# Live Blynk test with a simulated Edge

This runner exercises V0–V16 on your existing **test device** using real Blynk
HTTPS and MQTT/TLS. Telemetry, AI output, profile storage, and pump execution
are simulated. It imports no AI application and connects to no local broker
or hardware. A successful run proves Cloud integration, not physical irrigation.

## Prepare and launch

Stop other MQTT clients using the device token, including `run_edge.py` and
`examples.live_demo`. Ensure no other controller acts on this test device's V3.
The runner overwrites current test-device values and discards unsaved profile
drafts on startup/reconnect. These simulated values remain in Blynk afterward.

From the repository root:

```bash
source .venv/bin/activate
python -m pip install -r "Cloud Edge/requirements.txt"
PYTHONPATH="Cloud Edge" python -m unittest discover -s "Cloud Edge/tests" -t "Cloud Edge" -v
PYTHONPATH="Cloud Edge" python -m examples.simulated_edge --node-id demo-node
```

Use your desired test node identifier instead of `demo-node`. Root `.env` supplies
`BLYNK_SERVER` (regional hostname) and `BLYNK_AUTH_TOKEN`; never put the token on
the command line. The default fixture's `$NODE_ID` placeholder binds explicitly
to `--node-id`. Literal fixture node IDs must match. The runner validates the
whole fixture before connecting. Existing environment variables override `.env`.

Options: `--dataset path/to/file.json`, `--interval 10`, and
`--log path/to/new-file.log`. The log must be a new file; existing files are not
overwritten. Defaults go under ignored `Cloud Edge/runtime/live-<UTC>.log`.
Use an interactive Linux/macOS terminal (the runner uses `select` on stdin).

Confirm all streams below exist with the exact names and types. Percentage
streams need range 0–100; temperature must include 26–33.2°C. Allow 128 characters
on V7–V9, 512 on V15, and enough space for the sample V2 strings. Profile
thresholds use range 0–1 and at least three displayed decimal places. V3 is a
switch, V14 a momentary button, and V16 a 0/1 selector. V7 and V4 are read-only
displays. V8–V13 are editable. See [profile setup](PROFILES.md) for details.

The runner waits up to 60 seconds for subscriptions and profile restoration.
It seeds the illustrative Lettuce profile and initializes the simulated pump
OFF. Each dataset row uploads once, ten seconds apart; the final values remain
displayed. Type `replay` after completion to repeat, or use a larger interval
if you need more time. No periodic uploads occur after the replay finishes.
Each replay sends four telemetry batches, in addition to profile and pump
traffic; check your account quota before repeated runs. Batching does not imply
that Blynk counts all values as one quota message.

## Verify every datastream

The log prints each sample's `expected` pin values and the upload result.
Compare these with the dashboard. An omitted pin should retain its preceding
value. HTTP success is not a dashboard PASS.

The JSON dataset uses Edge API field names: `soil_index_pct` maps to Blynk
`soil_moisture` (V0), `temperature_c` to `weather_temp` (V1),
`air_humidity_pct` to `weather_humidity` (V6), and `predicted_moisture_pct`
to `predicted_moisture` (V5). Keep those JSON keys unchanged; the adapter sends
their values by virtual pin. Incoming pump requests use the exact MQTT topic
`downlink/ds/pump_command`.

| Pin | Exact name | Type | Observation required |
| --- | --- | --- | --- |
| V0 | `soil_moisture` | Integer | 43 → 18 → 78; stays 78 on missing sample |
| V1 | `weather_temp` | Double | 28.5 → 33.2 → 26 → 27 |
| V2 | `plant_status` | String | SIMULATED: Healthy → Dry → Wet → Missing soil/humidity (each prefixed SIMULATED:) |
| V3 | `pump_command` | Integer 0/1 | Dashboard toggles produce matching `pump_request` log events |
| V4 | `pump_status` | Integer 0/1 | Initially 0; changes only after terminal `ack`; withholding leaves it unchanged |
| V5 | `predicted_moisture` | Integer | 41 → 15 → 75; stays 75 on missing sample |
| V6 | `weather_humidity` | Integer | 66 → 48 → 80; stays 80 on missing sample |
| V7 | `node_id` | String | Exact `--node-id`, including after reconnect |
| V8 | `node_description` | String | Edit to `SIMULATED: greenhouse A`, Apply, verify backend |
| V9 | `plant_name` | String | Edit to `Test Lettuce`, Apply, verify backend |
| V10 | `permanent_wilting_point` | Double | Custom 0.12; preset 0.10 |
| V11 | `management_allowed_depletion_point` | Double | Custom 0.18; preset 0.184 |
| V12 | `field_capacity` | Double | Custom 0.26; preset 0.22 |
| V13 | `saturation_point` | Double | Custom 0.45; preset 0.41 |
| V14 | `apply_profile` | Integer 0/1 | Apply updates backend and button returns to 0 |
| V15 | `profile_status` | String | ACTIVE at restore, DRAFT at preview, APPLIED on success, REJECTED for invalid ordering |
| V16 | `profile_preset` | Integer 0/1 | 0 Custom retains draft; 1 previews illustrative Lettuce |

Record observations in the terminal, one pin at a time:

```text
mark V0 PASS saw 43 then 18 then 78; missing sample kept 78
mark V1 PASS saw all four temperatures
mark V3 FAIL toggling the switch produced no request
summary
```

All 17 results start as NOT TESTED. `mark` records your observation; it is not an
automatic assertion. Mark PASS only after completing all listed checks for that
pin. An additional mark replaces that pin's summary result while retaining both
observations in the log. Do not enter secrets in notes or fixture data.

## Pump request and simulated acknowledgement

Wait until the replay ends so no telemetry upload competes with the five-second
command lifetime. Toggle V3 ON, then enter `ack` within five seconds of receipt.
The log records a separate `simulated_ack`; V4 should become 1. Toggle V3 OFF
and repeat; V4 should become 0. No acknowledgement occurs automatically.

Next toggle ON and enter `withhold`, or wait more than five seconds. V4 must
remain 0; a subsequent `ack` must be rejected. Toggle again for a new request.
A new request supersedes the pending one. Disconnection invalidates it. Repeated
ON acknowledgements simply keep the simulated pump ON; no duration is extended.
If an upload is busy, `ack` is rejected rather than queued for delayed execution.
An upload failure can leave the dashboard stale even though the simulator state
changed; the log distinguishes acknowledgement from upload success.

## Profile workflow and reconnect

1. Startup restores a preset, so first select V16=0, then V16=1. Wait for DRAFT
   before editing or applying. Confirm the preset values in the table and press
   V14. Expect APPLIED and V14=0. Type `profile` to inspect the in-memory backend.
2. Select V16=0, wait for DRAFT, then enter the custom V8–V13 values above. Press
   Apply, check APPLIED, and compare `profile` output. Here APPLIED/ACTIVE mean
   simulated state only; the real AI adapter uses SAVED semantics.
3. Set PWP=0.50 while FC=0.26. Press Apply; expect REJECTED and V14=0. Verify
   `profile` still contains the previously applied valid values. Restore PWP=0.12
   and Apply before continuing. Only one person should edit; do not edit during Apply.
4. With the runner still alive, interrupt networking long enough to see
   `commands_ready: false` (MQTT detection may take over a minute). Try a dashboard
   ON while disconnected if your dashboard is on another connected device.
   Restore networking and wait for both readiness flags. Verify the saved custom
   profile is restored, V14 resets, and V4 does not automatically turn ON.
   MQTT commands received after reconnection are still only intent: they cannot
   change simulated state without a fresh terminal acknowledgement.

The backend survives reconnections but not process restarts. Restarting resets
the profile to the fixture. A profile rejection is a test outcome, not a transport
failure. Local `profile_status` events alone do not prove V15 delivery; inspect
the widget and any `transport_error` events.

## Finish and interpret results

Return V3 to OFF, acknowledge it, verify V4=0, then type `finish`. The runner
drains the active upload, records simulated OFF, uploads V4=0, closes the client,
and logs all 17 results. EOF/Ctrl+C also performs this cleanup. If the final OFF
upload fails, `final_off_failed` is explicit; do not assume the widget was reset.

Exit codes: **0** = all pins manually marked PASS and no upload/session failures;
**1** = a recorded FAIL or upload/session/setup failure; **2** = incomplete live
coverage. Recovered connection interruptions remain in the log. Review those
events when assessing reconnect behavior. A deliberate failed upload still
produces exit 1 even if a later attempt succeeds.

Offline tests cover validation, omission, rounding, acknowledgement separation,
expiry, supersession, reconnect invalidation, asynchronous uploads, cleanup, and
redaction. Existing Cloud tests cover malformed/retained MQTT input, HTTP failures,
and profile validation. Live dashboard observations are a separate manual check.
