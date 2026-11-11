#pragma once

#include <Arduino.h>

#include "TelemetryData.h"

#include "../Sensors/SensorManager.h"
#include "../Identity/DeviceIdentity.h"

class TelemetryBuilder {
private:
    String m_zoneId;

public:
    explicit TelemetryBuilder(const String& zoneId);

    TelemetryData build(
        const SensorData& sensorData,
        const DeviceIdentity& identity,
        const String& sampledAt = ""
    );

    String toJson(const TelemetryData& data);
};