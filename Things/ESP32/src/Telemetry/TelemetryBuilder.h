#pragma once

#include <Arduino.h>

#include "TelemetryData.h"

#include "../Sensors/SensorManager.h"
#include "../Identity/DeviceIdentity.h"

class TelemetryBuilder {
private:
    uint32_t m_sequence = 0;

    String m_siteId;
    String m_zoneId;

public:
    TelemetryBuilder(
        const String& siteId,
        const String& zoneId
    );

    TelemetryData build(
        const SensorData& sensorData,
        const DeviceIdentity& identity,
        bool clockSynced = false,
        const String& sampledAt = ""
    );

    String toJson(const TelemetryData& data);
};