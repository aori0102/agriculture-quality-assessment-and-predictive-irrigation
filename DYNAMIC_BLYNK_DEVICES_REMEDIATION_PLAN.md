# Dynamic Blynk Devices — Comprehensive Remediation & Integration Plan

**Project:** Agriculture Quality Assessment and Predictive Irrigation  
**Repository:** https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation  
**Reference:** `main`, reviewed 20 November 2026; provisioning integration at commit [`782c459`](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/commit/782c459d93bf90ed5f8b68585abb73f6c2216345).  
**Status:** **IMPLEMENTATION PLAN — NOT YET EXECUTED**. The issues are based on code inspection. Live Blynk end-to-end behavior and the current test pass count have not been independently verified in this review.  
**Target:** Reliable automatic creation of a Blynk Device **after accepted telemetry from an explicitly approved Edge node**, without disrupting existing telemetry, AI output, profile editing or pump reporting. Academic prototype; operator reauthorization on OAuth expiry is acceptable.

## 0. Instructions for Codex

1. Read `AGENTS.md`, this file, `Cloud Edge/docs/DYNAMIC_PROVISIONING.md`, `Cloud Edge/PROFILES.md`, `Cloud Edge/API.md`, and `AI/CLOUD_UPLOADS.md`.
2. **Inspect the current working tree and base branch first** (`git status`, `git log -1`, and the actual file contents). The reviewer examined remote `main`; local code may differ due to rebase, unpublished changes or teammate commits. Do not overwrite uncommitted work or blindly apply patches to different code.
3. Implement in small, reviewable changes; run tests between phases. Fix the integration, not just the failing assertions. Preserve currently working Cloud/AI/Things behavior unless explicitly superseded by an approved contract.
4. **Never run a live `create_device`/delete request, touch production credentials, change hardware actuation behavior, or push without authorization.** Unit/integration tests must mock MCP, HTTPS, MQTT and file stores as appropriate.
5. Keep secrets out of code, test fixtures, logs and Git. No hidden use of Codex OAuth state: the Edge uses its own OAuth store.
6. Do not implement long-term unattended OAuth refresh as a blocking milestone. Existing long-lived grant with explicit operator reauthorization is accepted for the course prototype.
7. Report each phase's diff, test commands/results, unresolved risks and manual verification instructions.

## 1. Current implementation: factual inventory

### Current functionality

| Component | Existing behavior / file |
|---|---|
| ESP32 → Edge | `AI/app.py`: local MQTT subscription, telemetry routed to `TelemetryHandler` |
| Accepted telemetry | `AI/IrrigationPrediction/telemetry_handler.py`: validates, records data, calls `operation_log_manager.log_node(node_id)`; then invokes optional `on_accepted(node_id, snapshot)` callback |
| Provisioning signal | `Cloud Edge/cloud_edge/provisioning.py`: bounded queue and approved-node allowlist; one worker thread |
| Blynk MCP | `blynk_provisioner.py`: independent OAuth storage; `search_devices(page)` and `create_device(deviceName, templateId)` with template `TMPL6FGqaSpR3` (`BTL`) |
| Persistent binding | `device_store.py`: local private JSON, atomic save, node ↔ device ID/host/token |
| Live clients | `device_registry.py`: thread-safe `RuntimeDeviceRegistry` |
| Blynk command polling | `edge_pump.py`: dispatcher uses `snapshot()` to see newly added clients |
| Cloud uplink | `cloud_edge/client.py` and `blynk_http.py`: telemetry, AI results, confirmed pump status, HTTP batch API |
| Existing Edge Cloud reporter | `AI/cloud_reporter.py`: queued uploads and validated pump event reporting; remains in repo |
| Edge combined startup | `Cloud Edge/cloud_edge/launcher.py`: creates clients, restores stored bindings, starts provisioning/dispatcher, invokes Edge app |

### Existing PoC evidence (reported in repository documentation; not independently re-executed here)

