# Plant profiles: Blynk → Cloud Edge → AI

Cloud Edge owns communication, validation, and the adapter to the existing AI
interface. AI owns its saved plant name and thresholds. The adapter calls
`feranmut_proxy.update_node_profile()` and uses the existing operation manager's
`load_log()` for verification. It never edits AI storage directly and never calls
`enable_irrigation_on_node()` or `disable_irrigation_on_node()`.

## Run and configure

The folder remains `Cloud Edge/`; its Python package is `cloud_edge`.
From the repository root:

```bash
python -m pip install -r "Cloud Edge/requirements.txt"
python "Cloud Edge/run_edge.py" --check
python "Cloud Edge/run_edge.py"
```

`--check` checks configuration structure without importing AI or contacting
Blynk. The normal launcher imports AI, constructs one shared adapter for all
node clients, starts Blynk workers, and calls the existing `app.main()` in the
same process. All clients close on exit or partial startup failure.

The checked-in AI configuration includes `[nodes] default_node_profile` with
percentage thresholds. The Cloud adapter converts these at its Blynk boundary.
The launcher reports a missing or invalid profile configuration explicitly.
AI model/dependency initialization must also
succeed before clients start. Running the normal launcher executes the existing
AI application and its normal behavior; it is not a hardware dry run.

Use [.env.example](.env.example) for multiple node/device bindings. Add its
placeholder entries to root `.env` or process environment; `Cloud Edge/.env` is
not loaded. One device token maps to one fixed node. Duplicate node IDs/tokens
are rejected before connections are created. Do not run competing MQTT clients
with the same token.

## Dashboard contract

Use native Blynk device selection; a Menu widget does not retarget other widgets.
Keep V7–V15 unchanged and create the agreed V16 selector before enabling profiles.

| Pin | Exact name | Type | Role |
| --- | --- | --- | --- |
| V7 | `node_id` | String | Read-only identity |
| V8 | `node_description` | String | Editable description |
| V9 | `plant_name` | String | Editable plant name |
| V10 | `permanent_wilting_point` | Double 0–1 | PWP |
| V11 | `management_allowed_depletion` | Double 0–1 | MAD fraction |
| V12 | `field_capacity` | Double 0–1 | FC |
| V13 | `saturation_point` | Double 0–1 | SP |
| V14 | `apply_profile` | Integer 0–1 | Momentary Apply button |
| V15 | `profile_status` | String | Result display |
| V16 | `profile_preset` | Integer 0–1 | 0 Custom, 1 Illustrative Lettuce |

Allow 128 characters on V7–V9, 512 on V15, and at least three displayed decimal
places for thresholds. Selecting the preset fills a draft; switching to Custom
keeps the current values. Reload the same preset by switching to Custom and back.
Wait for preview completion before editing or pressing Apply. Preset thresholds
must match the illustrative catalog within numerical round-trip tolerance.

The Lettuce values (PWP .10, MAD .30, FC .22, SP .41) are illustrative and have
no established agronomic validity. No profile operation activates irrigation.
Use one editor per device and do not edit during Apply: scalar Blynk streams
cannot provide atomic multi-user form submissions.

## Mapping, validation, and MAD readback

The adapter sends a complete validated snapshot through these existing arguments:

| Profile field | Edge argument |
| --- | --- |
| `node_id` | `node_id` |
| `plant_name` | `plant` |
| PWP | `permanent_wilting_point` |
| MAD | `management_allowable_depletion` |
| FC | `field_capacity` |
| SP | `saturation_point` |

V10–V13 remain fractions (0–1) in Blynk. The Cloud adapter converts PWP, FC,
and SP to percentages (0–100) when writing Edge profiles, and converts stored
Edge percentages back to fractions for Blynk. MAD remains a fraction; Edge uses
it to calculate and persist **MADP** in percentage points. Cloud Edge
reconstructs MAD from persisted values:

```text
MADP = FC - MAD * (FC - PWP)
MAD = (FC - MADP) / (FC - PWP)
0 < MAD < 1
0 <= PWP < MADP < FC < SP <= 1
```

