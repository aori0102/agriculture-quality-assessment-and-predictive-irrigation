from configparser import ConfigParser
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

import joblib
import pandas as pd

from time_helper import TimeHelper
from . import field_constants as fc


def load_validated_model(path: Path):
    model = joblib.load(path)
    sample = {
        fc.SOIL_MOISTURE: 40.0, fc.WEATHER_TEMPERATURE: 25.0,
        fc.WEATHER_HUMIDITY: 60.0, fc.IRRIGATION_PAST_10M: 0.0,
        fc.IRRIGATION_DURATION_MINUTES: 0.0, fc.SOIL_CHANGE_10M: 0.0,
    }
    fields = list(model.feature_names_in_)
    if set(fields) != set(sample) or len(fields) != len(sample):
        raise ValueError("New model input columns differ from the irrigation inputs")
    prediction = float(model.predict(pd.DataFrame([sample], columns=fields))[0])
    if not isfinite(prediction) or not 0 <= prediction <= 100:
        raise ValueError("New model returned an invalid moisture prediction")

    return model


def model_files(directory: Path) -> list[Path]:
    """Discover original and retrained models in the single model store."""
    models_dir = (directory / "models").resolve()
    return sorted(
        (p for p in models_dir.glob("*.joblib")
         if p.is_file() and not p.is_symlink() and p.resolve().parent == models_dir),
        key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True,
    )


def resolve_model_path(directory: Path, stored: str) -> Path:
    # Accept legacy config/log paths by filename, but only load from models/.
    name = stored.replace("\\", "/").rsplit("/", 1)[-1]
    path = (directory / "models" / name).resolve()
    if path.parent != (directory / "models").resolve() or path.suffix != ".joblib":
        raise ValueError("Model must be a .joblib file inside models/")
    return path


def save_model_path(directory: Path, path: Path, log) -> None:
    path = resolve_model_path(directory, str(path))
    log.set_model_path(path.relative_to(directory.resolve().parent).as_posix())


def prune_models(directory: Path, *protected: Path, log) -> None:
    """Keep three managed models, reserving slots for active/pending models."""
    try:
        status = log.get_model_status()
        active = status.get("model_path", "")
        keep = {path.resolve() for path in protected}
        if active:
            keep.add(resolve_model_path(directory, active))
        pending = status.get("new_model_path", "")
        if pending:
            keep.add(resolve_model_path(directory, pending))
        models = model_files(directory)
        keep.intersection_update(p.resolve() for p in models)
        for path in models:
            if len(keep) >= 3:
                break
            keep.add(path.resolve())
        for path in models:
            if path.resolve() not in keep:
                try:
                    path.unlink()
                    print(f"Removed old irrigation model {path.name}")
                except OSError as error:
                    print(f"Could not remove old model {path.name}: {error}")
    except (OSError, ValueError) as error:
        print(f"Could not clean up irrigation models: {error}")


def load_or_train_initial_model(directory: Path, log):
    """Block startup until a validated model is available; fail if training fails."""
    configured = log.get_model_status().get("model_path", "")
    if not configured:
        # One-way compatibility for a config from before model_path moved to the log.
        config = ConfigParser()
        config.read(directory.parent / "config.cfg", encoding="utf-8-sig")
        configured = config.get("irrigation_model", "model_path", fallback="")
    candidates = []
    if configured:
        try:
            candidates.append(resolve_model_path(directory, configured))
        except ValueError as error:
            print(f"Invalid configured model path: {error}")
    candidates.extend(p for p in model_files(directory) if p not in candidates)
    for path in candidates:
        try:
            model = load_validated_model(path)
        except Exception as error:
            print(f"Cannot load irrigation model {path.name}: {error}")
            continue
        relative_path = path.relative_to(directory.resolve().parent).as_posix()
        if relative_path != log.get_model_status().get("model_path", ""):
            save_model_path(directory, path, log)
        return model
    print("No usable model in models/. Training from base datasets; waiting...")

    models = directory / "models"
    models.mkdir(exist_ok=True)
    candidate = models / f"irrigation_{uuid4().hex}.joblib"
    committed = False
    try:
        subprocess.run(
            [sys.executable, str(directory / "random_forest_train.py"),
             "--train", str(directory / "datasets" / "train_data.csv"),
             "--test", str(directory / "datasets" / "test_data.csv"),
             "--output", str(candidate), "--jobs", "1", "--no-plot"],
            cwd=directory, check=True, timeout=3600,
        )
        model = load_validated_model(candidate)
        save_model_path(directory, candidate, log)
        committed = True
        # A recovery model supersedes an interrupted/pending retrain from the last run.
        log.clear_model_status()
        prune_models(directory, candidate, log=log)
        print(f"Initial irrigation model ready: {candidate.name}. Continuing Edge setup.")
        return model
    except Exception as error:
        raise RuntimeError("Cannot prepare irrigation model; Edge setup stopped") from error
    finally:
        if not committed:
            candidate.unlink(missing_ok=True)


