
#pragma once

#include <Arduino.h>
#include <DHTesp.h>

#include "Sensors\SensorData\SensorData.h"

class DHT11Sensor {
private:
    int m_pin;
    DHTesp m_dht;

public:
    explicit DHT11Sensor(int pin);

    void begin();

    DHT22Data read();
};
