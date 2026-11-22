# Interactive Edge debug mode

Run from `Cloud Edge` using the existing project environment:

```powershell
python -m run_edge           # normal application, no input thread
python -m run_edge --debug   # normal application plus debug> console
```

This uses the same process, MQTT connection, Cloud clients and instance lock.
Do not run it alongside the existing Edge process with the same Blynk tokens.
`--debug --check` only validates configuration and does not start a console.
The console controls real hardware; it is not a software-only mock.

## Commands

```text
simulate_pump <node_id> <duration_ms> [delay_ms=5000]
simulate_capture <node_id>
simulate_node_connection_lost <node_id> [after_pump_delay_ms=5000]
help
status
cancel <node_id>
```

Examples (replace `esp01` with the actual recorded node ID):

```text
debug> simulate_pump esp01 3000
debug> simulate_pump esp01 3000 10000
debug> simulate_capture esp01
debug> simulate_node_connection_lost esp01
debug> status
```

Pump: wait the requested delay, then request a run lasting `duration_ms` through
the existing pump coordinator. It requires a known node, fresh confirmed OFF,
no pending command, configuration/audit readiness, connected MQTT, and duration
within the configured maximum (at most 60,000 ms). Node cooldown/budget rules
still apply. It preserves Auto mode. A queued command is not proof of pump
start; normal device confirmation/timeout applies. Delays accept 0–120,000 ms.
A dispatch more than two seconds late is dropped rather than replayed.

Capture: enqueue the standard capture command for exactly that node with a
10-second command TTL. The normal capture acknowledgement/image path applies.

## Connection-loss simulation

1. All discovered nodes now receive fallback configuration automatically, without
   per-node policy opt-in. Require Auto ON, the enabled configuration synced and
   acknowledged, fresh configuration/audit readiness, confirmed pump OFF and no
   pending pump command. The debug command does not edit thresholds, fake soil
   readings, clear stop locks, reset cooldowns, or reset irrigation budgets.
2. Edge sends a short-lived `/debug` arm message with a unique simulation ID,
   current Edge session, and the post-pump delay. Node validates identity,
   session, expiry, and eligibility before replying `debug_simulation_armed`.
   Node needs a valid UTC clock for arm expiry, as normal timed commands do.
3. After that ACK, Edge suppresses config and heartbeat for this node only.
   Other nodes continue normally. New pump starts, captures and automatic
   decisions for the target are blocked; emergency Stop remains allowed.
   MQTT itself remains connected, as requested: this simulates loss of Edge
   supervision, not Wi-Fi teardown. Audit/status/telemetry can still arrive.
4. Node's unchanged fallback loop decides whether to water after heartbeat
   expiry (default 30 s). Moisture, valid sample, dry-episode, cooldown, stop lock
   and hourly budget conditions all still apply. A dry condition is not forced.
5. After a successfully started **local fallback** pump run stops, node waits
   `after_pump_delay_ms` (default 5,000), then publishes
   `debug_simulation_completed` with simulation/session IDs, run ID, actual
   duration, source and confirmed OFF. It retries that ACK every two seconds
   until cancelled or the node's own 120-second debug tracking expires.
6. Edge accepts only the matching active simulation's completion and restores
   config/heartbeat. Existing audit reconciliation/quarantine and a fresh config
   ACK are still required before new automatic control is ready.

The Edge ends simulation no later than 120 seconds from the console command,
including arming, heartbeat expiry, waiting for soil/cooldown, pump time and the
post-pump delay. If conditions never cause watering or this sequence cannot fit
in two minutes, it times out and restores heartbeat. Missing arm ACK aborts
after 10 seconds without ever suppressing heartbeat. Timeout is not a successful
pump run. Firmware also expires its debug tracking after 120 seconds.

