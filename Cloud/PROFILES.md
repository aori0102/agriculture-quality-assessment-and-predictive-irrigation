# Plant profiles: Cloud implementation and Edge handoff

Cloud validates Blynk drafts and synchronizes them with an injected Edge backend
in the same process. **Edge owns durable storage, active revisions, authorization,
and irrigation policy.** Cloud does not import AI/Things or implement persistence.
Cloud is tested offline; running AI integration and live Blynk verification remain
separate work.

## Blynk setup and editing

Use native device selection/navigation with one Blynk device/token per node.
A Menu widget does not switch other widgets' device bindings. V7 is read-only
identity, not a routing control; mismatches are rejected.

| Pin | Exact name | Type/range | Widget role |
| --- | --- | --- | --- |
| V7 | `node_id` | String | Read-only identity |
| V8 | `node_description` | String | Text input |
| V9 | `plant_name` | String | Text input |
| V10 | `permanent_wilting_point` | Double, 0–1 | Numeric input |
| V11 | `management_allowed_depletion` | Double, 0–1 | Numeric input |
| V12 | `field_capacity` | Double, 0–1 | Numeric input |
| V13 | `saturation_point` | Double, 0–1 | Numeric input |
| V14 | `apply_profile` | Integer, 0–1 | Momentary Apply button |
| V15 | `profile_status` | String | Read-only result |
| V16 | `profile_preset` | Integer, 0–1 | Menu: Custom / Illustrative Lettuce |

V7–V15 retain their existing definitions. V16 is the agreed addition and **must
be created in the template before enabling profiles**. Repository changes do
not modify the live template. Allow 128 characters for V7–V9 and 512 for V15;
configure numeric widgets to display at least three decimal places.

The illustrative preset uses PWP=0.10, MAD=0.30, FC=0.22, SP=0.41, MADP=0.184,
adapted from the old repository example. Its agronomic validity is **unestablished**.
It is never automatically installed as an active profile.

1. Wait for the restored status, then select the node's Blynk device.
2. Selecting Illustrative Lettuce fills draft name/thresholds and preserves
   description. It does not apply the profile.
3. Switch to Custom to edit thresholds; current draft values are retained.
   Name/description can be edited in either mode. To reload the same preset,
   switch to Custom and back; duplicate selector values are ignored.
4. Wait for preview completion, finish editing, then press Apply.
5. Read `APPLIED`, `REJECTED`, or `SYNC_FAILED` in V15.

Use one editor per device; do not edit during preview or Apply. Separate Blynk
scalar streams have no revision token or atomic form submission. A single
multi-field read does not make concurrent editing transactional. Receipt-time
expiry does not establish server-side freshness or exactly-once delivery.

## Public interface for the AI teammate

All types below are exported from `Cloud`:

```python
class ProfileBackend(Protocol):
    def get_active(self, node_id: str) -> ActiveProfile | None: ...
    def apply_profile(self, profile: PlantProfile) -> ProfileApplyResult: ...

# backend = YourEdgeProfileStore(...)
# bindings = load_device_bindings()  # Validates every binding before connecting.
# client = CloudClient(bindings[0].config, node_id=bindings[0].node_id,
#                      profile_backend=backend)
# client.start()
# ... existing Edge loop ...
# client.close()  # In finally; waits for in-flight HTTP/backend operations.
```

This is a contract, not a storage implementation. See
[examples/profile_backend.py](examples/profile_backend.py) for executable examples.
Its `MemoryBackend` is a fake: restarting loses its data. The default command is
offline and makes no connections:

```bash
python -m Cloud.examples.profile_backend
```

The example's `connected_profile_clients(real_backend)` context manager is an
explicitly invoked live integration skeleton. Use the actual Edge store there,
not the fake.

### Validated values

`PlantProfile` is immutable and validates construction. Fields: `node_id`,
`node_description`, `plant_name`, `permanent_wilting_point`,
`management_allowed_depletion`, `field_capacity`, `saturation_point`, `mode`
(`custom` or `preset`), and `preset_key` (`None` or `illustrative_lettuce`).
There is no `plant_id` or `soil_type`. Description may be empty; name and node ID
must be nonempty. Text is printable and limited to 128 characters. Node IDs are
matched exactly without trimming.

`validate_snapshot(values, node_id=...)` maps scalar JSON fields V7–V13/V16 to a
validated profile. It rejects missing values, booleans, nonfinite numbers,
arrays, and numeric strings. Unrelated response fields are ignored. It never
fills missing inputs with defaults. `profile.madp` is computed, never accepted
as input or assigned a datastream:

```text
MADP = FC - MAD * (FC - PWP)
0 < MAD < 1
0 <= PWP < MADP < FC < SP <= 1
```

Preset thresholds must match the catalog. Custom requires `preset_key=None`.

`ActiveProfile(profile, revision, irrigation_note=...)` is Edge-confirmed
durable state. Revision is a positive integer owned by Edge. The note is safe,
printable dashboard text, up to 128 characters; its default says eligibility
was not reported. Cloud does not infer irrigation eligibility.

`ProfileApplyResult(outcome, active, message="")` supports:

| Outcome | Backend contract |
| --- | --- |
| `applied` | Persist and activate before returning the exact submitted profile and its revision. |
| `rejected` | Leave active state unchanged; return the previous active state or `None`. |
| `sync_failed` | Persistence/activation failed without replacing the last valid state; return that state or `None`. |

