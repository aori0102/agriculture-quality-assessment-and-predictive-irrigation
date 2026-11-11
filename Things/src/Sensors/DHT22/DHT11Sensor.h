
#pragma once

#include <Arduino.h>
#include <DHT.h>

#include "Sensors/SensorData/SensorData.h"

class DHT11Sensor {
private:
    static constexpr uint8_t DHT_TYPE = DHT11;

    int m_pin;
    DHT m_dht;

public:
    explicit DHT11Sensor(int pin);

    void begin();

    DHT11Data read();
};
