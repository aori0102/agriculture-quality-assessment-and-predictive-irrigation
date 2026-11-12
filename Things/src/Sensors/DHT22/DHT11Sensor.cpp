
#include "Sensors/DHT22/DHT11Sensor.h"
#include <math.h>

DHT11Sensor::DHT11Sensor(int pin)
    : m_pin(pin), m_dht(pin, DHT_TYPE) {
}

void DHT11Sensor::begin() {
    m_dht.begin();
    Serial.println("[DHT11] Initialized");
}

DHT11Data DHT11Sensor::read() {
    DHT11Data result;

    float temperature = m_dht.readTemperature();
    float humidity = m_dht.readHumidity();

    if (isnan(temperature) || isnan(humidity)) {
        Serial.println("[DHT11] Failed to read sensor!");
        return result;
    }

    if (humidity < 0.0f || humidity > 100.0f) {
        Serial.printf(
            "[DHT11] Invalid humidity: %.2f\n",
            humidity
        );
        return result;
    }

    result.temperature = temperature;
    result.humidity = humidity;
    result.isValid = true;

    return result;
}