Readback validates finite numeric values, identity, and ordering before dividing.
Strings, booleans, missing thresholds, invalid legacy records, and ambiguous
units are rejected. Numeric comparisons use relative tolerance `1e-9` and
absolute tolerance `1e-12`; names and identities must match exactly. Legacy
saved profiles with all thresholds at or below 1 are rejected as ambiguous.
No VWC calibration is inferred.

Before writing, unknown nodes are rejected without registration. An identical
saved core profile skips the AI write; metadata can still be updated. After
writing, the adapter verifies the saved plant name and all thresholds. A function
return alone is not enough to claim success.

## Integration metadata and reconnect

Description, mode, and preset key are saved in
`Cloud Edge/runtime/profile_metadata.json` (ignored by Git), with schema version
1 and the acknowledged Edge values used solely to detect stale metadata. MAD
and Edge revisions are not invented or stored separately. Metadata is saved by
flush/fsync and atomic file replacement after successful Edge readback.

On reconnect, current Edge values are authoritative. Matching metadata completes
the form. Missing, unreadable, or stale metadata produces a verified numeric/name
form with empty description and Custom as **editing defaults**, plus a
`metadata incomplete` message. Editing remains enabled so the user can complete
and reapply it. Invalid Edge numeric records instead fail restoration and require
Edge repair; they are not replaced with guessed valid data.

Corrupt metadata documents are not silently overwritten on Apply. Preserve the
file for diagnosis and repair or move it aside deliberately before retrying.
A metadata save failure after an Edge save reports partial completion. Cloud
Edge does not attempt an unsafe rollback of the Edge operation log.

## Adapter and result API

```python
from cloud_edge import EdgeProfileBackend, CloudClient

# Proxy/manager are initialized by the existing AI environment.
backend = EdgeProfileBackend(
    proxy.update_node_profile,
    proxy.operation_log_manager.load_log,
)
client = CloudClient(config, node_id=node_id, profile_backend=backend)
# Share this same backend instance among all node clients.
```

`EdgeProfileBackend(update_node_profile, load_log, metadata_path=...)` implements
our internal `ProfileBackend` protocol; the AI teammate does not implement it.
Callables are injected, so importing the package never imports AI.

For compatibility, `ActiveProfile` remains the record type. `revision` can now
be `None` (the actual AI adapter has no revision). `saved_only=True` identifies
verified saved values rather than confirmed irrigation adoption, and
`metadata_complete=False` marks incomplete UI metadata. Existing revision-bearing
backends retain their behavior. `ProfileApplyResult` additionally accepts `saved`.

| Status | Meaning |
| --- | --- |
| `SAVED` | Edge values verified; on Apply, metadata was also saved. A reconnect may explicitly mark metadata incomplete. |
| `REJECTED` | Invalid Blynk input or unknown node detected before writing. |
| `SYNC_FAILED` | Write/readback/metadata/network failure; state may be uncertain. |
| `APPLIED` / `ACTIVE` | Retained for other backends that explicitly confirm activation. The real AI adapter does not claim these. |

`last_profile_status` records local outcomes; `last_profile_error` exposes status
delivery failures. MQTT errors remain in `last_command_error`. Status retries
never reapply the Edge update. Reconnect discards queued requests, restores saved
values, resets V14, and does not replay commands. Existing bounded queues,
30-second event expiry, and five-second reporting retries remain in effect.

## Limits and verification

The shared adapter lock serializes Cloud Edge updates. It cannot protect against
independent AI writers. The existing AI writer uses a non-atomic file write, so
failures cannot guarantee preservation of its previous record. Failed writes or
readback report uncertainty, never a false rollback guarantee. Backend operations
must return for orderly shutdown; Cloud does not forcibly interrupt file writes.

The existing irrigation consumer still needs to adopt per-node profiles. VWC
calibration and irrigation eligibility remain AI responsibilities. Saving does
not prove adoption, calibration, or pump execution.

```bash
PYTHONPATH="Cloud Edge" python -m unittest discover -s "Cloud Edge/tests" -t "Cloud Edge" -v
PYTHONPATH="Cloud Edge" python -m examples.profile_backend
```

The example backend is in-memory only. Automated tests include the real AI proxy
and operation manager against temporary storage and a supplied test configuration;
no live AI storage, credentials, broker, or hardware are used. Live Blynk testing
and actual AI runtime startup remain separate acceptance checks.
