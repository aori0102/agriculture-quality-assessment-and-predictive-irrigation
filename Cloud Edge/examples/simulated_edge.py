"""Explicit live Blynk test; all Edge data and pump execution are simulated."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import select
import sys
from time import monotonic, sleep

from cloud_edge import CloudClient, CloudConfig, CloudError, illustrative_preset
from cloud_edge.blynk_http import BlynkHTTP
from .profile_backend import MemoryBackend


DEFAULT_DATASET = Path(__file__).with_suffix(".json")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class _PreviewHTTP(BlynkHTTP):
    """Reuse the actual adapter's validation/encoding without network I/O."""

    def _request(self, path, params, **kwargs):
        self.values = dict(params)


def load_dataset(path, node_id):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Dataset must be a nonempty JSON array.")
    fields = {"soil_index_pct", "temperature_c", "air_humidity_pct",
              "plant_status", "predicted_moisture_pct"}
    prepared = []
    preview = _PreviewHTTP(CloudConfig("sgp1.blynk.cloud", "offline-preview"))
    try:
        for row in rows:
            if not isinstance(row, dict) or set(row) != fields | {"scenario", "node_id"}:
                raise ValueError("Each row needs scenario, node_id, and all five snapshot fields.")
            if row["node_id"] not in ("$NODE_ID", node_id):
                raise ValueError("Dataset node_id does not match --node-id.")
            if not isinstance(row["scenario"], str) or not row["scenario"].isprintable():
                raise ValueError("Scenario must be nonempty printable text.")
            if not isinstance(row["plant_status"], str) or not row["plant_status"].startswith("SIMULATED:"):
                raise ValueError("plant_status must start with SIMULATED:.")
            values = {key: row[key] for key in fields}
            preview.values = {}
            preview.send_snapshot(**values)
            prepared.append({"scenario": row["scenario"], "values": {"node_id": node_id, **values},
                             "expected": preview.values})
    finally:
        preview.close()
    return prepared


class SimulatedPump:
    """Intent and simulator state are separate; no hardware transport exists."""

    def __init__(self, node_id):
        self.node_id = node_id
        self.pending = None
        self.generation = None
        self.is_on = False

    def receive(self, command, generation):
        if command.node_id != self.node_id:
            raise ValueError("Pump request targets another node.")
        self.pending, self.generation = command, generation

    def withhold(self):
        self.pending = None

    def acknowledge(self, generation, ready):
        command = self.pending
        self.pending = None
        if (command is None or command.is_expired() or not ready
                or generation != self.generation):
            raise ValueError("No current request to acknowledge; toggle V3 again.")
        self.is_on = command.requested_on
        return self.is_on


