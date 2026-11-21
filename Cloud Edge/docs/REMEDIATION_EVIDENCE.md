# Dynamic Blynk remediation evidence — 20 November 2026

Base commit: `782c459`. The user-provided remediation plan was untracked when work began and was left untouched. No live Blynk create/delete, Edge MQTT publish, or hardware actuation was run during this remediation.

## Phases and checks

| Phase | Changes | Verification |
| --- | --- | --- |
| Startup | Restored missing `ensure_env_file`/`ENV_FILE` imports; checked static and dynamic `--check` paths; made a newly created `.env` private. | Baseline Cloud suite had 3 launcher errors; startup tests now pass. |
| Reporter | One live registry feeds static and dynamic uploads. Accepted telemetry routes once to `CloudReporter`; provisioning hands off its first snapshot after activation. AI prediction and confirmed pump event call sites remain on the reporter. | Static and dynamic mock upload tests; both suites pass. |
| Lifecycle | Added private create-intent journal, single-process lock, sanitized reconciliation logs, status CLI and `active_local`/`commands_ready` distinction. Moved reusable OAuth storage into `cloud_edge`. | Crash/restart, duplicate observation, lock, status and store tests pass. |
| Profiles | Blynk fractions convert to Edge percentage thresholds at the adapter boundary; saved AI profiles were not rewritten. Ambiguous legacy fraction records fail visibly. | Percentage default, write/readback, invalid legacy and isolated real Edge contract tests pass. |
| Documentation | Static, dynamic-only and hybrid setup, public broker spoofing risk, operator reconciliation and regional-host limits documented. | `git diff --check` passes. |

Final offline checks: `PYTHONPATH='AI:Cloud Edge' .venv/bin/python -m unittest discover -s 'Cloud Edge/tests' -t 'Cloud Edge' -q` passed **130 tests**; `PYTHONPATH='AI:Cloud Edge' .venv/bin/python -m unittest discover -s AI -p 'test_cloud_reporter.py' -q` passed **8 tests**. `Cloud Edge/run_edge.py --check` passed with provisioning disabled and enabled. Tests use mocks and temporary stores; these results do not establish physical ESP32 or live Blynk end-to-end behavior.

## Remaining manual verification

Historical note: this was written before open enrollment. For current setup and the fresh-node runbook, use [DYNAMIC_PROVISIONING.md](DYNAMIC_PROVISIONING.md). The public MQTT broker still permits node-ID spoofing; this prototype requires a controlled lab or a separately coordinated authenticated broker.
