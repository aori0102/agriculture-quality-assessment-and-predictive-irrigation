# Dynamic Blynk provisioning MVP

The Edge can create a Blynk `BTL` device after it accepts telemetry from an explicitly approved node. This is an academic-project mode with a long-lived OAuth access token; Blynk did not issue a refresh token to the tested account. Reauthorize the standalone Python MCP client if the token expires or is revoked.

## Configure

1. Install `Cloud Edge/requirements.txt` and `Cloud Edge/requirements-mcp-poc.txt` in the project virtual environment.
2. Run `python "Cloud Edge/examples/blynk_mcp_oauth_poc.py"` once and complete browser authorization. The private OAuth store is `Cloud Edge/runtime/mcp_oauth.json`.
3. In the root `.env`, set `BLYNK_PROVISIONING_ENABLED=1`, `BLYNK_APPROVED_NODES=esp03` (exact comma-separated node IDs), and `BLYNK_PROVISION_SERVER=sgp1.blynk.cloud` (your verified regional host). The host is supplied by the operator because MCP `create_device` does not return it. Keep existing `BLYNK_DEVICES_JSON` entries for manually configured nodes, or use an empty array in dynamic-only mode.
4. Check configuration with `python "Cloud Edge/run_edge.py" --check`, then start with `python "Cloud Edge/run_edge.py"`.

The launcher restores `Cloud Edge/runtime/blynk_devices.json` before accepting new telemetry. That file is ignored by Git and stores Device Auth Tokens at mode `0600`. Back it up securely; if a remote create succeeds but the token is lost before persistence, the worker marks the node `needs_reconciliation` and will not create another device. An existing manually named Blynk device also requires reconciliation unless it has an explicit static binding. The worker creates only after validated Edge telemetry and exact allowlist approval. It never sends a pump command. Sensor uploads begin on subsequent accepted telemetry; it does not replay the first reading.

This branch has no AI-output reporter, so this MVP forwards accepted V0/V1/V6 telemetry only. `V2` and `V5` remain available through the existing `CloudClient` API when AI results are ready. `V4` still requires actual device confirmation. The Blynk command dispatcher polls the live client registry and keeps existing command safety behavior.

## Verification and limitations

- Offline provisioning tests use fake MCP adapters and temporary stores; ordinary tests never create Blynk devices.
- A disposable test device `test-dynamic-esp01` (Blynk ID `4334566`) was created through the independent Python OAuth client, saved privately, and successfully used with `sgp1.blynk.cloud` for HTTPS telemetry and an MQTT command subscription. It remains in the account and counts toward the device quota; removal is an operator action.
- The complete MQTT telemetry → Edge enrollment → automatic create flow has not been run with a physical ESP32. The regional host test proves the configured host works for the disposable device, not that Blynk will keep every future device on that host.
- A renamed remote device cannot be found by the stable-name lookup after local credentials are lost. Treat loss of the private binding file as an administrative reconciliation event. Do not approve arbitrary node IDs from the public MQTT broker.