class Evidence:
    def __init__(self, stream, token, output=print):
        self.stream, self.token, self.output = stream, token, output
        self.results = {f"V{index}": "NOT TESTED" for index in range(17)}

    def emit(self, event, **fields):
        # Redact before JSON encoding so escaping cannot hide a token match.
        def redact(value):
            if isinstance(value, str):
                return value.replace(self.token, "[REDACTED]") if self.token else value
            if isinstance(value, dict):
                return {redact(key): redact(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [redact(item) for item in value]
            return value
        line = json.dumps(redact({"at": utc_now(), "event": event, **fields}), ensure_ascii=True)
        self.stream.write(line + "\n")
        self.stream.flush()
        self.output(line)

    def mark(self, pin, result, note):
        pin, result = pin.upper(), result.upper()
        if pin not in self.results or result not in ("PASS", "FAIL") or not note.strip():
            raise ValueError("Use: mark V0 PASS|FAIL observation (V0 through V16).")
        self.results[pin] = result
        self.emit("dashboard_observation", pin=pin, result=result, observation=note)


HELP = """Commands (press Enter after each):
  replay                 Replay the dataset once; final values remain displayed
  ack                    Acknowledge the latest unexpired SIMULATED pump request
  withhold               Discard the request without changing simulated pump state
  profile                Show the fake backend's current profile
  mark V0 PASS observed  Record a dashboard observation (V0..V16, PASS or FAIL)
  summary                Show all 17 results
  finish                 Report simulated OFF, close, and save results
  help                   Show these commands
Use Cloud Edge/LIVE_TEST.md for the full dashboard checklist.
"""


class Session:
    def __init__(self, client, backend, node_id, rows, interval, evidence, worker):
        self.client, self.backend, self.node_id = client, backend, node_id
        self.rows, self.interval, self.evidence, self.worker = rows, interval, evidence, worker
        self.pump = SimulatedPump(node_id)
        self.future = None
        self.job = None
        self.index = 0
        self.next_sample = 0
        self.failed = False
        self.last_status = None
        self.last_connection = None
        self.last_errors = None

    def submit(self, event, method, **kwargs):
        if self.future is not None:
            raise ValueError("Upload in progress; retry after its result appears.")
        self.job = event
        self.future = self.worker.submit(method, **kwargs)

    def collect(self, wait=False):
        if self.future is None or (not wait and not self.future.done()):
            return
        try:
            self.future.result()
            self.evidence.emit("upload_success", operation=self.job)
        except (CloudError, ValueError) as error:
            self.failed = True
            self.evidence.emit("upload_failed", operation=self.job,
                               category=getattr(error, "category", "validation"))
        finally:
            self.future = None

    def tick(self):
        self.collect()
        generation = self.client.command_connection_id
        connection = (generation, self.client.commands_ready, self.client.profiles_ready)
        if connection != self.last_connection:
            self.pump.withhold()
            self.evidence.emit("connection", generation=generation,
                               commands_ready=connection[1], profiles_ready=connection[2])
            self.last_connection = connection
        self.receive_request()
        if self.pump.pending is not None and self.pump.pending.is_expired():
            self.pump.withhold()
            self.evidence.emit("pump_request_expired", simulated_state=self.pump.is_on)
        errors = tuple(getattr(error, "category", None) for error in
                       (self.client.last_command_error, self.client.last_profile_error))
        if errors != self.last_errors:
            if any(errors):
                self.evidence.emit("transport_error", mqtt=errors[0], profile=errors[1])
            self.last_errors = errors
        status = self.client.last_profile_status
        if status != self.last_status:
            self.evidence.emit("profile_status", status=status)
            self.show_profile()
            self.last_status = status
        if (self.future is None and self.index < len(self.rows) and monotonic() >= self.next_sample
                and self.client.commands_ready and self.client.profiles_ready):
            row = self.rows[self.index]
            self.evidence.emit("sample", scenario=row["scenario"], sampled_at=utc_now(),
                               values=row["values"], expected=row["expected"])
            self.submit(row["scenario"], self.client.send_snapshot, **row["values"])
            self.index += 1
            self.next_sample = monotonic() + self.interval

    def receive_request(self):
        generation = self.client.command_connection_id
        command = self.client.poll_command()
        if command is not None and self.client.commands_ready:
            self.pump.receive(command, generation)
            self.evidence.emit("pump_request", node_id=command.node_id, requested_on=command.requested_on,
                               expires_at=command.expires_at.isoformat(), instruction="ack or withhold")

    def show_profile(self):
        active = self.backend.get_active(self.node_id)
        self.evidence.emit("simulated_backend", values=active.profile.to_datastreams() if active else None,
                           revision=active.revision if active else None)

    def handle(self, line):
        parts = line.strip().split(maxsplit=3)
        if not parts:
            return True
        action = parts[0].lower()
        if action == "finish":
            return False
        if action == "ack":
            if self.future is not None:
                raise ValueError("Upload in progress; retry ack before expiry or toggle V3 again.")
            self.receive_request()  # Include intent received while waiting for terminal input.
            state = self.pump.acknowledge(self.client.command_connection_id, self.client.commands_ready)
            self.evidence.emit("simulated_ack", is_on=state)
            self.submit("pump_status", self.client.report_pump_status, node_id=self.node_id, is_on=state)
        elif action == "withhold":
            self.pump.withhold()
            self.evidence.emit("ack_withheld", simulated_state=self.pump.is_on)
        elif action == "replay":
            if self.future is not None or self.index < len(self.rows):
                raise ValueError("Wait for the current replay to finish.")
            self.index, self.next_sample = 0, 0
            self.evidence.emit("replay_started")
        elif action == "profile":
            self.show_profile()
        elif action == "mark" and len(parts) == 4:
            self.evidence.mark(*parts[1:])
        elif action == "summary":
            self.evidence.emit("summary", results=self.evidence.results)
        elif action == "help":
            print(HELP)
        else:
            raise ValueError("Unknown or incomplete command; type help.")
        return True

    def finish(self):
        self.collect(wait=True)
        self.pump.withhold()
        self.pump.is_on = False
        self.evidence.emit("simulated_ack", is_on=False, reason="session shutdown")
        try:
            self.client.report_pump_status(node_id=self.node_id, is_on=False)
            self.evidence.emit("final_off_uploaded", reminder="Return dashboard V3 to OFF.")
        except (CloudError, ValueError) as error:
            self.failed = True
            self.evidence.emit("final_off_failed", category=getattr(error, "category", "validation"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--log", type=Path, help="New .log file (default: Cloud Edge/runtime/live-<UTC>.log)")
    args = parser.parse_args(argv)
    if not 0 < args.interval < math.inf:
        parser.error("--interval must be positive and finite")
    if args.log and args.log.suffix != ".log":
        parser.error("--log must end in .log (ignored by Git)")
    client = None
    try:
        rows = load_dataset(args.dataset, args.node_id)
        backend = MemoryBackend()
        backend.apply_profile(illustrative_preset(args.node_id, "SIMULATED: live test"))
        config = CloudConfig.from_env()
        log_path = args.log or DEFAULT_DATASET.parent.parent / "runtime" / (
            datetime.now(timezone.utc).strftime("live-%Y%m%dT%H%M%S-%f.log"))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("x", encoding="utf-8") as stream, ThreadPoolExecutor(max_workers=1) as worker:
            evidence = Evidence(stream, config.auth_token)
            evidence.emit("start", node_id=args.node_id, log=str(log_path), simulation=True)
            client = CloudClient(config, node_id=args.node_id, profile_backend=backend)
            session = Session(client, backend, args.node_id, rows, args.interval, evidence, worker)
            try:
                client.start()
                deadline = monotonic() + 60
                while not (client.commands_ready and client.profiles_ready):
                    error = client.last_command_error
                    if error and error.category != "network":
                        raise error
                    if monotonic() >= deadline:
                        raise CloudError("Readiness timed out", category="timeout")
                    sleep(0.1)
                evidence.emit("ready", instruction="Use the dashboard and terminal checklist.")
                session.submit("initial_simulated_off", client.report_pump_status,
                               node_id=args.node_id, is_on=False)
                print(HELP)
                running = True
                while running:
                    session.tick()
                    readable, _, _ = select.select([sys.stdin], [], [], 0.1)
                    if readable:
                        line = sys.stdin.readline()
                        if not line:  # EOF still performs OFF/close cleanup.
                            break
                        try:
                            running = session.handle(line)
                        except ValueError as error:
                            evidence.emit("input_error", message=str(error))
            except KeyboardInterrupt:
                evidence.emit("interrupted")
            except (CloudError, ValueError) as error:
                session.failed = True
                evidence.emit("session_failed", category=getattr(error, "category", "validation"))
            finally:
                try:
                    session.finish()
                finally:
                    client.close()
                    evidence.emit("summary", results=evidence.results, transport_failed=session.failed)
            if session.failed or "FAIL" in evidence.results.values():
                return 1
            return 0 if all(v == "PASS" for v in evidence.results.values()) else 2
    except (OSError, ValueError, CloudError):
        # Configuration/file errors may contain user data; do not print raw exceptions.
        print("Setup failed: check dataset, node identity, .env, and new log path.", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