- Independent Python Blynk MCP client authenticated and reused its OAuth grant after restart.
- Blynk MCP `create_device(deviceName, templateId)` returned a `token` for a disposable device.
- Disposable device `test-dynamic-esp01` (ID `4334566`) used `sgp1.blynk.cloud` for HTTPS and MQTT subscription; its saved binding was restored.
- The grant did **not** include a refresh token. This is accepted for the short-lived academic deployment.
- No documented successful full physical ESP32 → accepted MQTT telemetry → remote auto-create test.

### Confirmed issues on inspected `main`

| ID | Priority | Problem | Evidence / impact |
|---|---|---|---|
| R1 | **P0** | `launcher.py` calls `ensure_env_file()` and references `ENV_FILE` without importing them | Fails with `NameError` before normal or `--check` startup; import currently includes only `CloudConfig, load_device_bindings` |
| R2 | **P1** | Launcher no longer configures/closes `AI/cloud_reporter.py` | Existing AI prediction and confirmed pump event uploads are dropped/not delivered; static-bound nodes no longer have normal telemetry upload |
| R3 | **P1** | `telemetry_handler.py` calls new `on_accepted` but removed `cloud_reporter.submit('send_telemetry', ...)` | Telemetry forwarding now depends on provisioning being enabled. Existing static-only path is regressed |
| R4 | **P1** | Provisioning worker performs HTTPS uploads directly (`client.send_snapshot`) | Telemetry path is fragmented; first accepted snapshot is skipped by design; concurrent failures mix provisioning and reporting concerns |
| R5 | **P1** | Existing tests still assume old CloudReporter/launcher wiring | `Cloud Edge/tests/test_edge_launcher.py` patches missing imported names; `AI/test_cloud_reporter.py` checks old telemetry hook/launcher lifecycle; tests need real contract coverage |
| R6 | **P1** | Node allowlist is not sender authentication | `AI/config.cfg` points to a public MQTT broker on port `1883`; another publisher might spoof an approved node ID |
| R7 | **P1** | Edge profile default unit contract may be inconsistent | `AI/config.cfg` default `pwp=10, madp=20, fc=36, sp=50`; `cloud_edge/edge_profiles.py` validates fractional `0..1`. Verify authoritative units before changing anything |
| R8 | **P2** | Worker `status='ready'` means registered client, not MQTT subscription or HTTP success | `CloudClient.start()` is asynchronous; distinguish provisioned / locally activated / cloud-connected |
| R9 | **P2** | Handling of store/create crash, retry and manual reconciliation is only partially operationalized | Remote may exist without recoverable token; user needs visible status and instructions, not silent repeated attempts |
| R10 | **P2** | Documentation and configuration templates disagree with actual behavior | README says old Cloud upload works; provisioning docs claim no `AI/cloud_reporter.py` although file exists on `main`; `.env.example` emphasizes single-device vars while `load_device_bindings` uses `BLYNK_DEVICES_JSON` |
| R11 | **P2** | Multi-process Edge starts and duplicate worker start/shutdown are not explicitly guarded | Shared OAuth/device stores and duplicate Blynk MQTT connections can cause races; design a pragmatic single-process invariant for the prototype |

**Do not assume** each flagged behavior reproduces on a newer local branch; re-check its source first.

## 2. Target behavior and ownership

```text
ESP32 publishes MQTT telemetry
        |
        v
Edge AI validates, records, enrolls node (`log_node`)
        |
        v
ONE accepted-telemetry callback (nonblocking)
        |
        +-- active CloudClient? -- YES --> CloudReporter queued HTTPS upload
        |                               (V0 / V1 / V6)
        |
        +-- NO --> Node eligible/approved? -- NO --> no Blynk device creation
                      |
                      YES
                      v
            Provisioning queue (dedupe by node_id)
                      |
                Existing binding?
                  /          \
                YES          NO
                |             |
          activate client    Search full remote device list
                              |
                       already exists/uncertain --> RECONCILE; never create blindly
                              |
                           absent
                              v
                    MCP create_device(BTL)
                              |
                   persist Auth Token first
                              |
                    activate CloudClient
                              |
                enqueue latest accepted snapshot
                    via CloudReporter

AI inference (V2/V5) ---------> CloudReporter -> active client -> Blynk HTTPS
Device-confirmed pump event --> CloudReporter -> active client -> Blynk HTTPS V4
Blynk V3 command -----------> CloudClient MQTT -> PumpDispatcher -> Edge intent
```

