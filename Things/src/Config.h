#pragma once

namespace Config {
    // WiFi
    constexpr char WIFI_SSID[] = "UET-Wifi-T4 2.4Ghz";
    constexpr char WIFI_PASSWORD[] = "";

    // Device configuration
    constexpr char SITE_ID[] = "site01";
    constexpr char ZONE_ID[] = "zone01";

    // MQTT broker
    constexpr char MQTT_HOST[] = "broker.hivemq.com";
    constexpr uint16_t MQTT_PORT = 1883;

    constexpr char MQTT_USERNAME[] = "";
    constexpr char MQTT_PASSWORD[] = "";

    // Sampling interval
    constexpr uint32_t TELEMETRY_INTERVAL_MS = 5000;
}