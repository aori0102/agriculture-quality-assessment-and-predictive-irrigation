# Standalone Python Blynk MCP OAuth proof of concept

**Decision: NO-GO for indefinite unattended Edge provisioning (20 November 2026).**

This is a separate, read-only probe. It does not use Codex credentials, modify the production Cloud integration, or call Blynk create/delete tools. Its only MCP tool call is `get_template` for `BTL` (`TMPL6FGqaSpR3`).

## Run

Install into a virtual environment:

```bash
python -m pip install -r "Cloud Edge/requirements-mcp-poc.txt"
python "Cloud Edge/examples/blynk_mcp_oauth_poc.py"
python "Cloud Edge/examples/blynk_mcp_oauth_poc.py" --unattended
python "Cloud Edge/examples/blynk_mcp_oauth_poc.py" --unattended --force-refresh
```

The first command opens a browser for OAuth consent and listens only on `127.0.0.1:3030/callback`. The store is `Cloud Edge/runtime/mcp_oauth.json`, ignored by Git, written atomically at mode `0600` inside a `0700` directory. It holds both the access token and dynamic client registration. The next command starts a fresh process and fails if it needs a browser. The last command marks the provider's in-memory access token expired to exercise refresh behavior; it does not alter the saved credential. Never paste this store or an OAuth callback URL into logs, issues, or chat.

## Observed with MCP Python SDK 2.3.0

- Independent OAuth authorization completed and saved a client registration and access token. A fresh `--unattended` process read `BTL` successfully without browser login.
- The issued access token reports an approximately one-year lifetime. The OAuth response contained **no refresh token**. With an in-memory expired-token simulation, `--unattended --force-refresh` required browser authorization and failed. Natural expiry was not observed; the simulation establishes that this grant lacks a refresh credential.
- The SDK reloads tokens after restart but does not restore their original expiration timestamp. The probe records issuance time privately and restores the provider's in-memory expiry before connecting. This uses SDK internals and is not suitable as a stable production contract without upstream support or another validated approach.
- `get_template` returns template metadata and datastreams, **not** a Device Auth Token or regional device host. A previously observed `create_device` result returned a token but no regional host. The probe therefore cannot prove that a newly created device can be configured through the current `CloudConfig(server, auth_token)` without an independently verified regional-host source. No device was created by this probe.

The current grant supports a long-lived unattended session until its access token expires or is revoked; it does not satisfy automatic renewal beyond that point. Resolve refresh-token issuance or use an authorized machine-to-machine grant, confirm regional-host lookup and crash reconciliation, then repeat this probe before enabling automatic provisioning.

Sources: [MCP SDK OAuth clients](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/oauth-clients.md), [MCP SDK transports](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/transports.md), [Blynk MCP](https://docs.blynk.io/en/getting-started/mcp-server).
