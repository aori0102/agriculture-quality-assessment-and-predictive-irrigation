# Dynamic Blynk provisioning MVP

With dynamic provisioning enabled, the Edge can create a Blynk `BTL` device after it accepts telemetry from any Node whose ID matches `[A-Za-z0-9_-]{1,64}`. This is open enrollment for an academic prototype, not authenticated onboarding. Blynk did not issue a refresh token to the tested account; reauthorize the standalone Python MCP client if its access token expires or is revoked.

## Configure

1. Install `Cloud Edge/requirements.txt` and `Cloud Edge/requirements-mcp-poc.txt` in the project virtual environment.
2. Run `python "Cloud Edge/examples/blynk_mcp_oauth_poc.py"` once and complete browser authorization. The private OAuth store is `Cloud Edge/runtime/mcp_oauth.json`.
3. In the root `.env`, set `BLYNK_PROVISIONING_ENABLED=1`, `BLYNK_PROVISION_SERVER=sgp1.blynk.cloud` (your verified regional host), and `BLYNK_DEVICES_JSON='[]'` for dynamic-only mode. The host is supplied by the operator because MCP `create_device` does not return it. Keep existing `BLYNK_DEVICES_JSON` entries for manually configured nodes in hybrid mode. No Node ID list is needed.
4. Check configuration with `python "Cloud Edge/run_edge.py" --check`, then start with `python "Cloud Edge/run_edge.py"`.

The launcher restores `Cloud Edge/runtime/blynk_devices.json` before accepting new telemetry. That file is ignored by Git and stores Device Auth Tokens at mode `0600`. A private `pending_creates.json` journal records each remote create intent before the call; an uncertain result or crash requires manual reconciliation and cannot trigger an automatic second create. Back up both files securely. An existing manually named Blynk device also requires reconciliation unless it has an explicit static binding. The worker creates only after validated Edge telemetry; one pending queue item per Node coalesces repeat observations without dropping other Nodes in a burst. It never sends a pump command. The first accepted snapshot is queued to `AI/cloud_reporter.py` after binding persistence and client activation.

One live client registry serves static and dynamically provisioned nodes. `AI/cloud_reporter.py` queues V0/V1/V6 telemetry, AI V2/V5 output and device-confirmed V4 updates in both modes. `V4` still requires actual device confirmation. The Blynk command dispatcher polls the same registry. `active_local` means a client started; `commands_ready` means its MQTT subscription is confirmed. Neither status alone proves an HTTPS upload succeeded. `auth_required` means run the standalone OAuth probe interactively again. `needs_reconciliation` means inspect the remote Device and local private files before any retry; never remove the journal marker merely to force creation.

The launcher takes an exclusive lock at `Cloud Edge/runtime/edge.lock` and refuses a second Edge process sharing these device tokens. Turning dynamic provisioning off prevents new creation and leaves persisted dynamic bindings untouched; enabling it again restores them. Use the Blynk Console to remove disposable Devices deliberately.

For static-only mode use `BLYNK_PROVISIONING_ENABLED=0` and at least one `BLYNK_DEVICES_JSON` entry with a separate referenced token variable. For dynamic-only mode use `BLYNK_PROVISIONING_ENABLED=1`, `BLYNK_DEVICES_JSON='[]'`, and a regional host. Hybrid mode uses both static bindings and dynamic settings. The ESP32 uses local MQTT; its Blynk Device is an Edge-managed cloud representation and does not need the Blynk Auth Token in firmware.

To check fresh-node enrollment, start Edge with dynamic-only configuration and publish valid telemetry for a new Node ID on the configured local MQTT broker. Verify one Device and V0/V1/V6 values in Blynk. Publish a second valid Node ID without changing `.env` or restarting Edge, then verify its separate Device. Repeat telemetry and restart Edge to check that the stored Device IDs are reused. A simulated publisher is sufficient for the enrollment path; an ESP32 test also requires matching broker and Edge ID settings. Run live creation only as an explicit operator test.

## Verification and limitations

Run `python "Cloud Edge/run_edge.py" --provisioning-status` for a read-only,
token-free list of stored Device IDs and pending create intents. It does not
contact Blynk or report current MQTT readiness. A pending intent requires an
operator to inspect Blynk and the private binding store before correction.

- Offline provisioning tests use fake MCP adapters and temporary stores; ordinary tests never create Blynk devices.
- A disposable test device `test-dynamic-esp01` (Blynk ID `4334566`) was created through the independent Python OAuth client, saved privately, and successfully used with `sgp1.blynk.cloud` for HTTPS telemetry and an MQTT command subscription. It remains in the account and counts toward the device quota; removal is an operator action.
- The complete MQTT telemetry → Edge enrollment → automatic create flow has not been run with a physical ESP32. The regional host test proves the configured host works for the disposable device, not that Blynk will keep every future device on that host.
- A renamed remote device cannot be found by the stable-name lookup after local credentials are lost. Treat loss of the private binding file as an administrative reconciliation event.
- The current public MQTT broker does not authenticate sender identity. An attacker able to publish accepted telemetry under a valid Node ID can trigger Device creation. Use this mode only on a controlled lab network until broker authentication and ACLs are coordinated with firmware owners. Blynk account quotas and API restrictions still apply; provisioning failures are reported.
- The Edge stores PWP, MADP, FC and SP as 0–100 percentages; the Blynk profile editor uses 0–1 fractions. The Cloud profile adapter converts at that boundary and rejects ambiguous legacy records with all thresholds at or below 1. Existing saved AI profiles are not rewritten.