**Boundaries/invariants:**

- **One** Blynk Device per explicitly approved `(edge_id, node_id)`; template remains `BTL`, `TMPL6FGqaSpR3` and V0–V16 mapping unchanged.
- Edge enrollment is **not** equivalent to device authentication. The gate must be explicit; the worker never auto-approves unknown node IDs.
- **One authoritative `RuntimeDeviceRegistry`** for static and dynamic clients. All telemetry/AI/status uploads go through **one `CloudReporter`**; provisioning manages lifecycle only.
- Do not block the local MQTT callback for MCP/HTTPS/OAuth requests; bound queue size and timeouts.
- Save token before activation; treat uncertain create results as `needs_reconciliation` and never blindly retry.
- Blynk V3 is **requested pump intent**; V4 is **confirmed pump state only**. No changes to hardware safety logic or automatic actuation.
- Static mode and old bindings remain supported; `BLYNK_PROVISIONING_ENABLED=0` must **not** disable existing Cloud telemetry.
- Missing/expired OAuth affects only creation of new devices, not currently activated clients or Edge telemetry/irrigation.

## 3. Implementation phases

### Phase 0 — Safety and baseline (no feature edits)

**Tasks:**

- Confirm repository branch, clean/dirty working tree, current commit, and any active rebase/conflict.
- Verify `AI/cloud_reporter.py`, `Cloud Edge/cloud_edge/launcher.py`, provisioning files and test fixtures are present in the target tree.
- Capture baseline tests without modifying credentials or making live Blynk calls. Classify failures as baseline vs introduced.
- Create a dedicated branch or small, reviewable commits according to group workflow. Do not discard teammate changes.

**Commands, from repository root (Linux):**

```bash
git status --short
git log -1 --oneline
PYTHONPATH='AI:Cloud Edge' .venv/bin/python -m unittest discover -s 'Cloud Edge/tests' -t 'Cloud Edge' -v
PYTHONPATH='AI:Cloud Edge' .venv/bin/python -m unittest discover -s AI -p 'test_cloud_reporter.py' -v
```

**Exit:** Baseline failures documented with stack traces; no unreviewed live calls.

### Phase 1 — Restore launchability (**P0**)

**Files:** `Cloud Edge/cloud_edge/launcher.py`, `Cloud Edge/tests/test_edge_launcher.py`.

- Restore `from .config import ensure_env_file, ENV_FILE` alongside existing imports, **or** deliberately refactor references in a coherent way. Never leave unbound identifiers.
- Ensure `main(['--check'])` validates config without importing AI models, connecting to Blynk or creating devices (existing env template behavior may create a placeholder only when missing).
- Ensure both disabled and enabled provisioning configs are validated clearly. Preserve root `.env` path behavior.
- Add smoke tests that execute `main(['--check'])` with mocked filesystem/config, not only direct worker classes.

**Exit:** no `NameError`; `--check` is deterministic and never contacts Blynk.

### Phase 2 — Reunify CloudReporter and runtime registry (**P1, highest functional priority**)

**Files:** `Cloud Edge/cloud_edge/launcher.py`, `AI/cloud_reporter.py`, `AI/IrrigationPrediction/telemetry_handler.py`, possibly a small composition callback module.

- Configure one `CloudReporter` with the **live registry** before `edge_main()` starts, regardless of `BLYNK_PROVISIONING_ENABLED`.
- Adapt `CloudReporter` to a thread-safe `registry.get(node_id)` lookup at enqueue/dispatch time, not a copied `dict` that cannot see new clients. Preserve bounded queue, sanitized errors and safe closure. If its API currently accepts dicts in existing tests/examples, retain compatibility or migrate callers explicitly.
- Make the accepted-telemetry handler call **one** integration entry point. For a known client, enqueue telemetry to reporter; for a missing approved client, enqueue provisioning; for an unapproved/unknown client, do not create anything. Handle static-only mode by uploading known bindings directly.
- Keep AI inference (`send_ai_results`, V2/V5) and pump events (`report_pump_status`, V4) using the same reporter and live registry. Confirm their existing call sites in `AI/app.py` and `AI/IrrigationPrediction/irrigation_handler.py`.
- Remove direct `client.send_snapshot(...)` from provisioning worker **once** reporter routing is in place. After successful create/activate, have worker optionally hand off the **latest accepted** snapshot through reporter (no duplicate first/second send; choose a documented policy).
- Add integration test for **static node, provisioning disabled**, proving an accepted telemetry sample reaches the configured mock client; add dynamic node test proving upload after activation without restart.

