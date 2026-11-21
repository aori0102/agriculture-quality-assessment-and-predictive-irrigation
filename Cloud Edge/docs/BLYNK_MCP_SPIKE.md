# Blynk MCP provisioning spike

Status: **GO for the academic-project MVP with operator reauthorization on token expiry; NO-GO for indefinite unattended operation** (20 November 2026). The independent Python OAuth probe succeeds through restart, but the observed grant has no refresh token. See [standalone OAuth proof](BLYNK_PYTHON_MCP_OAUTH_POC.md) and [dynamic mode](DYNAMIC_PROVISIONING.md).

## Verified

- [Blynk's MCP documentation](https://docs.blynk.io/en/getting-started/mcp-server) lists `https://blynk.cloud/mcp`, OAuth 2.0 with Dynamic Client Registration, `get_all_templates`, `search_devices`, `get_device`, and `create_device`. It says `create_device` creates a device under a template and generates an Auth Token.
- The connected Blynk tool declares `create_device(deviceName: string, templateId: string)`. A live call returned `uri`, `id`, `name`, `templateId`, `templateName`, `activatedAt`, and a nonempty `token`. The token was not printed or saved. Its response did **not** provide a regional host.
- The live `BTL` template ID is `TMPL6FGqaSpR3`, with V0–V16 datastreams matching the current Cloud contract. Device search is paginated by a zero-based `page` and reports `content` and `totalElements`; the observed first page listed two existing devices before the test create.
- One disposable device was created: `aiot provisioning spike 20261120`, ID `4334421`, URI `device://4334421`. No delete tool is available in the connected Blynk server. It remains in the account and counts toward its device quota until an operator removes it in the Blynk Console.
- A second disposable device, `test-dynamic-esp01` (ID `4334566`), was created by the independent Python client and saved in the ignored private binding store. Its token worked with the configured `sgp1.blynk.cloud` host for HTTPS telemetry and Blynk MQTT command subscription. The binding was restored in a new Python process without another create call.
- The connected tool runs in Codex, not in the Edge Python process. A separate Python client has now verified first-time authorization and reuse after restart. Its observed grant contains no refresh token; automatic renewal after access-token expiry failed in an unattended simulation. The user accepted this limitation for the project lifetime. Recovery of a created device's token through supported APIs remains unverified.
- The current branch has no `AI/cloud_reporter.py` or Edge-to-Cloud telemetry upload hook. `AI/IrrigationPrediction/telemetry_handler.py` records accepted telemetry and enrolls the node; `Cloud Edge/cloud_edge/launcher.py` starts clients from static bindings. Implementation must account for this difference from the pasted proposal.
- Baseline: `PYTHONPATH='AI:Cloud Edge' .venv/bin/python -m unittest discover -s 'Cloud Edge/tests' -t 'Cloud Edge' -q` passed 105 tests. The shorter `PYTHONPATH='Cloud Edge'` command fails one import test because `node_profile` is under `AI/`.

## Gate before implementation

Historical note: the original gate required an approved-node list. The current [dynamic provisioning setup](DYNAMIC_PROVISIONING.md) uses open enrollment when enabled. A create/store crash or lost token still requires administrative reconciliation; the Edge must not create a duplicate. The regional host is operator configured and was validated with one disposable device. An end-to-end physical-node telemetry trigger remains unverified.
