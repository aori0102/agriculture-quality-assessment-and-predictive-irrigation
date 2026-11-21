# Edge / AI terminal logs

Application logs use Python logging and stderr:

```text
[2026-11-21 20:30:12] [Telemetry] ACCEPTED node=one soil=42.0% temp=28.5C humidity=65.0% sampled_at=...
[2026-11-21 20:30:12] [Command] QUEUED node=one action=start_pump duration_ms=6000 ttl_ms=300000
[2026-11-21 20:30:12] [Cloud] PUMP node=one state=ON | node event accepted
[2026-11-21 20:30:13] [Cloud] Cloud upload failed: TimeoutError
```

Time uses local system time, formatted as `[YYYY-MM-DD HH:MM:SS]`, followed
immediately by the component in brackets. Level names are not printed. On an
interactive terminal, INFO is green, WARNING yellow, ERROR/CRITICAL red and
DEBUG cyan. Redirected output stays plain text; set NO_COLOR to disable colors.
Cloud Edge's existing logger messages use `[Cloud]` after AI initializes.
Standalone CLI check/status output and external training subprocess/third-party
library output retain their own format.

Default level is INFO. Repeated irrigation polling messages and full MQTT payloads
are DEBUG. To show details in PowerShell before starting the Edge application:

```powershell
$env:EDGE_LOG_LEVEL = "DEBUG"
python "Cloud Edge/run_edge.py"
```

Use INFO to restore normal output. WARNING and ERROR levels can further reduce
output. ANSI colors are emitted only for interactive terminal output.
Embedded newlines/control characters are escaped to keep one event per line.
No root logger settings or global print hooks are changed. Logs do not create
files or modify operation_log.json. QUEUED means MQTT accepted a publish request;
PUMP means a validated node event was accepted, not a measured water-flow result.

Tests cover repeated initialization, formatting, line injection, event routing,
telemetry acceptance, command policy and model lifecycle. Hardware/live broker
behavior is not tested by these logging changes.
