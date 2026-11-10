
#include "DHT22Sensor.h"
#include <math.h>

DHT11Sensor::DHT11Sensor(int pin) {
    m_pin = pin;
}

void DHT11Sensor::begin() {
    m_dht.setup(m_pin, DHTesp::DHT11);
}

DHT22Data DHT11Sensor::read() {
    DHT22Data result;

    TempAndHumidity data = m_dht.getTempAndHumidity();

    if (m_dht.getStatus() != DHTesp::ERROR_NONE) {
        Serial.printf("DHT error: %s\n", m_dht.getStatusString());
        return result;
    }

    if (!isfinite(data.temperature) || !isfinite(data.humidity)) {
        Serial.printf("Loi read DHT: Temp=%.2f, Hum=%.2f\n", data.temperature, data.humidity);
        return result;
    }

    if (data.humidity < 0.0f || data.humidity > 100.0f) {
        Serial.printf("Loi Humid: Temp=%.2f, Hum=%.2f\n", data.temperature, data.humidity);
        return result;
    }

    result.temperature = data.temperature;
    result.humidity = data.humidity;
    result.isValid = true;

    return result;
}
