
#pragma once

#include <Arduino.h>

struct TelemetryData {
    String zoneId;
    String nodeId;
    String sampledAt;

    bool soilValid = false;
    float soilIndexPct = 0.0f;

    bool dhtValid = false;
    float temperatureC = 0.0f;
    float airHumidityPct = 0.0f;
};
