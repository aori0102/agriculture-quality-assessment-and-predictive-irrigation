
#pragma once

#include <Arduino.h>
#include <Wire.h>

#include "Sensors\SensorData\SensorData.h"
#include "Moisture\SoilMoistureSensor.h"
#include "DHT22\DHT22Sensor.h"

class SensorManager {
private:
    SoilMoistureSensor m_soilSensor;
    DHT22Sensor m_dhtSensor;

public:
    SensorManager(
        int soilPin,
        int dhtPin,
        int dryValue,
        int wetValue
    );

    void begin();

    SensorData readAll();
};
