
#pragma once

#include <Arduino.h>
#include "Sensors\SensorData\SensorData.h"

class SoilMoistureSensor {
private:
    int m_pin;

    int m_dryValue;
    int m_wetValue;

    int readRawValue();

public:
    SoilMoistureSensor(
        int pin,
        int dryValue,
        int wetValue
    );

    void begin();

    SoilSensorData read();
};
