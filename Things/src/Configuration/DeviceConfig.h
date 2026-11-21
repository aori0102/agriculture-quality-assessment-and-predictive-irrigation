
#pragma once

#include <Arduino.h>

struct DeviceConfig {
    String wifiSSID;
    String wifiPassword;

    String edgeId;

    String mqttHost;
    uint16_t mqttPort;
    String mqttUsername;
    String mqttPassword;

    uint32_t telemetryIntervalMs;

    // Pump PWM configuration: raw duty 0 - 255.
    uint8_t pumpPwmDuty = 200;
};

class DeviceConfigManager {
public:
    bool begin();
    bool load();
    bool save(const DeviceConfig& config);

    const DeviceConfig& get() const;

private:
    DeviceConfig m_config;
};
