# Things configuration

The web configuration portal includes a pump PWM slider from 0 to 255, default
200. The current numeric duty is displayed beside it. Save Configuration writes
`pump_duty` to NVS and restarts the node; startup applies this value to the pump
controller. Adjusting the slider alone does not run the pump. PWM 0 prevents a
pump start. The server rejects missing/non-integer/out-of-range PWM values before
saving; compile-time checks cover boundaries, malformed values and overflow.

Defaults for a new node (or absent NVS keys): MQTT host `broker.hivemq.com`,
port `1883`, telemetry interval `10000` ms, pump PWM `200`. Existing valid NVS
settings are retained; use the portal to change an already configured device.
Empty host, zero port and zero interval fall back to defaults. First boot also
initializes defaults when the NVS namespace is not yet present.

Build with `pio run`, then upload the Things firmware. Hardware PWM behavior and
browser slider interaction require a device test; no board is flashed by a build.

## Status LED (GPIO23)

- One flash: WiFi disconnected.
- Two flashes: WiFi connected, MQTT disconnected.
- Three flashes: configuration portal/AP is open (highest priority).
- Steady on: WiFi and MQTT connected while the portal is closed.

Each flash is 150 ms on / 150 ms off, followed by an additional 1000 ms pause
between groups. Opening/closing the portal resets the pattern. The portal
pattern also updates during startup WiFi/NTP waiting loops. Save restarts the
node, returning the LED to its current connection state. This indicates portal
availability, not whether a phone is connected or viewing the page.

## Close configuration with the button

Hold GPIO25 for five seconds to open the configuration portal. Release it, then
press and release once (less than one second) to close the portal/AP. Releasing
the original opening hold does not close it. Closing stops DNS/HTTP and the AP;
the station/MQTT connection can continue, and the LED returns to connection status.
Closing does not save form edits or restart the device. Existing restart and
sleep hold gestures remain unchanged.

## Edge heartbeat loss log

When the stored `edge_timeout_ms` expires (default 30 seconds), Things prints
`[FALLBACK] HEARTBEAT LOST node=... | no Edge heartbeat for ... ms` once per
loss episode, before evaluating Auto, moisture, cooldown or pump limits. A
valid heartbeat rearms the log for the next loss. A newly accepted Edge session
starts a fresh timeout. A stored fallback configuration is required to know the
timeout; the log does not claim that the pump started.

## Compact runtime Serial log

Routine config/heartbeat JSON payload dumps and QoS publish-ACK lines are quiet.
Each sensor transmission uses one `[TELEM]` summary. SDK logging is set to
warning/error level (`CORE_DEBUG_LEVEL=2`).

For pump commands, follow the exact command ID:

```text
[CMD RX] action=start_pump id=<command_id> duration_ms=3000
[PUMP ON] id=<command_id>
[PUMP OFF] id=<command_id>
```

A refused run logs `[PUMP REJECT] id=... reason=cooldown retry_ms=... detail=...`.
Duplicate starts log `[CMD SKIP] ... reason=duplicate_start`; command-storage
failures are explicit. RX only means the JSON command reached processing;
ON means controller start succeeded. Physical water delivery is not measured.
Repeated five-second state publications still happen but do not spam Serial.
Local fallback starts use `[PUMP LOCAL] run=... duration_ms=...`. Capture-error
diagnostics and heartbeat-loss warnings remain visible. Firmware build validation
does not flash the board or exercise the real pump.

## Hourly run-count limit removed

Things no longer rejects pump starts based on `max_runs_per_hour`. The legacy
configuration field and NVS run counter are retained for wire/storage
compatibility, but the count does not authorize or reject a run. Reflash Things
to apply this change. The configured cumulative duration budget (default
60,000 ms), cooldown (default 30 seconds), per-run 60,000 ms cap and fallback
dry-episode conditions still apply.
