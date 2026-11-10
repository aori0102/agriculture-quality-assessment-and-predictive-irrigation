
#include <Arduino.h>

#include "Sensors/SensorManager.h"

#define SOIL_PIN 26
#define DHT_PIN 27

#define SOIL_DRY_VALUE 3200
#define SOIL_WET_VALUE 1400

SensorManager sensorManager(
    SOIL_PIN,
    DHT_PIN,
    SOIL_DRY_VALUE,
    SOIL_WET_VALUE
);

unsigned long lastReadTime = 0;

const unsigned long READ_INTERVAL = 5000;

void setup() {
    Serial.begin(115200);

    sensorManager.begin();
}

void loop() {
    unsigned long currentTime = millis();

    if (currentTime - lastReadTime >= READ_INTERVAL) {
        lastReadTime = currentTime;

        SensorData data = sensorManager.readAll();

        Serial.println("\n========== SENSOR DATA ==========");

        // Soil Sensor
        Serial.println("[SOIL SENSOR]");

        if (data.soil.isValid) {
            Serial.printf("Raw: %d\n", data.soil.rawValue);

            Serial.printf(
                "Moisture: %.2f%%\n",
                data.soil.moisturePercent
            );
        }
        else {
            Serial.println("Invalid reading!");
        }

        // DHT22
        Serial.println("\n[DHT22]");

        if (data.dht.isValid) {
            Serial.printf(
                "Temperature: %.2f C\n",
                data.dht.temperature
            );

            Serial.printf(
                "Humidity: %.2f %%RH\n",
                data.dht.humidity
            );
        }
        else {
            Serial.println("Invalid reading!");
        }

        Serial.println("=================================");
    }
}
