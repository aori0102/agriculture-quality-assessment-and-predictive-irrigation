#pragma once

namespace Config {
    // Deployment information
    constexpr char SITE_ID[] = "site01";
    constexpr char ZONE_ID[] = "zone01";

    // MQTT broker
    constexpr char MQTT_HOST[] = "broker.hivemq.com";
    constexpr uint16_t MQTT_PORT = 1883;

    constexpr char MQTT_USERNAME[] = "";
    constexpr char MQTT_PASSWORD[] = "";

    // Telemetry
    constexpr uint32_t TELEMETRY_INTERVAL_MS = 5000;
    constexpr int CALIBRATION_VERSION = 1;
}