class ModelRetrainer:
    """Poll the two training subprocesses and activate a candidate on the Edge thread."""

    def __init__(self, log, irrigation, data_path: Path, *, debug_hourly: bool):
        self.log = log
        self.irrigation = irrigation
        self.data_path = data_path
        self.debug_hourly = debug_hourly
        self.directory = Path(__file__).resolve().parent
        status = log.get_model_status()
        self.last_period = status.get("last_retrain_period")
        if self.last_period is None:
            self.last_period = self._period(datetime.now(timezone.utc))
            log.initialize_retrain_period(self.last_period)
        self.waiting_for_swap = bool(status["new_model_ready"])
        self.retry_interrupted = status["new_model_pending"] and not self.waiting_for_swap
        self.process = None
        self.workspace = None
        self.candidate = None
        self.stage = None

    def _period(self, now: datetime) -> str:
        return now.strftime("%Y-%m-%dT%H" if self.debug_hourly else "%Y-%m-%d")

    def _launch(self, stage: str, script: str, *args) -> None:
        self.process = subprocess.Popen(
            [sys.executable, str(self.directory / script), *map(str, args)],
            cwd=self.directory,
        )
        self.stage = stage
        self.deadline = monotonic() + 3600

    def _start(self, period: str, now: datetime) -> None:
        self.last_period = period
        self.retry_interrupted = False
        self.log.log_model_training(period)
        self.workspace = TemporaryDirectory(prefix="edge-retrain-")
        models = self.directory / "models"
        models.mkdir(exist_ok=True)
        self.candidate = models / f"irrigation_{uuid4().hex}.joblib"
        self._launch(
            "generate", "train_test_generator.py",
            "--edge-log", self.data_path.resolve(),
            "--base-train", self.directory / "datasets" / "train_data.csv",
            "--output-dir", self.workspace.name,
            "--closed-before", TimeHelper.format_time(TimeHelper.get_slot(now)),
        )
        print(f"Started irrigation retrain for UTC period {period}")

    def _poll(self) -> None:
        result = self.process.poll()
        if result is None:
            if monotonic() >= self.deadline:
                raise RuntimeError(f"{self.stage} timed out")
            return
        self.process = None
        if result != 0:
            raise RuntimeError(f"{self.stage} exited with code {result}")
        if self.stage == "generate":
            data = Path(self.workspace.name)
            self._launch(
                "train", "random_forest_train.py",
                "--train", data / "train_data.csv", "--test", data / "test_data.csv",
                "--output", self.candidate, "--jobs", 1, "--no-plot",
            )
            return
        if not self.candidate.is_file():
            raise RuntimeError("Training did not produce a model file")
        load_validated_model(self.candidate)
        self._close_workspace()
        self.log.log_model_ready(str(self.candidate.relative_to(self.directory)))
        self.waiting_for_swap = True
        prune_models(self.directory, self.candidate, log=self.log)
        self.candidate = None

    def _activate(self) -> None:
        status = self.log.get_model_status()
        path = resolve_model_path(self.directory, status["new_model_path"])
        model = load_validated_model(path)
        save_model_path(self.directory, path, self.log)
        self.irrigation.model = model
        print(f"Activated irrigation model {path.name}")
        prune_models(self.directory, path, log=self.log)

    def tick(self, *, can_swap: bool, now: datetime | None = None) -> bool:
        """Run once between MQTT iterations; return True if this turn is for swapping."""
        if self.waiting_for_swap:
            if not can_swap:
                return False
            try:
                self._activate()
            except Exception as error:
                print(f"Could not activate new irrigation model: {error}")
            self.log.clear_model_status()
            self.waiting_for_swap = False
            return True

        now = now or datetime.now(timezone.utc)
        period = self._period(now)
        try:
            if self.process is not None:
                self._poll()
            elif self.retry_interrupted or period != self.last_period:
                self._start(period, now)
        except Exception as error:
            print(f"Irrigation retrain failed: {error}")
            self._cancel()
            self.log.clear_model_status()
        return False

    def _close_workspace(self) -> None:
        if self.workspace is not None:
            self.workspace.cleanup()
            self.workspace = None

    def _cancel(self) -> None:
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait()
            self.process = None
        self._close_workspace()
        if self.candidate is not None:
            self.candidate.unlink(missing_ok=True)
            self.candidate = None

    def close(self) -> None:
        if self.process is not None:
            self._cancel()
            # Leave pending persisted so the next Edge run retries the interrupted job.
