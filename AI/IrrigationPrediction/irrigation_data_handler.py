import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from threading import RLock
from functools import wraps
import os
import tempfile
from math import isfinite
from time_helper import TimeHelper
from . import field_constants as fc

LOG_FILE = "data_log.csv"
LOG_FIELDS = (
    fc.TIMESTAMP,
    fc.NODE_ID,
    fc.WEATHER_TEMPERATURE,
    fc.WEATHER_HUMIDITY,
    fc.SOIL_MOISTURE,
    fc.IRRIGATION_DURATION_MINUTES
)

def synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class IrrigationDataHandler:
    def __init__(self):
        self._lock = RLock()
        self.last_fetch_error: str | None = None
        self.initialize_log()
            

    def initialize_log(self):
        self.data_path = Path(__file__).with_name(LOG_FILE)
        if self.data_path.exists() and self.data_path.stat().st_size > 0:
            self.data_frame = pd.read_csv(self.data_path, dtype={fc.NODE_ID: str})
        else:
            self.data_frame = pd.DataFrame(columns=LOG_FIELDS)
            self.save_data()
        
        print("Initialized irrigation data log.")

    @synchronized
    def save_data(self):
        # Replace atomically so an interrupted write cannot truncate the history.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                             dir=self.data_path.parent, delete=False) as file:
                temporary = Path(file.name)
                self.data_frame.to_csv(file, index=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.data_path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @staticmethod
    def _is_empty(value) -> bool:
        return pd.isna(value) or (isinstance(value, str) and not value.strip())

    @synchronized
    def has_irrigation_duration(self, node_id: str, timestamp: datetime | str) -> bool:
        slot = TimeHelper.get_slot(timestamp)
        times = self.data_frame[fc.TIMESTAMP].map(TimeHelper.get_slot)
        rows = self.data_frame.loc[(self.data_frame[fc.NODE_ID] == node_id) & (times == slot)]
        return any(not self._is_empty(value) for value in rows[fc.IRRIGATION_DURATION_MINUTES])

    @synchronized
    def fill_expired_irrigation(
        self, now: datetime | str = None, *,
        closed_slot: datetime | None = None, node_ids: set[str] | None = None,
    ) -> int:
        """Close older rows and register known nodes missing from the window just closed."""
        now = now if now is not None else datetime.now(timezone.utc)
        current_slot = TimeHelper.get_slot(now)
        if current_slot is None:
            raise ValueError("Invalid time for closing irrigation windows")
        times = self.data_frame[fc.TIMESTAMP].map(TimeHelper.get_slot)
        count = 0
        for index, row in self.data_frame.iterrows():
            if not self._is_empty(row[fc.IRRIGATION_DURATION_MINUTES]):
                continue
            slot = times.loc[index]
            if TimeHelper.get_slot(slot) is None:
                raise ValueError(f"Invalid timestamp in irrigation log row {index}")
            if slot >= current_slot:
                continue
            # Preserve an existing decision if legacy duplicate rows share a window.
            same_window = (self.data_frame[fc.NODE_ID] == row[fc.NODE_ID]) & (times == slot)
            recorded = self.data_frame.loc[same_window, fc.IRRIGATION_DURATION_MINUTES]
            values = [value for value in recorded if not self._is_empty(value)]
            self.data_frame.at[index, fc.IRRIGATION_DURATION_MINUTES] = values[0] if values else 0.0
            count += 1
        if closed_slot is not None and closed_slot < current_slot and node_ids:
            recorded_nodes = set(self.data_frame.loc[times == closed_slot, fc.NODE_ID])
            missing_nodes = sorted(node_ids - recorded_nodes)
            for node_id in missing_nodes:
                self.data_frame.loc[len(self.data_frame)] = {
                    fc.TIMESTAMP: TimeHelper.format_time(closed_slot),
                    fc.NODE_ID: node_id,
                    fc.IRRIGATION_DURATION_MINUTES: 0.0,
                }
            count += len(missing_nodes)
        if count:
            self.save_data()
        return count
        

    @synchronized
    def get_irrigation_data(
        self, node_id: str, timestamp: datetime | str
    ) -> dict[str, str | float] | None:
        """Fetch the five observed prediction features from the in-memory log.

        Timestamps are normalized to UTC (naive timestamps are treated as UTC)
        and floored to 10-minute windows. The last logged row in each window
        is used. Both the requested window and the immediately preceding
        window must exist for the node, with finite values for every input.

        Irrigation in the previous window is irrigation_past_10m and must be
        present. The current window's irrigation duration is neither required
        nor returned: the irrigation handler supplies candidate durations.
        """
        self.last_fetch_error = None

        def unavailable(reason: str):
            self.last_fetch_error = reason
            return None

        if not isinstance(node_id, str) or not node_id:
            return unavailable("Invalid node_id")
        slot = TimeHelper.get_slot(timestamp)
        if slot is None:
            return unavailable("Invalid timestamp")
        previous_slot = TimeHelper.get_slot(slot, offset_windows=-1)
        data = self.data_frame
        missing_columns = set(LOG_FIELDS) - set(data.columns)
        if missing_columns:
            return unavailable(f"Missing columns: {', '.join(sorted(missing_columns))}")
        node_data = data.loc[data[fc.NODE_ID] == node_id]
        times = node_data[fc.TIMESTAMP].map(TimeHelper.get_slot)
        current_rows = node_data.loc[times == slot]
        previous_rows = node_data.loc[times == previous_slot]
        if current_rows.empty:
            return unavailable(f"No data for node {node_id} in window {TimeHelper.format_time(slot)}")
        if previous_rows.empty:
            return unavailable(f"No previous data for node {node_id} in window {TimeHelper.format_time(previous_slot)}")

        current = current_rows.iloc[-1]
        previous = previous_rows.iloc[-1]
        inputs = (
            (current, slot, fc.SOIL_MOISTURE),
            (previous, previous_slot, fc.SOIL_MOISTURE),
            (current, slot, fc.WEATHER_TEMPERATURE),
            (current, slot, fc.WEATHER_HUMIDITY),
            (previous, previous_slot, fc.IRRIGATION_DURATION_MINUTES),
        )
        values = []
        for row, window, field in inputs:
            try:
                value = float(row[field])
                if not isfinite(value):
                    raise ValueError("Non-finite value")
            except (TypeError, ValueError, OverflowError):
                return unavailable(
                    f"Missing/invalid {field} for node {node_id} in window {TimeHelper.format_time(window)}"
                )
            values.append(value)

        moisture, previous_moisture, temperature, humidity, irrigation = values
        delta = moisture - previous_moisture
        if not isfinite(delta):
            return unavailable("Non-finite soil moisture delta")
        return {
            fc.TIMESTAMP: TimeHelper.format_time(slot),
            fc.NODE_ID: node_id,
            fc.SOIL_MOISTURE: moisture,
            fc.SOIL_CHANGE_10M: delta,
            fc.WEATHER_TEMPERATURE: temperature,
            fc.WEATHER_HUMIDITY: humidity,
            fc.IRRIGATION_PAST_10M: irrigation,
        }


    @synchronized
    def log_data(
        self,
        node_id: str,
        timestamp: datetime,
        weather_temperature: float = None,
        weather_humidity: float = None,
        soil_moisture: float = None,
        irrigation_duration: float = None
    ):
        """Accept data only until a duration seals the node/window.

        Return True for accepted telemetry or a newly recorded live decision;
        False for sealed windows or an expired irrigation request.
        """
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("node_id must be a non-empty string.")
        slot = TimeHelper.get_slot(timestamp)
        if slot is None:
            raise ValueError("timestamp must be a valid datetime.")

        sample_times = self.data_frame[fc.TIMESTAMP].map(TimeHelper.get_slot)
        matching = (self.data_frame[fc.NODE_ID] == node_id) & (sample_times == slot)
        recorded = self.data_frame.loc[matching, fc.IRRIGATION_DURATION_MINUTES]
        existing_values = [value for value in recorded if not self._is_empty(value)]
        if existing_values:
            return False  # Freeze the entire sensor snapshot, including zero-duration decisions.
        expired = TimeHelper.is_window_expired(slot, datetime.now(timezone.utc))
        requested_duration = irrigation_duration
        if expired:
            irrigation_duration = 0.0
        if self._is_empty(irrigation_duration):
            irrigation_duration = None
        updates = {
            fc.WEATHER_TEMPERATURE: weather_temperature,
            fc.WEATHER_HUMIDITY: weather_humidity,
            fc.SOIL_MOISTURE: soil_moisture,
            fc.IRRIGATION_DURATION_MINUTES: irrigation_duration,
        }
        if matching.any():
            for field, value in updates.items():
                if value is not None:
                    if field == fc.IRRIGATION_DURATION_MINUTES:
                        empty = self.data_frame[field].map(self._is_empty)
                        self.data_frame.loc[matching & empty, field] = value
                    else:
                        self.data_frame.loc[matching, field] = value
            self.data_frame.loc[matching, fc.TIMESTAMP] = TimeHelper.format_time(slot)
        else:
            self.data_frame.loc[len(self.data_frame)] = {
                fc.TIMESTAMP: TimeHelper.format_time(slot),
                fc.NODE_ID: node_id,
                **updates,
            }

        duration_written = not existing_values and not expired and not self._is_empty(requested_duration)
        self.save_data()
        return self._is_empty(requested_duration) or duration_written


irrigation_data_handler = IrrigationDataHandler()