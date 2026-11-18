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


def main():
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


if __name__ == "__main__":
    main()