`cancel <node_id>` removes a scheduled debug start or restores a simulated
connection. It does not stop an already-running pump; that run retains its
normal bounded timer. EOF closes the console and cancels debug operations while
Edge continues. Ctrl+C uses the normal application shutdown and cleans up debug
state. Debug state is not persisted across process/node restarts.

## Firmware and validation

The node must be flashed with this updated `Things` firmware for the loss
simulation ACK protocol. An older node cannot arm the simulation. No firmware
upload, live pump action or live capture was performed while implementing this.

Offline tests cover delayed pump dispatch, validation, capture routing, node
isolation, arm/reject/complete ACK matching, stale/retained ACK rejection,
timeout, cancellation, EOF cleanup, transport failures and CLI mode selection.
PlatformIO compilation verifies the updated firmware, but does not replace a
hardware test of pump completion and the delayed ACK.

## Split terminal interface

On an interactive terminal, `--debug` now opens a full-screen layout: a scrolling
log pane above and a fixed command input below. Logs from Python stdout/stderr
are captured in the log pane while the UI is active; the draft command is kept
separate. The pane retains up to 3,000 recent log lines with bounded line lengths.

- Enter submits a command.
- Tab completes command names; Up/Down recalls command history.
- Page Up/Page Down scroll logs without moving the input or changing its draft.
- Ctrl+End resumes following the latest log messages.
- Ctrl+C stops Edge through its existing shutdown/cleanup path.
- Ctrl+D closes the debug console, cancels simulations, and keeps Edge running.

The interface uses prompt-toolkit (`pip install -r AI/requirements.txt` from
the project root). It has been installed in the existing project virtualenv.
Redirected/noninteractive input or an unavailable terminal backend falls back
to the plain console. Without `--debug`, terminal output and application startup
remain unchanged.

## Explicit pump rejection logs

Updated node firmware now reports the exact first failing condition instead of
"start failed or already running": cooldown (with remaining milliseconds), hourly
run limit, hourly duration budget (used/requested/limit), already running,
invalid duration, full pending audit journal, unavailable/persist-failed safety
state, uninitialized PWM, or zero PWM duty. Existing thresholds, limits and
actuation checks are unchanged.

For Edge commands the node publishes `pump_start_rejected` with the original
command ID, reason, detail and retry delay. Edge checks event freshness and
correlates it to the pending start, logs `START REJECTED`, clears the pending
request and updates V18 detail. It does not invent a pump ON/OFF state or emit
a misleading later ACK-timeout warning for that rejected request.

Example: `START REJECTED node=... reason=cooldown detail=Wait 240000 ms after
last stop retry_after_ms=240000`. Reflash Things and restart Edge to use these
diagnostics. No hardware actuation or firmware upload was performed during
verification.

Capture failures now appear as `CAPTURE FAILED node=... seq=... code=... reason=...` in Edge logs. Reflash both Things and Camera for specific S3 error reasons; see `Camera/README.md`.

The model's 30-minute post-fallback quarantine does **not** block
`simulate_node_connection_lost`. Debug checks fresh config/audit readiness
independently, plus confirmed pump OFF and no pending command. Node cooldown,
per-episode rearming and hourly budgets still apply to its actual pump decision.

## Pump dispatch/result diagnostics

In connected mode the Edge now processes inbound MQTT state/ACKs and expires
stale pump state before executing due debug pump commands. A debug start
independently checks the last OFF report is less than 15 seconds old. This
removes decisions made using the preceding loop's state.

Terminal outcomes now distinguish `DEBUG PUMP NOT SENT` (with the failed
precondition), `QUEUED`/`COMMAND PENDING` (with command ID), `COMMAND RESULT`
(confirmed ON or explicit node rejection), and `COMMAND UNCONFIRMED` (ACK timed
out, physical state not established). There is no automatic resend of a start.
A scheduled dispatch still expires if processing is over two seconds late.
The node's remaining hourly **duration** limit is separate from the retired
run-count limit: its default is 60,000 ms, in addition to 30-second cooldown.
