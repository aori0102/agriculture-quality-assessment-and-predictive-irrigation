
#include <Arduino.h>
#include <WiFi.h>
#include <time.h>

#include "Config.h"

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

// Device identity
DeviceIdentity deviceIdentity;

// Telemetry
TelemetryBuilder telemetryBuilder(
    Config::ZONE_ID
);

// MQTT
MQTTClient mqttClient;

uint32_t lastTelemetryTime = 0;

// NTP configuration
const char* NTP_SERVER = "pool.ntp.org";
const long GMT_OFFSET_SECONDS = 7 * 3600;
const int DAYLIGHT_OFFSET_SECONDS = 0;

void setup() {
    Serial.begin(115200);
    delay(1000);

    // 1. Initialize WiFi
    WiFi.mode(WIFI_AP_STA);

    // 2. Initialize device identity
    deviceIdentity.begin();

    Serial.println("\n========== DEVICE IDENTITY ==========");
    Serial.print("[DEVICE] Node ID: ");
    Serial.println(deviceIdentity.getNodeId());

    // 3. Initialize sensors
    Serial.println("\n========== SENSOR INITIALIZATION ==========");
    sensorManager.begin();

    // 4. Connect WiFi (temporary test)
    WiFi.begin(Config::WIFI_SSID, Config::WIFI_PASSWORD);

    Serial.print("[WIFI] Connecting");

    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }

    Serial.println("\n[WIFI] Connected!");
    Serial.print("[WIFI] IP: ");
    Serial.println(WiFi.localIP());

    // 5. Synchronize time using NTP
    Serial.println("\n========== NTP SYNCHRONIZATION ==========");

    configTime(
        GMT_OFFSET_SECONDS,
        DAYLIGHT_OFFSET_SECONDS,
        NTP_SERVER
    );

    struct tm timeInfo;

    if (getLocalTime(&timeInfo, 10000)) {
        Serial.println("[NTP] Time synchronized");
    } else {
        Serial.println("[NTP] Synchronization failed");
    }

    // 6. Initialize MQTT
    Serial.println("\n========== MQTT INITIALIZATION ==========");

    mqttClient.begin(
        Config::SITE_ID,
        deviceIdentity.getNodeId(),
        Config::MQTT_HOST,
        Config::MQTT_PORT,
        Config::MQTT_USERNAME,
        Config::MQTT_PASSWORD
    );
}

void loop() {
    // 1. Maintain MQTT connection
    mqttClient.loop();

    // 2. Wait for MQTT connection
    if (!mqttClient.isConnected()) {
        return;
    }

    uint32_t currentTime = millis();

    // 3. Read and publish telemetry periodically
    if (currentTime - lastTelemetryTime >=
        Config::TELEMETRY_INTERVAL_MS) {

        lastTelemetryTime = currentTime;

        // Read sensors
        SensorData sensorData = sensorManager.readAll();

        // Get current UTC timestamp
        String sampledAt = "";

        struct tm timeInfo;

        if (getLocalTime(&timeInfo, 100)) {
            char timestamp[25];

            strftime(
                timestamp,
                sizeof(timestamp),
                "%Y-%m-%dT%H:%M:%SZ",
                &timeInfo
            );

            sampledAt = timestamp;
        }

        // Build telemetry
        TelemetryData telemetry = telemetryBuilder.build(
            sensorData,
            deviceIdentity,
            sampledAt
        );

        // Convert telemetry to JSON
        String payload = telemetryBuilder.toJson(telemetry);

        // Publish telemetry
        Serial.println("\n========== TELEMETRY ==========");
        Serial.print("[MQTT] Topic: ");
        Serial.println("iot/" + String(Config::SITE_ID) + "/" +
                       deviceIdentity.getNodeId() + "/telemetry");

        Serial.println("[MQTT] Payload:");
        Serial.println(payload);

        mqttClient.publishTelemetry(payload);
    }
}
