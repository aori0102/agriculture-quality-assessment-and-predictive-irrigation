import pandas as pd
import matplotlib.pyplot as plt

raw_datasets = [
    "dataset_zone_1.csv",
    "dataset_zone_2.csv",
    "dataset_zone_3.csv",
    "dataset_zone_4.csv",
    "dataset_zone_5.csv"
]

dataset = "dataset_zone_1.csv"

def preprocess_data(data):
    data['ts'] = pd.to_datetime(data.ts, format="%Y-%m-%d %H:%M:%S")
    data = data.sort_values("ts")
    drop_columns = [
        'outside_temperature', 
        'inside_temperature',
        'outside_humidity',
        'inside_humidity',
        'weather_rain',
        'weather_pressure',
        'weather_wind_speed',
        'weather_radiation',
        'ec',
        'ph',
        'soil_temperature_0-7cm',
        'soil_temperature_7-18cm',
        'liters_total']

    data = data.drop(drop_columns, axis=1)
    
    return data


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
    previous_data['sector'] = 0
    
    for i in range(1, len(raw_datasets)):
        current_data = pd.read_csv(raw_datasets[i])
        current_data['sector'] = i
        combined_data = pd.concat([previous_data, current_data], axis=0, ignore_index=True)
        previous_data = combined_data

    moisture = combined_data['soil_moisture']
    combined_data['soil_moisture'] = moisture.where(moisture.between(0, 100))
    combined_data = preprocess_data(combined_data)
    
    combined_data = combined_data.rename(
        columns={"irrigation_duration_minutes": "irrigation_next_10m"}
    )
    
    horizon = pd.Timedelta(minutes=10)

    combined_data = combined_data.sort_values(["sector", "ts"]).copy()
    by_sector = combined_data.groupby("sector")

    previous_ts = by_sector["ts"].shift(1)
    previous_duration = by_sector["irrigation_next_10m"].shift(1)

    combined_data["irrigation_past_10m"] = previous_duration.where(
        previous_ts.eq(combined_data["ts"] - horizon)
    )
    
    next_ts = by_sector["ts"].shift(-1)
    next_moisture = by_sector["soil_moisture"].shift(-1)

    combined_data["y"] = next_moisture.where(
        next_ts.eq(combined_data["ts"] + horizon)
    )
    
    previous_moisture = by_sector["soil_moisture"].shift(1)
    combined_data["soil_delta_10m"] = (
        combined_data["soil_moisture"] - previous_moisture
    ).where(previous_ts.eq(combined_data["ts"] - horizon))

    labeled = combined_data.dropna(subset=["soil_moisture", "y"])
    labeled = labeled.sort_values(["ts", "sector"])

    cutoff_ts = pd.Timestamp("2025-08-15")

    train_data = labeled.loc[labeled["ts"] + horizon < cutoff_ts].copy()
    test_data = labeled.loc[labeled["ts"] >= cutoff_ts].copy()

    train_data.to_csv("train_data.csv", index=False)
    test_data.to_csv("test_data.csv", index=False)
    
    print(f"Train data shape: {train_data.shape}")
    print(f"Test data shape: {test_data.shape}")


if __name__ == "__main__":
    main()