**Exit:** static + dynamic telemetry work via one path, AI V2/V5 and confirmed V4 uploads are restored; no double uploads from two independent paths.

### Phase 3 — Harden device lifecycle and recovery (**P1–P2**)

**Files:** `provisioning.py`, `device_registry.py`, `device_store.py`, `blynk_provisioner.py`, `launcher.py`.

- Preserve strict allowlist, `stable_name(edge_id,node_id)`, remote search before create and one in-flight provisioning per node.
- Verify `search_devices` pagination, identity matching and handling of HTTP/MCP uncertain results; if search is incomplete/error, never create.
- Persist Device ID, stable name, template, host, token **before** runtime activation. Keep `0600` JSON, `0700` parent, atomic replacement; never print secrets.
- Define explicit states, e.g. `unapproved`, `queued`, `provisioning`, `stored`, `active_local`, `commands_ready`, `auth_required`, `needs_reconciliation`, `transport_error`. A status should not imply live MQTT connection merely because `start()` returned.
- Expose a **safe operator diagnostic** (CLI `--provisioning-status` or structured sanitized logs) showing node ID, state, last error category, stored device ID and next action, without Auth Tokens.
- On create timeout/uncertain success, remote device discovered without local token, corrupted/private-store failure or rename mismatch: **do not auto-recreate**. Surface administrative reconciliation with instructions to recover a token via supported Blynk UI/process, or rebind manually.
- Preserve persisted active bindings through restart; decide and document whether removal from `BLYNK_APPROVED_NODES` revokes already existing binding or only blocks new creation. For prototype, do **not** automatically delete Blynk devices.
- Prevent simultaneous Edge instances from writing to the same store or using the same Device tokens (simple startup lock/file lock or fail-fast single-instance policy); test it.
- Decouple production `blynk_provisioner.py` from importing `examples.blynk_mcp_store`: move reusable storage to `cloud_edge/oauth_store.py` or explicitly justify a stable library location. Update PoC imports and tests.
- Retain current accepted long-lived OAuth grant; explicitly report `auth_required` when expired/revoked, avoid browser interaction from unattended worker. No implementation requirement for refresh-token flow.
- Validate `BLYNK_PROVISION_SERVER` before activation (operator-selected regional host). The prior disposable-device host check is useful but cannot guarantee every future token; HTTP 308/MQTT redirect should fail safely with actionable diagnostics.

**Exit:** repeated telemetry/restarts/concurrent observations produce at most one intended Device; uncertain remote state never causes blind duplication; troubleshooting is possible without inspecting secret files.

### Phase 4 — Fix Edge profile contract and security gate (**P1**)

**Files:** `AI/config.cfg`, `AI/operation_log_manager.py`, `Cloud Edge/cloud_edge/edge_profiles.py`, validation code/tests; firmware/local MQTT configuration if security improvements are approved by teammates.

- **First determine the authoritative units** for `pwp`, `madp`, `fc`, `sp`. Current `AI/config.cfg` default values are `10,20,36,50`, whereas Cloud Profile code expects fractions `0..1`. Coordinate with the AI owner before transforming persisted values or defaults. Add a boundary conversion only if supported by documented units, and cover it with tests. Never silently rewrite existing profiles.
- Confirm V7–V16 profile sync for freshly provisioned nodes and after restart; if profile backend rejects a newly enrolled default profile, surface the error separately from device creation status.
- Current approved-node allowlist is an **authorization policy for names**, not proof of origin. Prefer a private/authenticated MQTT broker with ACL/TLS or a verifiable per-device onboarding credential. If unavailable for the academic demo, limit use to a controlled lab, document spoofing risk and require explicit operator approval for every Node ID. Do not advertise this as secure device identity.
- Confirm edge_id topic matching, `node_id` topic/payload match, telemetry freshness and quality checks, pump command TTL/status semantics; don't modify irrigation policy as part of this integration.

