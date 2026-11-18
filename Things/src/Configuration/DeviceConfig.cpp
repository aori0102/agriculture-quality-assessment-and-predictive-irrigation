
#include "DeviceConfig.h"

#include <Preferences.h>
#include "Config.h"

namespace {
    constexpr char NVS_NAMESPACE[] = "device_config";
}

bool DeviceConfigManager::begin() {
    return load();
}

bool DeviceConfigManager::load() {
    Preferences preferences;

    if (!preferences.begin(NVS_NAMESPACE, true)) {
        Serial.println("[CONFIG] Cannot open NVS!");
        return false;
    }

    m_config.wifiSSID = preferences.getString(
        "wifi_ssid",
        Config::WIFI_SSID
    );

    m_config.wifiPassword = preferences.getString(
        "wifi_pass",
        Config::WIFI_PASSWORD
    );

    m_config.edgeId = preferences.getString(
        "edge_id",
        Config::EDGE_ID
    );

    m_config.mqttHost = preferences.getString(
        "mqtt_host",
        Config::MQTT_HOST
    );

    m_config.mqttPort = preferences.getUShort(
        "mqtt_port",
        Config::MQTT_PORT
    );

    m_config.mqttUsername = preferences.getString(
        "mqtt_user",
        Config::MQTT_USERNAME
    );

    m_config.mqttPassword = preferences.getString(
        "mqtt_pass",
        Config::MQTT_PASSWORD
    );

    m_config.telemetryIntervalMs = preferences.getUInt(
        "tele_interval",
        Config::TELEMETRY_INTERVAL_MS
    );

    preferences.end();

    Serial.println("[CONFIG] Configuration loaded!");

    return true;
}

bool DeviceConfigManager::save(const DeviceConfig& config) {
    Preferences preferences;

    if (!preferences.begin(NVS_NAMESPACE, false)) {
        Serial.println("[CONFIG] Cannot open NVS for writing!");
        return false;
    }

    preferences.putString("wifi_ssid", config.wifiSSID);
    preferences.putString("wifi_pass", config.wifiPassword);

    preferences.putString("edge_id", config.edgeId);

    preferences.putString("mqtt_host", config.mqttHost);
    preferences.putUShort("mqtt_port", config.mqttPort);
    preferences.putString("mqtt_user", config.mqttUsername);
    preferences.putString("mqtt_pass", config.mqttPassword);

    preferences.putUInt(
        "tele_interval",
        config.telemetryIntervalMs
    );

    preferences.end();

    m_config = config;

    Serial.println("[CONFIG] Configuration saved!");

    return true;
}

const DeviceConfig& DeviceConfigManager::get() const {
    return m_config;
}
