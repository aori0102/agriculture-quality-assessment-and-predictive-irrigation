import pandas as pd
from pathlib import Path
import field_constants as fc

datasets = [
    "dataset_zone_1.csv",
    "dataset_zone_2.csv",
    "dataset_zone_3.csv",
    "dataset_zone_4.csv",
    "dataset_zone_5.csv"
]

datasets_directory = Path(__file__).parent / "datasets"

for dataset in datasets:
    data_path = datasets_directory / dataset
    df = pd.read_csv(data_path)
    df.rename(columns={"ts": fc.TIMESTAMP}, inplace=True)
    df[fc.IRRIGATION_DURATION_MINUTES] = [float(x) for x in df[fc.IRRIGATION_DURATION_MINUTES].fillna(0)]
    df.drop(columns=[
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
        'liters_total'
    ], inplace=True)
    df.to_csv(datasets_directory / dataset.replace(".csv", "_processed.csv"), index=False)