**Exit:** profile value units are consistent with a written contract; new devices cannot be created solely because an arbitrary external sender claims an unknown name.

### Phase 5 — Tests and regression matrix (**P1**)

Update `Cloud Edge/tests/test_edge_launcher.py`, `Cloud Edge/tests/test_provisioning.py`, `AI/test_cloud_reporter.py`; add integration tests when useful. **Do not delete tests or weaken assertions simply to get a green suite.**

Required scenarios:

| Scenario | Expected result |
|---|---|
| Static binding + provisioning OFF | Accepted telemetry uploads V0/V1/V6; existing AI and pump-reporting paths work |
| Approved new node + no local/remote Blynk device | One create request, private persisted binding, one runtime client, telemetry upload via reporter |
| Approved known node / reconnect / repeated telemetry | No new create; no duplicate client; normal uploads |
| Two approved distinct nodes | Two distinct Device IDs/tokens; proper routing, no cross-node commands |
| Unapproved node or malformed telemetry | No remote search/create, no cloud upload under another identity |
| Remote already exists, no local token | `needs_reconciliation`, no create |
| Search incomplete / MCP timeout / uncertain create | No blind retry or duplicate create; actionable state |
| Local store save fails after remote create | No activation without saved token; next attempt reconciles |
| Restart with persisted bindings | Client restored without calling create; telemetry and commands resume |
| OAuth missing/revoked | `auth_required` for new provisioning; existing clients/Edge unaffected |
| Wrong regional host / MQTT subscribe failure | Diagnostic; do not report `commands_ready` falsely |
| Queue full / worker shutdown / duplicate notifications | Bounded work, no deadlock, clean shutdown |
| AI prediction + pump events | V2/V5 routed; only hardware-confirmed V4 published |
| Profile defaults and reconnect | Profile values validated with correct units, no hidden substitution |
| Launcher config check, partial init, shutdown | Works without network; profile/reporter/worker/dispatcher/clients close in safe order |

**Tests to run (from repo root):**

```bash
python 'Cloud Edge/run_edge.py' --check
PYTHONPATH='AI:Cloud Edge' .venv/bin/python -m unittest discover -s 'Cloud Edge/tests' -t 'Cloud Edge' -v
PYTHONPATH='AI:Cloud Edge' .venv/bin/python -m unittest discover -s AI -p 'test_cloud_reporter.py' -v
# Run any additional integration/unit suites that actually exist in this working tree.
```

Important: `--check` uses local `.env` and may generate a placeholder when absent; do not run it against production credentials indiscriminately. Tests should use temporary config and mocked services. Do **not** interpret green offline tests as proof of real Blynk or ESP32 behavior.

**Exit:** all affected tests pass; no leaked credentials in output; mock transport tests confirm all critical branches.

### Phase 6 — Authorized end-to-end smoke test (manual, after code review)

**No creation during normal automated tests.** A team member explicitly authorizes a disposable Device and provides a controlled approved Node ID.

1. Back up and secure `Cloud Edge/runtime/mcp_oauth.json` and `blynk_devices.json` where applicable. Ensure sufficient Blynk quota and use a disposable Node ID not mapped to a real pump.
2. Configure root `.env`: `BLYNK_PROVISIONING_ENABLED=1`, an exact `BLYNK_APPROVED_NODES` allowlist, `BLYNK_PROVISION_SERVER` verified for the account/region, and any existing static `BLYNK_DEVICES_JSON` bindings.
3. Check `--check`, then run the combined launcher with safe/simulated Edge data. Verify no uncaught `NameError` or startup regressions.
4. Publish one **valid** test-node telemetry packet to the correct `iot/{edge_id}/{node_id}/telemetry` topic from an authorized ESP32/Wokwi/test publisher. Record only safe status events.
5. Confirm exactly **one** new Blynk Device exists under `BTL` and `blynk_devices.json` contains one private binding; never print the token.
6. Verify V0/V1/V6 update via the reporter, and new Device MQTT command subscription readiness. Test V3 control **with pump actuation disabled/mocked**; do not equate saved irrigation intent with actual running pump.
7. Send further telemetry and restart Edge; confirm no duplicate remote creation and reconnection to existing Device. Check AI V2/V5 and confirmed V4 separately with mocks or safe test hardware.
8. Test Blynk unavailability / OAuth fault with mocks or an explicit safe fault-injection environment; verify Edge still accepts local telemetry.
9. Document device ID and nonsecret diagnostics, results, limitations, and owner responsibility for deleting disposable devices in Blynk Console.

