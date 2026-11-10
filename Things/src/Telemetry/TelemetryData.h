#pragma once

#include <Arduino.h>

struct TelemetryData {
    int schemaVersion = 1;

    String siteId;
    String zoneId;
    String deviceId;
    String bootId;

    uint32_t seq = 0;

    String sampledAt;
    bool clockSynced = false;

    uint32_t uptimeMs = 0;

    bool soilValid = false;
    int soilRaw = 0;
    float soilIndexPct = 0.0f;

    bool dhtValid = false;
    float temperatureC = 0.0f;
    float airHumidityPct = 0.0f;

    int calibrationVersion = 1;
};