#include <Arduino.h>
#include <WiFi.h>

#include "config/Config.h"

#include "Identity/DeviceIdentity.h"
#include "Sensors/SensorManager.h"

#include "Telemetry/TelemetryBuilder.h"
#include "MQTT/MQTTClient.h"

// Sensor configuration
SensorManager sensorManager(
    27,     // Soil moisture pin
    26,     // DHT22 pin
    3200,   // Dry calibration
    1400    // Wet calibration
);

// Identity
DeviceIdentity deviceIdentity;

// Telemetry
TelemetryBuilder telemetryBuilder(
    Config::SITE_ID,
    Config::ZONE_ID
);

// MQTT
MQTTClient mqttClient;

bool mqttInitialized = false;

uint32_t lastTelemetryTime = 0;

void setup() {
    Serial.begin(115200);

    // 1. Initialize identity
    deviceIdentity.begin();

    Serial.println("========== IDENTITY ==========");
    Serial.println(deviceIdentity.getNodeId());
    Serial.println(deviceIdentity.getBootId());

    // 2. Initialize sensors
    sensorManager.begin();

    // 3. Connect WiFi (temporary test)
    WiFi.mode(WIFI_STA);
    WiFi.begin("vivo x90", "22224444");

    Serial.print("[WIFI] Connecting");

    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }

    Serial.println("\n[WIFI] Connected!");
    Serial.println(WiFi.localIP());

    // 4. Initialize MQTT
    mqttClient.begin(
    "550e8400-e29b-41d4-a716-446655440000",
    deviceIdentity.getNodeId(),
    Config::MQTT_HOST,
    Config::MQTT_PORT,
    Config::MQTT_USERNAME,
    Config::MQTT_PASSWORD
    );  

    mqttInitialized = true;
}

void loop() {
    // Wait for WiFi before initializing MQTT.
    if (WiFi.isConnected() && !mqttInitialized) {
        mqttClient.begin(
            /* edgeId: lấy từ ConfigStorage */
            "YOUR_EDGE_UUID",

            deviceIdentity.getNodeId(),

            Config::MQTT_HOST,
            Config::MQTT_PORT,

            Config::MQTT_USERNAME,
            Config::MQTT_PASSWORD
        );

        mqttInitialized = true;
    }

    mqttClient.loop();

    if (!mqttClient.isConnected()) {
        return;
    }

    uint32_t currentTime = millis();

    if (currentTime - lastTelemetryTime >=
        Config::TELEMETRY_INTERVAL_MS) {

        lastTelemetryTime = currentTime;

        // Read sensors
        SensorData sensorData = sensorManager.readAll();

        // Build telemetry
        TelemetryData telemetry = telemetryBuilder.build(
            sensorData,
            deviceIdentity
        );

        // Convert to JSON
        String payload = telemetryBuilder.toJson(telemetry);

        // Publish
        Serial.println("\n[TELEMETRY]");
        Serial.println(payload);

        mqttClient.publishTelemetry(payload);
    }
}