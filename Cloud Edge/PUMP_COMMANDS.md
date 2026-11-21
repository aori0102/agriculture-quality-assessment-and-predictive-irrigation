# Blynk irrigation requests → Edge flag

The same-process launcher now consumes Blynk V3 (`pump_command`) requests and
calls the existing AI proxy functions:

| Request | Edge function |
| --- | --- |
| ON (`1`) | `enable_irrigation_on_node(node_id=...)` |
| OFF (`0`) | `disable_irrigation_on_node(node_id=...)` |

Those functions save `auto_irrigation_enabled` in the Edge operation log.
**Saved intent is not physical pump state.** This integration sends no direct
pump actuation commands and never writes V4 (`pump_status`) or V15 (profile
status). It adds no datastream. Existing hardware-confirmed V4 reporting remains
available separately through `CloudClient.report_pump_status()`.

## Startup and ownership

```bash
python "Cloud Edge/run_edge.py" --check
python "Cloud Edge/run_edge.py"
```

The existing AI configuration prerequisite documented in [PROFILES.md](PROFILES.md)
still applies. Normal startup runs AI's existing application behavior; this is
not a hardware dry run. Cloud Edge owns the dispatcher and adapters; AI owns
internal storage and consumption of the saved irrigation flag.

The launcher constructs a single shared `threading.RLock` and injects it into
both `EdgeProfileBackend(..., operation_lock=...)` and
`EdgePumpBackend(enable, disable, load_log, operation_lock=...)`. Use the same
lock and one pump adapter when embedding these components manually. Independent
AI writers use the operation log manager's own reentrant lock for atomic read/modify/write operations. The final irrigation permission check and command publish also hold that lock.

`PumpDispatcher(clients_by_node_id, backend, interval=0.1)` polls all configured
clients in one background thread. Start it after clients are constructed. Close
it before closing those clients. Startup failures and normal launcher shutdown
perform cleanup in that order. An already-entered Edge operation finishes before
the dispatcher joins; injected Edge functions must return promptly.

## Request semantics

- The MQTT callback validates and retains only the latest request per node.
- The dispatcher acquires the operation lock nonblockingly **before polling**.
  While a profile operation holds it, a queued OFF can replace ON and disconnect
  can clear pending intent.
- The adapter validates node binding and registry membership, checks the saved
  flag's type, and rechecks expiry, connection readiness/generation, and shutdown
  after reading Edge state and before updating it.
- A saved flag already matching the request skips the write. A missing flag on
  a registered node can be set by a fresh request; a malformed non-Boolean flag
  is reported as a synchronization failure.
- After a write, exact Boolean readback is required. Exceptions/readback mismatch
  report uncertain state; writes are not automatically retried.
- Retained commands, expired requests, and queued commands cleared by disconnect
  are not replayed. Startup never converts stored flags or V3 values into requests.
- An OFF received during an already-entered Edge update is processed afterward.
  That update cannot be cancelled safely. This adapter is not an emergency-stop
  mechanism and does not claim physical stopping behavior.

The default command lifetime remains five seconds from local MQTT receipt;
there is no server-side issue timestamp. `CloudClient.command_connection_id`
is a read-only connection generation used to invalidate work across reconnect.

## Local reporting

`dispatcher.last_result(node_id)` returns an immutable `PumpRequestResult`:
`node_id`, `requested_on`, `outcome`, and `message`. Outcome is one of `saved`,
`unchanged`, `rejected`, `sync_failed`, or `discarded`. `requested_on` is `None`
only for an unexpected dispatch-layer error where no valid request is known.

`dispatcher.last_error(node_id)` returns the latest rejection/synchronization
error message, or `None`. Results are also emitted through the
`cloud_edge.edge_pump` logger: INFO for normal results, WARNING for failures.
Enable INFO logging in the host application to view successful saves. Messages
never include raw Edge exceptions or credentials. There is deliberately no
Blynk result-stream write for this flag-saving operation.

## Verification and limits

```bash
PYTHONPATH="Cloud Edge" python -m unittest discover -s "Cloud Edge/tests" -t "Cloud Edge" -v
```

Offline tests exercise mapping, exact readback, duplicate handling, unknown
nodes, expiry/reconnect, lock contention, and shutdown ordering. The actual AI
proxy's enable/disable functions are exercised against a temporary operation log.
No live broker or hardware is used.

The AI storage writer uses atomic replacement. OFF prevents future automatic irrigation; it does not cancel an active run. New nodes default to OFF, while legacy Boolean flags are migrated on read. The node enforces a maximum 60-second run at raw PWM duty 100/255. Successful starts/stops and successful captures are reported on `/events`; see the README for the event contract. Physical pump flow and live delivery still require hardware testing.
