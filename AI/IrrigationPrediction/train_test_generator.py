import argparse
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
import field_constants as fc

raw_datasets = [
    "datasets/dataset_zone_1_processed.csv",
    "datasets/dataset_zone_2_processed.csv",
    "datasets/dataset_zone_3_processed.csv",
    "datasets/dataset_zone_4_processed.csv",
    "datasets/dataset_zone_5_processed.csv"
]


def show_moisture_graph(data):
    plt.figure(figsize=(12, 6))
    plt.plot(data['soil_moisture'], label='Soil Moisture')
    plt.xlabel('Index')
    plt.ylabel('Moisture Level')
    plt.title('Soil Moisture Levels Over Time')
    plt.legend()
    plt.show()


def generate_original_datasets():
    previous_data = pd.read_csv(raw_datasets[0])
    previous_data[fc.SECTOR] = 0
    
    for i in range(1, len(raw_datasets)):
        current_data = pd.read_csv(raw_datasets[i])
        current_data[fc.SECTOR] = i
        combined_data = pd.concat([previous_data, current_data], axis=0, ignore_index=True)
        previous_data = combined_data

    moisture = combined_data[fc.SOIL_MOISTURE]
    combined_data[fc.SOIL_MOISTURE] = moisture.where(moisture.between(0, 100))
    combined_data[fc.TIMESTAMP] = pd.to_datetime(combined_data[fc.TIMESTAMP], format='%Y-%m-%d %H:%M:%S')
    
    horizon = pd.Timedelta(minutes=10)

    combined_data = combined_data.sort_values([fc.SECTOR, fc.TIMESTAMP]).copy()
    by_sector = combined_data.groupby(fc.SECTOR)

    previous_ts = by_sector[fc.TIMESTAMP].shift(1)
    previous_duration = by_sector[fc.IRRIGATION_DURATION_MINUTES].shift(1)

    combined_data[fc.IRRIGATION_PAST_10M] = previous_duration.where(
        previous_ts.eq(combined_data[fc.TIMESTAMP] - horizon)
    )
    
    next_ts = by_sector[fc.TIMESTAMP].shift(-1)
    next_moisture = by_sector[fc.SOIL_MOISTURE].shift(-1)

    combined_data["y"] = next_moisture.where(
        next_ts.eq(combined_data[fc.TIMESTAMP] + horizon)
    )
    
    previous_moisture = by_sector[fc.SOIL_MOISTURE].shift(1)
    combined_data[fc.SOIL_CHANGE_10M] = (
        combined_data[fc.SOIL_MOISTURE] - previous_moisture
    ).where(previous_ts.eq(combined_data[fc.TIMESTAMP] - horizon))


    labeled = combined_data.dropna(subset=[fc.SOIL_MOISTURE, "y"])
    labeled = labeled.sort_values([fc.TIMESTAMP, fc.SECTOR])

    cutoff_ts = pd.Timestamp("2025-08-15")

    train_data = labeled.loc[labeled[fc.TIMESTAMP] + horizon < cutoff_ts].copy()
    test_data = labeled.loc[labeled[fc.TIMESTAMP] >= cutoff_ts].copy()

    train_data.to_csv("datasets/train_data.csv", index=False)
    test_data.to_csv("datasets/test_data.csv", index=False)
    
    print(f"Train data shape: {train_data.shape}")
    print(f"Test data shape: {test_data.shape}")


def generate_retrain_datasets(edge_log: Path, base_train: Path, output_dir: Path,
                              closed_before: str) -> None:
    """Use consecutive closed windows and reserve the newest Edge samples for test."""
    # Read the atomically replaced log into one snapshot for this training run.
    from io import BytesIO

    data = pd.read_csv(BytesIO(edge_log.read_bytes()), dtype={fc.NODE_ID: str})
    timestamps = pd.to_datetime(data[fc.TIMESTAMP], format="mixed", utc=True, errors="coerce")
    cutoff = pd.to_datetime(closed_before, utc=True)
    data = data.loc[timestamps < cutoff].copy()
    data[fc.TIMESTAMP] = timestamps.loc[data.index]
    data = data.dropna(subset=[fc.NODE_ID]).sort_values(fc.TIMESTAMP, kind="stable")
    data = data.drop_duplicates([fc.NODE_ID, fc.TIMESTAMP], keep="last")
    measurements = [fc.SOIL_MOISTURE, fc.WEATHER_TEMPERATURE,
                    fc.WEATHER_HUMIDITY, fc.IRRIGATION_DURATION_MINUTES]
    for column in measurements:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data[measurements] = data[measurements].replace([float("inf"), float("-inf")], float("nan"))
    data[fc.SOIL_MOISTURE] = data[fc.SOIL_MOISTURE].where(data[fc.SOIL_MOISTURE].between(0, 100))
    duration = data[fc.IRRIGATION_DURATION_MINUTES]
    data[fc.IRRIGATION_DURATION_MINUTES] = duration.where(duration >= 0)

    horizon = pd.Timedelta(minutes=10)
    by_node = data.groupby(fc.NODE_ID, sort=False)
    previous_is_adjacent = by_node[fc.TIMESTAMP].shift(1).eq(data[fc.TIMESTAMP] - horizon)
    next_is_adjacent = by_node[fc.TIMESTAMP].shift(-1).eq(data[fc.TIMESTAMP] + horizon)
    data[fc.IRRIGATION_PAST_10M] = by_node[fc.IRRIGATION_DURATION_MINUTES].shift(1).where(previous_is_adjacent)
    data[fc.SOIL_CHANGE_10M] = (data[fc.SOIL_MOISTURE] - by_node[fc.SOIL_MOISTURE].shift(1)).where(previous_is_adjacent)
    data["y"] = by_node[fc.SOIL_MOISTURE].shift(-1).where(next_is_adjacent)
    required = [*measurements, fc.IRRIGATION_PAST_10M, fc.SOIL_CHANGE_10M, "y"]
    labeled = data.dropna(subset=required)
    slots = labeled[fc.TIMESTAMP].drop_duplicates().sort_values()
    if len(slots) < 2:
        raise ValueError("Not enough consecutive closed Edge windows for train/test.")
    test_start = slots.iloc[int(len(slots) * 0.8)]
    edge_train = labeled.loc[labeled[fc.TIMESTAMP] + horizon < test_start]
    edge_test = labeled.loc[labeled[fc.TIMESTAMP] >= test_start]
    if edge_train.empty:
        raise ValueError("Not enough Edge windows before the test split.")
    base = pd.read_csv(base_train)
    train = pd.concat([base, edge_train], ignore_index=True)
    train.to_csv(output_dir / "train_data.csv", index=False)
    edge_test.to_csv(output_dir / "test_data.csv", index=False)
    print(f"Prepared {len(edge_train)} Edge train rows and {len(edge_test)} Edge test rows.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edge-log", type=Path)
    parser.add_argument("--base-train", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--closed-before")
    args = parser.parse_args()
    if args.edge_log is None:
        generate_original_datasets()
        return
    if args.base_train is None or args.output_dir is None or args.closed_before is None:
        parser.error("--edge-log requires --base-train, --output-dir and --closed-before")
    generate_retrain_datasets(args.edge_log, args.base_train, args.output_dir, args.closed_before)


if __name__ == "__main__":
    main()