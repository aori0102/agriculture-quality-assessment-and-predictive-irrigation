#pragma once

namespace Config {
    // WiFi
    constexpr char WIFI_SSID[] = "vivo X90";
    constexpr char WIFI_PASSWORD[] = "22224444";

    // Device configuration
    constexpr char EDGE_ID[] = "c995b729-888e-4a09-a5c3-77d86efa652c";

    constexpr char SITE_ID[] = "c995b729-888e-4a09-a5c3-77d86efa652c";
    constexpr char ZONE_ID[] = "zone01";

    // MQTT broker

    constexpr char MQTT_HOST[] = "broker.hivemq.com";
    constexpr uint16_t MQTT_PORT = 1883;

    constexpr char MQTT_USERNAME[] = "";
    constexpr char MQTT_PASSWORD[] = "";

    // Sampling interval
    constexpr uint32_t TELEMETRY_INTERVAL_MS = 20000;
}