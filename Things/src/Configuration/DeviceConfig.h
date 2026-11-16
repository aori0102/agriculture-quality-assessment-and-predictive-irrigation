
#pragma once

#include <Arduino.h>

struct DeviceConfig {
    String wifiSSID;
    String wifiPassword;

    String siteId;
    String zoneId;
    String edgeId;

    String mqttHost;
    uint16_t mqttPort;
    String mqttUsername;
    String mqttPassword;

    uint32_t telemetryIntervalMs;
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
