# Irrigation control and confirmation

The combined Edge launcher owns irrigation policy. Blynk supplies a request for
one bound node; it does not authorize a physical state by itself. Run the Edge
with `python "Cloud Edge/run_edge.py"`. Each Blynk device token maps to one Edge
node, and commands are sent only to that node's local MQTT topic.

| Pin | Name | Behavior |
| --- | --- | --- |
| V3 | `pump_command` | `1` requests one 30 second manual run; `0` requests stop and blocks automatic starts. |
| V17 | `auto_irrigation_enabled` | `1` restores automatic decisions; `0` selects manual OFF and requests stop. |
| V4 | `pump_status` | Last state reported by the firmware controller (`0`/`1`). It does not prove water flow. |
| V18 | `pump_status_detail` | Text status: pending, confirmed, or unknown. |

V3 `1` sets manual mode. If the pump is already running, it does not extend the
run. A start requires a fresh firmware OFF event; unknown state cannot cause an
automatic or manual start. Automatic predictions are capped at the firmware's
60 second per-command limit; a later sample may authorize another run. The
saved `irrigation_status` Boolean carries the mode: `true` is Auto and `false`
is Manual OFF. Existing saved values follow this mapping; missing values default
to Auto. The launcher publishes the saved mode to V17 on startup.

The Edge adds a unique `command_id`, node ID, 10 second TTL, and bounded duration
to local MQTT commands. Firmware checks node and Edge identity, time and TTL,
rejects invalid durations, and persists the last start ID to avoid starting the
same request twice after restart. Firmware publishes boot ID, increasing sequence,
command ID, run ID, and short-lived `pump_started`/`pump_stopped` events. The Edge
accepts fresh events, correlates responses to pending commands, and only then
updates V4. A command acknowledgement timeout sets V18 to unknown and prevents
another start until fresh device state arrives; commands are not blindly retried.
Automatic mode restoration while the pump is running waits for a confirmed stop.

Firmware repeats its controller state every five seconds on local MQTT. After
15 seconds without a valid event, Edge marks the state unknown and blocks starts.
On local MQTT disconnect, the Edge clears its known state and pending commands.
Firmware continues to enforce the bounded run locally. A reconnect publishes a
fresh firmware state; old dashboard ON values are not replayed. V4 is the last
confirmed controller state and may remain stale during a disconnect; V18 carries
the current uncertainty.

The Cloud MQTT callback validates and queues requests. The dispatcher checks
expiry, connection generation, node binding, and registered Edge profile before
calling the Edge coordinator. The Edge coordinator and profile adapter share an
operation lock in the combined launcher.

## Verification

Run `PYTHONPATH="AI:Cloud Edge" python -m unittest discover -s "Cloud Edge/tests" -t "Cloud Edge" -q`
and `PYTHONPATH="AI:Cloud Edge" python -m unittest AI/test_pump_control.py AI/test_cloud_reporter.py -q`.
These are offline tests. Firmware compilation and real pump behavior require the
hardware build environment and device test. Wire V3 and V17 as separate switch
controls, V4 as state display, and V18 as text status in the Blynk dashboard.