**Exit:** documented end-to-end demo and explicit manual cleanup; no unexpected pump activation or duplicate Device.

### Phase 7 — Documentation and operator handoff (**P2**)

**Files:** `Cloud Edge/README.md`, `Cloud Edge/docs/DYNAMIC_PROVISIONING.md`, `AI/CLOUD_UPLOADS.md`, `.env.example`, `AGENTS.md` when agreed.

- Correct the stale statement that the branch lacks `AI/cloud_reporter.py`; describe the new unified upload path and when it operates.
- Provide one working setup path for **static mode**, **dynamic-only mode**, and **hybrid mode**, with correctly quoted `BLYNK_DEVICES_JSON` where required.
- Document one-time independent MCP OAuth consent, runtime credentials, allowed Node IDs, regional-host configuration, device quota, state/error meanings, restart behavior and safe reconciliation steps.
- Clarify that a Blynk Device is a **cloud representation of an Edge node**; the ESP32 itself does not need a Blynk Auth Token if it communicates only with the local MQTT Edge.
- Explicitly state long-lived OAuth + manual reauthorization are accepted prototype constraints. Do not claim indefinite unattended operation.
- Update test instructions to include both `AI` and `Cloud Edge` on `PYTHONPATH` when needed.

**Exit:** new teammate can configure and demo the system using docs without reading implementation details.

## 4. Proposed minimal code ownership map

| File | Required change |
|---|---|
| `cloud_edge/launcher.py` | Fix imports; create/share registry + reporter; attach accepted telemetry callback in all modes; start/stop provisioning/dispatcher/reporter in safe order |
| `AI/cloud_reporter.py` | Use live registry lookup instead of copied `dict`; preserve worker, AI and pump status routes |
| `AI/IrrigationPrediction/telemetry_handler.py` | One accepted-data event; delegate routing; avoid blocking and duplicate uploads |
| `cloud_edge/provisioning.py` | Device creation/lifecycle only; on activation enqueue latest snapshot to reporter; state/uncertainty handling |
| `cloud_edge/device_registry.py` | Shared thread-safe lookup, idempotent ownership and lifetime rules |
| `cloud_edge/device_store.py` | Keep safe atomic writes, validate permissions, handle recovery and concurrency according to plan |
| `cloud_edge/blynk_provisioner.py` | Keep MCP contract/schema; relocate OAuth storage from `examples/`; actionable sanitized errors |
| `cloud_edge/edge_pump.py` | Preserve dynamic snapshot polling and command safety; regression tests |
| `cloud_edge/edge_profiles.py` / AI config | Resolve verified unit mismatch with AI owner; avoid unapproved automatic conversion |
| Test/doc files | Update tests for real behavior and amend stale descriptions |

## 5. Recommended implementation sequence / PRs

1. **PR A — Launcher import fix + true baseline tests.** No new Blynk calls; merge first.
2. **PR B — Single live registry and CloudReporter integration.** Verify static-only and dynamic mode uploads, V2/V5/V4 preservation.
3. **PR C — Provisioning reliability / status / cleanup.** Reconciliation, duplicate/expiry safety, storage ownership, test matrix.
4. **PR D — Profile data contract + local device identity / broker safety.** Coordinate with AI/Things maintainers.
5. **PR E — Documentation + authorized end-to-end demonstration.** Live test only with explicit consent.

If teamwork/time does not permit splitting actual PRs, use the same boundaries as sequential commits and test checkpoints. Do not mix hardware/control changes into the cloud provisioning fix unless agreed.

## 6. Definition of Done