Identical applications must return the existing revision. Messages are public
dashboard text, limited to 128 characters: no secrets or internal exception
details. Exceptions are sanitized by Cloud. Wrong-node results or confirmation
of a different candidate produce synchronization failure.

### Required AI implementation

- Maintain one record per known node, initially unconfigured; authorize targets
  against Edge's registry. Load validated durable state before starting clients.
- Persist updates atomically with recovery handling before replacing active
  state. Preserve the previous valid state on failures. Own revisions and any
  application timestamps in the persistence layer.
- Both methods run on a Cloud worker, not the main Edge loop. They must be
  bounded and thread-safe: separate node workers may call the same backend
  concurrently. Protect shared storage and active-state reads.
- Do not call back into Cloud, close a client, wait for its worker, perform
  inference, or actuate hardware from these methods. Shutdown joins workers
  and depends on backend methods returning.
- **Gate automatic irrigation until measurements and predictions use compatible
  calibrated VWC.** Dividing `soil_index_pct` by 100 is not calibration. Profile
  application itself must never start a pump.
- Return an eligibility note such as
  `Automatic irrigation blocked: VWC calibration required`.

A backend transaction already entered cannot be cancelled by disconnect. It
must complete consistently; reconnect reads the resulting active state. Cloud
does not automatically retry the application.

## Multiple-device configuration

See [.env.example](.env.example) for placeholders to add to root `.env` or process
environment. `load_device_bindings()` reads `BLYNK_DEVICES_JSON`: a nonempty array
of objects containing exactly `node_id`, `server`, and `auth_token_env`. Tokens
are resolved from the named variables. Process environment overrides root
`.env`; `Cloud/.env` is not loaded. An explicit mapping can be supplied instead.

The function returns a tuple of `DeviceBinding(node_id, config)` after validating
the entire list. Duplicate node IDs/tokens are rejected, even when different
variable names resolve to the same token. Tokens are hidden in repr/errors.
`CloudConfig.from_env()` retains its existing single-device behavior.

Construct one client per binding, and close all clients in finally. Never run
other live clients/examples using those tokens. The loader cannot detect other
processes' connections.

## Lifecycle and failure handling

Profiles are opt-in through `CloudClient(..., profile_backend=backend)`.
Without that argument there are no profile subscriptions, workers, or writes.
Existing pump/telemetry method interfaces remain compatible.

The existing MQTT connection subscribes to pump commands and the exact names
`downlink/ds/apply_profile` and `downlink/ds/profile_preset`. All subscription
acknowledgements must succeed before restoration. MQTT callbacks only enqueue
events; HTTP and backend work run on the worker.

At startup/reconnect, clear queued events, restore Edge's profile to V7–V13/V16,
reset V14, and report `ACTIVE`. For an unconfigured node, clear drafts to
empty/zero placeholders and report `UNCONFIGURED`; these placeholders cannot
pass validation. This deliberately discards unsaved edits. No new events are
accepted until restoration succeeds; stored Apply/pump values are never fetched
for replay. Selector echoes are ignored.

There are at most 16 queued events. Repeated Apply presses coalesce while an
application is pending. Events expire 30 seconds after local receipt; releasing
the button does not cancel an Apply. Retained messages are ignored. Queue
overflow is exposed locally: wait and retry. Failed restoration/status uploads
retry every five seconds; previews/applications are not replayed. Network
operations keep the configured HTTP timeout and verified TLS.

| Property/status | Meaning |
| --- | --- |
| `profiles_ready` | Connected, subscribed, and Edge state restored successfully. |
| `last_profile_status` | Latest local outcome, even if dashboard delivery failed. |
| `last_profile_error` | Latest profile delivery/queue failure; inspect `last_command_error` for MQTT failures. |
| `APPLIED` | Edge confirmed persistence and activation; includes revision and eligibility note. |
| `REJECTED` | Invalid draft or Edge rejection; previous configuration remains active. |
| `SYNC_FAILED` | Retrieval/backend/preview failed; investigate and explicitly retry. |

If Edge applies but the V15 upload fails, local status remains `APPLIED` and
`last_profile_error` records the delivery failure. Retry only the latest status
and V14 reset; never roll back or resubmit the profile. After restart, Edge's
durable state is authoritative. An unreachable dashboard cannot immediately
display an error.

Profile writes are restricted to V7–V16 and can only reset V14 to zero. They
cannot write V3/V4 or local MQTT irrigation commands. Reads use the
[multi-datastream HTTPS API](https://docs.blynk.io/en/blynk.cloud/device-https-api/get-multiple-datastream-values);
subscriptions use [exact MQTT names](https://docs.blynk.io/en/blynk.cloud-mqtt-api/device-mqtt-api/datastreams).

## Verification

```bash
python -m unittest discover -s Cloud/tests -v
python -m Cloud.examples.profile_backend
```

Offline tests cover validation, presets, two-node isolation, backend failures,
retry after commit, stale events, slow operations, reconnect, and existing
pump/telemetry behavior. They do not import AI files.

Live acceptance requires the actual AI backend and two Blynk devices: apply
different profiles, verify durable restart recovery, reject invalid drafts,
interrupt connectivity, and confirm no pump starts. Verify V16/template setup
and actual API/widget behavior. End-to-end persistence is not verified until
that integration passes.
