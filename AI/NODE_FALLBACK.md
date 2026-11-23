# Node fallback irrigation

Implemented on 2026-11-22. Firmware compiles for the existing `esp32dev` controller; the separate ESP32-S3 camera is unchanged. Hardware actuation and a live broker outage have not been tested.

## Automatic fallback for every node

Update Edge and Things together. Legacy Things firmware cannot acknowledge the new configuration and will remain blocked from new Edge pump starts. Configuration and heartbeat use the same broker as existing telemetry. Use an authenticated broker with per-node ACLs before enabling fallback; the current public broker configuration does not establish configuration ownership.

Every discovered node with a valid profile automatically receives a fallback
configuration. No per-node opt-in or `enabled` entry is needed; an empty
`{"nodes": {}}` or a missing limits file uses defaults for all nodes. Auto OFF
still disables automatic irrigation, including fallback. Manual stop locks remain
in force on the node.

`AI/fallback_policies.json` is optional and only overrides per-node thresholds,
durations and safety limits. For example: All numbers below are illustrative and must be checked against the sensor scale, substrate and pump:

```json
{
  "nodes": {
    "YOUR_NODE_ID": {
      "emergency_threshold_pct": 15.0,
      "rearm_threshold_pct": 20.0,
      "duration_ms": 6000,
      "cooldown_ms": 30000,
      "required_low_samples": 3,
      "max_sample_age_ms": 30000,
      "edge_timeout_ms": 30000,
      "max_runs_per_hour": 3,
      "max_total_duration_ms_per_hour": 60000,
      "unlock_generation": 0
    }
  }
}
```

Fallback is automatically enabled whenever that node's Auto permission is ON.
Legacy per-node `enabled` fields are ignored, including `false`: they no longer
select which nodes have fallback. Calibrate the node profile and tune limits as
needed. Invalid limits disable the affected node until corrected; a malformed
limits file disables fallback for all nodes on the next configuration sync. PWP, MADP and FC are read from the current node profile. An omitted emergency threshold defaults to the midpoint of PWP and MADP; an omitted rearm threshold defaults to MADP. Require `PWP < emergency < MADP`, `emergency < rearm <= FC`, and finite percentages in 0–100. Each run is at most 60000 ms. A malformed policy file revokes enabled configurations on the next successful sync; a disconnected node continues using its last stored valid configuration.

These are durations, not measured water volumes. A manual stop persists a fallback lock in NVS. To explicitly release the lock, increase `unlock_generation` for that node and let the new configuration synchronize; restarting Edge or merely enabling auto does not release the lock. Do not decrease this counter.

## Runtime behavior

- Edge checks policy and sends a full configuration plus heartbeat every five seconds. Versions persist in `AI/fallback_state.json`; each process has its own heartbeat session. Control readiness requires an ACK for a current synchronization nonce, no pending node journal records, and pump off.
- Node checks identity, schema, version, all thresholds and limits, stores the snapshot in NVS, then ACKs. Same-version identical snapshots are acknowledged without rewriting flash; changed content under the same version is rejected. Retained control messages are discarded. Incoming MQTT work is queued and executed on the application loop.
- Node samples soil even while Wi-Fi or MQTT is unavailable. Missing heartbeat alone never starts a run: fallback must be enabled, enough fresh valid samples must be below the emergency threshold, the dry episode must be unused, cooldown complete, pump off and budgets available.
- One run consumes the dry episode. Moisture must reach the rearm threshold before another episode is eligible. Failure to recover leaves the dry episode consumed; the audit plus measured soil readings identify this condition. There is no repeated watering loop.
- All Edge and fallback starts use the same node gate. Budget is reserved persistently before motor activation; failed starts conservatively consume that reservation. The budget resets only after a full hour without a new start (and a new full hour after reboot), which is more conservative than an exact sliding-hour counter.
- Reboot always starts with motor off. Dry-episode latch, stop lock, configuration, consumed budget and pending audit survive. An interrupted run is recorded with unknown actual duration rather than resumed.
- Journal holds up to 32 records; starts stop when fewer than two slots remain. Storage failure blocks new starts. Edge durably stores each audit before acknowledging it; node removes only the acknowledged first record. Lost ACKs cause replay, not duplicate records.

## MQTT additions

All topics use `iot/{edge_id}/{node_id}` as prefix, with `config`, `heartbeat`, `fallback_ack` and the existing `events` suffixes. Configuration and audit acknowledgements use QoS 1, heartbeat QoS 0; none is retained. The MQTT integration already requires credentials to be separately configured on each node; this change does not provision a broker or ACLs.

Config ACKs include the session, sync nonce, applied version, pending journal count and current pump state. Heartbeat sequence must increase within its accepted session; receipt freshness uses node uptime rather than NTP. Audit records contain event ID, run ID, source, config version, phase, requested/observed duration, boot ID and uptime; UTC epoch is nullable if the clock is not synchronized.

## History reconciliation

`fallback_state.json` preserves original audit records keyed by node and event ID. Replayed audit is never routed into the live pump-state handler. Autonomous or interrupted actuation quarantines the node's existing history through receipt time plus 30 minutes. During quarantine Edge blocks AI predictions/automatic irrigation; retraining excludes that node's rows through the cutoff. Source telemetry CSV is preserved. This deliberately excludes more history than necessary rather than fabricating missing per-window irrigation durations. Raw observed durations remain in the audit for later exact reconstruction; they are not yet allocated back into CSV windows.

Live pump state still comes from the firmware's current status events. Manual starts wait for configuration/audit reconciliation but can be explicitly requested during the AI quarantine; firmware cooldown and budget still apply.

Keep `fallback_state.json` with the deployment. Deleting it loses Edge's configuration revision counter; nodes may reject lower versions. Recover by restoring its backup or carefully migrating versions after reviewing each node's persisted configuration, not by clearing node safety state indiscriminately.

## Verification

Run Edge tests from `AI` with the project virtual environment and `Cloud Edge` on `PYTHONPATH`. Build the controller with `platformio run -d Things`. These commands do not flash hardware.

Before enabling fallback, perform supervised hardware tests for missing heartbeat, sensor fault, no moisture recovery, cooldown/budget exhaustion, stop then network loss, restart during a run, full journal and reconnect during actuation. Verify GPIO output duration as well as events. Compile/test success does not establish actual flow or live end-to-end behavior.

Existing disabled snapshots automatically get a new configuration version after
Edge restart/reload. Each node must acknowledge that version before normal Edge
control is ready. This change does not require a new firmware protocol: Things
already stores the configuration in NVS and applies its own offline conditions.
It cannot safely initialize thresholds before ever receiving a valid profile;
a first-time node needs one successful sync before autonomous fallback is ready.

Model quarantine is separate from control readiness. Connection-loss simulation
can start during that 30-minute period once config/audit sync is fresh, pump OFF
is confirmed and no pump command is pending. The quarantine still applies to
model inference; node-local irrigation safeguards are unchanged.

Default pump cooldown is 30,000 ms (30 seconds), measured from the last pump stop. Edge sends a new config version when this value changes; the node must apply it before the shorter interval takes effect.

## Hourly run-count limit removed

Things no longer rejects pump starts based on `max_runs_per_hour`. The legacy
configuration field and NVS run counter are retained for wire/storage
compatibility, but the count does not authorize or reject a run. Reflash Things
to apply this change. The configured cumulative duration budget (default
60,000 ms), cooldown (default 30 seconds), per-run 60,000 ms cap and fallback
dry-episode conditions still apply.