- [ ] `run_edge.py --check` works, including dynamic-enabled and disabled config paths.
- [ ] No module relies on missing imports or old `CloudReporter` lifecycle.
- [ ] Static nodes preserve telemetry, AI output and confirmed status uploads.
- [ ] Approved new node is automatically provisioned once and becomes usable without Edge restart.
- [ ] Nonapproved/spoofable unknown nodes do not create Blynk devices.
- [ ] Reconnect/restart does not create duplicate Device; all secrets remain in private ignored stores.
- [ ] MCP failure, OAuth expiry, bad region and crash-reconciliation states are visible and safe.
- [ ] No false 'pump running' status from the Blynk switch alone.
- [ ] Profile units are documented and consistent with Cloud/Edge expectations.
- [ ] Legacy and new automated test suites pass on the target working tree.
- [ ] One authorized live end-to-end smoke test is documented; disposable devices are cleaned up by an authorized operator.
- [ ] Docs correctly describe the final implementation and remaining academic-project limitations.

## 7. Sources and evidence

### Repository primary sources (reviewed `main`)

- [Provisioning commit `782c459`](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/commit/782c459d93bf90ed5f8b68585abb73f6c2216345)
- [Cloud launcher](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/Cloud%20Edge/cloud_edge/launcher.py)
- [Provisioning worker](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/Cloud%20Edge/cloud_edge/provisioning.py)
- [Blynk MCP adapter](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/Cloud%20Edge/cloud_edge/blynk_provisioner.py)
- [Runtime device registry](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/Cloud%20Edge/cloud_edge/device_registry.py)
- [Private device store](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/Cloud%20Edge/cloud_edge/device_store.py)
- [Telemetry handler](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/AI/IrrigationPrediction/telemetry_handler.py)
- [Edge Cloud reporter](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/AI/cloud_reporter.py)
- [Pump dispatcher](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/Cloud%20Edge/cloud_edge/edge_pump.py)
- [Cloud profile adapter](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/Cloud%20Edge/cloud_edge/edge_profiles.py)
- [Edge configuration](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/AI/config.cfg)
- [Existing tests: launcher](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/Cloud%20Edge/tests/test_edge_launcher.py), [provisioning](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/Cloud%20Edge/tests/test_provisioning.py), [CloudReporter](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/AI/test_cloud_reporter.py)
- [Dynamic provisioning documentation](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/Cloud%20Edge/docs/DYNAMIC_PROVISIONING.md), [MCP spike](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/Cloud%20Edge/docs/BLYNK_MCP_SPIKE.md), [OAuth PoC](https://github.com/aori0102/agriculture-quality-assessment-and-predictive-irrigation/blob/main/Cloud%20Edge/docs/BLYNK_PYTHON_MCP_OAUTH_POC.md)

### Official external technical references

- [Blynk MCP Server: tools, OAuth and device creation](https://docs.blynk.io/en/getting-started/mcp-server)
- [Blynk HTTPS API troubleshooting: regional host and 308 redirect](https://docs.blynk.io/en/blynk.cloud/device-https-api/troubleshooting)
- [Blynk MQTT Device API authentication and redirect](https://docs.blynk.io/en/blynk.cloud-mqtt-api/device-mqtt-api/authentication)
- [MCP Python SDK OAuth clients](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/oauth-clients.md)
- [MCP Python SDK transports](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/transports.md)

**Evidence distinction:** Repository docs report successful isolated Blynk tests; the remediation issues above come from current source inspection. Neither repository docs nor this plan establish a successful complete physical-node end-to-end test.

## 8. One-message Codex execution prompt

> Read `AGENTS.md` and `DYNAMIC_BLYNK_DEVICES_REMEDIATION_PLAN.md`. First inspect the **current working tree** and confirm the issues still exist. Implement the plan phase by phase in the prescribed order. Fix startup and baseline tests, restore a single CloudReporter using RuntimeDeviceRegistry for **both** static and dynamically provisioned devices, preserve V0–V16 and pump command safety, and harden provisioning state/reconciliation. Mock all external calls for automated tests. Do not create/delete live Blynk Devices, access or print secrets, change physical actuation, force-push, or discard teammate changes. Run both the Cloud Edge and AI/cloud_reporter test suites after each relevant phase, document test evidence, and stop before any real end-to-end create test pending explicit authorization.
