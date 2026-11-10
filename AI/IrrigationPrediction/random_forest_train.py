"""Train a 10-minute soil moisture forecast from sensor CSV files.

Sensor CSV (one or more): timestamp, sector, soil_moisture_pct, air_temp_c,
air_rh_pct, irrigation_ml. Irrigation is the volume in the 10-minute interval
ending at timestamp. Use zero for a confirmed interval without irrigation.
Mendeley dataset_zone_N_preprocessed.csv files are also supported. These use
irrigation duration in minutes. Use one irrigation unit per training run.

Naive sensor timestamps use --sensor-timezone (default UTC).
"""

import argparse
from pathlib import Path
import re

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error

FEATURES = [
    "soil_moisture_pct",
    "soil_change_10m",
    "air_temp_c",
    "air_rh_pct",
    "irrigation_10m",
]
HORIZON = pd.Timedelta(minutes=10)


def prepare_sensors(paths, sensor_timezone="UTC"):
    frames = []
    irrigation_columns = set()
    for path in paths:
        frame = pd.read_csv(path)
        irrigation_column = "irrigation_ml"
        zone = re.fullmatch(r"dataset_zone_(\d+)_preprocessed", Path(path).stem)
        if zone and "ts" in frame:
            frame = frame.rename(columns={
                "ts": "timestamp",
                "soil_moisture": "soil_moisture_pct",
                "weather_temp": "air_temp_c",
                "weather_humidity": "air_rh_pct",
            })
            frame["sector"] = int(zone.group(1))
            irrigation_column = "irrigation_duration_minutes"

        columns = [
            "timestamp", "sector", "soil_moisture_pct", "air_temp_c", "air_rh_pct",
            irrigation_column,
        ]
        missing = set(columns) - set(frame.columns)
        if missing:
            raise ValueError(f"{path}: missing columns {', '.join(sorted(missing))}")
        frame = frame[columns].copy()
        if sensor_timezone == "UTC":
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        else:
            timestamps = pd.to_datetime(frame["timestamp"])
            if timestamps.dt.tz is None:
                timestamps = timestamps.dt.tz_localize(sensor_timezone)
            frame["timestamp"] = timestamps.dt.tz_convert("UTC")
        irrigation_columns.add(irrigation_column)
        frames.append(frame)

    if len(irrigation_columns) != 1:
        raise ValueError("Use either irrigation duration or volume in a training run.")
    irrigation_column = irrigation_columns.pop()
    sensors = pd.concat(frames, ignore_index=True)
    frames = []
    for _, sector in sensors.groupby("sector"):
        sector = sector.set_index("timestamp").sort_index().asfreq("10min")
        moisture = sector["soil_moisture_pct"]
        irrigation = sector[irrigation_column]
        if irrigation_column == "irrigation_duration_minutes":
            # Mendeley timestamps mark the start of each valve-log interval.
            # At timestamp t, only the preceding intervals have finished.
            irrigation = irrigation.shift(1)
        sector["soil_change_10m"] = moisture.diff()
        sector["irrigation_10m"] = irrigation
        sector["target"] = moisture.shift(-1)
        frames.append(sector.reset_index())
    return pd.concat(frames, ignore_index=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sensors", nargs="+", required=True)
    parser.add_argument(
        "--sensor-timezone", default="UTC",
        help="Timezone of sensor timestamps without an offset (default: UTC).",
    )
    parser.add_argument("--output", default="irrigation_rf_10m_1step.joblib")
    args = parser.parse_args()

    data = prepare_sensors(args.sensors, args.sensor_timezone)
    rows_before = len(data)
    data = data.dropna(subset=[*FEATURES, "target"]).sort_values("timestamp")
    print(f"Complete rows: {len(data):,}/{rows_before:,}")

    times = data["timestamp"].drop_duplicates().reset_index(drop=True)
    if len(times) < 30:
        parser.error("Need at least 30 complete sensor timestamps.")
    validation_start = times.iloc[int(len(times) * 0.70)]
    test_start = times.iloc[int(len(times) * 0.85)]
    train = data[data["timestamp"] + HORIZON < validation_start]
    validation = data[
        (data["timestamp"] >= validation_start)
        & (data["timestamp"] + HORIZON < test_start)
    ]
    test = data[data["timestamp"] >= test_start]
    if any(part.empty for part in (train, validation, test)):
        parser.error("Not enough complete rows after splitting by time.")

    model = RandomForestRegressor(
        n_estimators=200, max_depth=10, min_samples_leaf=10,
        random_state=42, n_jobs=-1,
    )
    model.fit(train[FEATURES], train["target"])
    for name, part in (("Validation", validation), ("Test", test)):
        mae = mean_absolute_error(part["target"], model.predict(part[FEATURES]))
        baseline = mean_absolute_error(part["target"], part["soil_moisture_pct"])
        print(f"{name} ({len(part):,} rows): MAE {mae:.3f} pp, baseline {baseline:.3f} pp")

    joblib.dump(model, args.output)
    print(f"Saved model: {args.output}")


if __name__ == "__main__":
    main()
