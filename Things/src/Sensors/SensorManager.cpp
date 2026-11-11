
#include "SensorManager.h"

SensorManager::SensorManager(
    int soilPin,
    int dhtPin,
    int dryValue,
    int wetValue
)
    : m_soilSensor(soilPin, dryValue, wetValue),
      m_dhtSensor(dhtPin) {
}

void SensorManager::begin() {
    Wire.begin(21, 22);

    m_soilSensor.begin();
    m_dhtSensor.begin();

    Serial.println("Sensor initialization completed.");
}

SensorData SensorManager::readAll() {
    SensorData data;

    data.soil = m_soilSensor.read();
    data.dht = m_dhtSensor.read();

    return data;
}
