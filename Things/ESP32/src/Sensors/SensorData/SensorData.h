
#pragma once

struct SensorReading {
    float value = 0.0f;
    bool isValid = false;
};

struct SoilSensorData {
    int rawValue = 0;
    float moisturePercent = 0.0f;
    bool isValid = false;
};

struct DHT22Data {
    float temperature = 0.0f;
    float humidity = 0.0f;
    bool isValid = false;
};

struct SensorData {
    SoilSensorData soil;
    DHT22Data dht;
    SensorReading light;
};
