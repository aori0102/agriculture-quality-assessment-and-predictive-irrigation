from pathlib import Path
import joblib
import pandas as pd
import json
from .telemetry_handler import telemetry_handler
from . import field_constants as fc
from .time_helper import TimeHelper
from configparser import ConfigParser

class IrrigationHandler:
    def __init__(self):
        cfg_path = Path(__file__).with_name("config.cfg")
        cfg = ConfigParser()

        with cfg_path.open(encoding="utf-8-sig") as file:
            cfg.read_file(file)

        self.default_irrigation_minutes = cfg.getint("irrigation", "default_irrigation_minutes")
        self.reduced_irrigation_minutes = cfg.getint("irrigation", "reduced_irrigation_minutes")
        self.sample_delay_seconds = cfg.getint("sampling", "sample_delay_seconds")
        self.sample_timeout_minutes = cfg.getint("sampling", "sample_timeout_minutes")
                
        model_path = Path(__file__).with_name(cfg.get("model", "model_path"))
        self.model = joblib.load(model_path)
        
        plant_path = Path(__file__).with_name(cfg.get("plant", "plant_profile_path"))
        with plant_path.open("r", encoding="utf-8") as file:
            self.plant_profile = json.load(file)


    def predict_soil_moisture(
        self,
        soil_moisture_pct: float,
        soil_change_10m: float,
        air_temp_c: float,
        air_rh_pct: float,
        irrigation_10m: float
        ) -> float:
        sample = pd.DataFrame([{
            fc.SOIL_MOISTURE_PERCENTAGE: soil_moisture_pct,
            fc.SOIL_CHANGE_10M: soil_change_10m,
            fc.AIR_TEMP_C: air_temp_c,
            fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE: air_rh_pct,
            fc.IRRIGATION_10M: irrigation_10m,
        }])

        return self.model.predict(sample[self.model.feature_names_in_])[0]


    def handle_irrigation(self) -> None:
        print("Checking irrigation needs...")
        data = telemetry_handler.get_irrigation_data()
        if data is None:
            print("Not enough data to make a prediction. Waiting for more samples...")
            telemetry_handler.record_irrigation_data(0)
            return

        current_soil_moisture = data[fc.SOIL_MOISTURE_PERCENTAGE]
        predicted_soil_moisture = self.predict_soil_moisture(
            data[fc.SOIL_MOISTURE_PERCENTAGE],
            data[fc.SOIL_CHANGE_10M],
            data[fc.AIR_TEMP_C],
            data[fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE],
            data[fc.IRRIGATION_10M]
            )
        
        if current_soil_moisture >= self.plant_profile[fc.SATURATION_POINT]: # Saturation
            print(f"Current soil moisture {current_soil_moisture:.2f}% is above saturation threshold. No irrigation needed.")
            telemetry_handler.record_irrigation_data(0)
            return
        
        if current_soil_moisture >= self.plant_profile[fc.FIELD_CAPACITY]: # Excess
            print(f"Current soil moisture {current_soil_moisture:.2f}% is above field capacity threshold. No irrigation needed.")
            telemetry_handler.record_irrigation_data(0)
            return
        
        pwp = self.plant_profile[fc.PERMANENT_WILTING_POINT]
        if current_soil_moisture <= pwp or predicted_soil_moisture <= pwp: # Danger
            print(f"Predicted soil moisture {predicted_soil_moisture:.2f}% or current soil moisture {current_soil_moisture:.2f}% is below permanent wilting point. Irrigation needed.")
            telemetry_handler.record_irrigation_data(self.default_irrigation_minutes)
            return
        
        madp = self.plant_profile[fc.MANAGEMENT_ALLOWED_DEPLETION_POINT]
        if current_soil_moisture < madp: # Stress
            print(f"Current soil moisture {current_soil_moisture:.2f}% is below management allowed depletion point. Irrigation needed.")
            telemetry_handler.record_irrigation_data(self.default_irrigation_minutes)
            return
        
        if predicted_soil_moisture < madp: # Stress
            print(f"Predicted soil moisture {predicted_soil_moisture:.2f}% is below management allowed depletion point. Irrigation needed.")
            telemetry_handler.record_irrigation_data(self.reduced_irrigation_minutes)
            return
        
        print(f"Predicted soil moisture {predicted_soil_moisture:.2f}% is optimal. No irrigation needed.")
        telemetry_handler.record_irrigation_data(0)
        
irrigation_handler = IrrigationHandler()