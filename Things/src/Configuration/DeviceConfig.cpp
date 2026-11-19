
#include "DeviceConfig.h"

#include <Arduino.h>
#include <Preferences.h>

#include "Config.h"

namespace {
    constexpr char NVS_NAMESPACE[] = "device_config";

    constexpr char KEY_WIFI_SSID[] = "wifi_ssid";
    constexpr char KEY_WIFI_PASSWORD[] = "wifi_pass";

    constexpr char KEY_EDGE_ID[] = "edge_id";

    constexpr char KEY_MQTT_HOST[] = "mqtt_host";
    constexpr char KEY_MQTT_PORT[] = "mqtt_port";
    constexpr char KEY_MQTT_USERNAME[] = "mqtt_user";
    constexpr char KEY_MQTT_PASSWORD[] = "mqtt_pass";

    constexpr char KEY_TELEMETRY_INTERVAL[] = "tele_interval";

    constexpr char KEY_PUMP_PWM[] = "pump_pwm";

    constexpr uint8_t DEFAULT_PUMP_PWM_PERCENT = 100;
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

    // ---------------------------------------------
    // 1. WiFi configuration
    // ---------------------------------------------

    m_config.wifiSSID = preferences.getString(
        KEY_WIFI_SSID,
        Config::WIFI_SSID
    );

    m_config.wifiPassword = preferences.getString(
        KEY_WIFI_PASSWORD,
        Config::WIFI_PASSWORD
    );

    // ---------------------------------------------
    // 2. Device configuration
    // ---------------------------------------------

    m_config.edgeId = preferences.getString(
        KEY_EDGE_ID,
        Config::EDGE_ID
    );

    // ---------------------------------------------
    // 3. MQTT configuration
    // ---------------------------------------------

    m_config.mqttHost = preferences.getString(
        KEY_MQTT_HOST,
        Config::MQTT_HOST
    );

    m_config.mqttPort = preferences.getUShort(
        KEY_MQTT_PORT,
        Config::MQTT_PORT
    );

    m_config.mqttUsername = preferences.getString(
        KEY_MQTT_USERNAME,
        Config::MQTT_USERNAME
    );

    m_config.mqttPassword = preferences.getString(
        KEY_MQTT_PASSWORD,
        Config::MQTT_PASSWORD
    );

    // ---------------------------------------------
    // 4. Telemetry configuration
    // ---------------------------------------------

    m_config.telemetryIntervalMs = preferences.getUInt(
        KEY_TELEMETRY_INTERVAL,
        Config::TELEMETRY_INTERVAL_MS
    );

    // ---------------------------------------------
    // 5. Pump configuration
    // ---------------------------------------------

    m_config.pumpPwmPercent = preferences.getUChar(
        KEY_PUMP_PWM,
        DEFAULT_PUMP_PWM_PERCENT
    );

    // Validate PWM configuration.
    if (m_config.pumpPwmPercent > 100) {

        Serial.println(
            "[CONFIG] Invalid pump PWM in NVS. Using 100%."
        );

        m_config.pumpPwmPercent = DEFAULT_PUMP_PWM_PERCENT;
    }

    preferences.end();

    Serial.println("[CONFIG] Configuration loaded!");

    Serial.print("[CONFIG] Pump PWM: ");
    Serial.print(m_config.pumpPwmPercent);
    Serial.println("%");

    return true;
}

bool DeviceConfigManager::save(const DeviceConfig& config) {

    Preferences preferences;

    if (!preferences.begin(NVS_NAMESPACE, false)) {

        Serial.println(
            "[CONFIG] Cannot open NVS for writing!"
        );

        return false;
    }

    // Validate pump PWM before saving.
    if (config.pumpPwmPercent > 100) {

        Serial.println(
            "[CONFIG] Save rejected: invalid pump PWM."
        );

        preferences.end();

        return false;
    }

    // ---------------------------------------------
    // 1. WiFi configuration
    // ---------------------------------------------

    preferences.putString(
        KEY_WIFI_SSID,
        config.wifiSSID
    );

    preferences.putString(
        KEY_WIFI_PASSWORD,
        config.wifiPassword
    );

    // ---------------------------------------------
    // 2. Device configuration
    // ---------------------------------------------

    preferences.putString(
        KEY_EDGE_ID,
        config.edgeId
    );

    // ---------------------------------------------
    // 3. MQTT configuration
    // ---------------------------------------------

    preferences.putString(
        KEY_MQTT_HOST,
        config.mqttHost
    );

    preferences.putUShort(
        KEY_MQTT_PORT,
        config.mqttPort
    );

    preferences.putString(
        KEY_MQTT_USERNAME,
        config.mqttUsername
    );

    preferences.putString(
        KEY_MQTT_PASSWORD,
        config.mqttPassword
    );

    // ---------------------------------------------
    // 4. Telemetry configuration
    // ---------------------------------------------

    preferences.putUInt(
        KEY_TELEMETRY_INTERVAL,
        config.telemetryIntervalMs
    );

    // ---------------------------------------------
    // 5. Pump configuration
    // ---------------------------------------------

    preferences.putUChar(
        KEY_PUMP_PWM,
        config.pumpPwmPercent
    );

    preferences.end();

    // Update runtime configuration.
    m_config = config;

    Serial.println("[CONFIG] Configuration saved!");

    Serial.print("[CONFIG] Pump PWM saved: ");
    Serial.print(m_config.pumpPwmPercent);
    Serial.println("%");

    return true;
}

const DeviceConfig& DeviceConfigManager::get() const {
    return m_config;
}
