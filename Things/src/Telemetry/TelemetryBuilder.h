
#pragma once

#include <Arduino.h>

#include "TelemetryData.h"

#include "../Sensors/SensorManager.h"
#include "../Identity/DeviceIdentity.h"

class TelemetryBuilder {
public:
    TelemetryData build(
        const SensorData& sensorData,
        const DeviceIdentity& identity,
        const String& sampledAt = ""
    );

    String toJson(const TelemetryData& data);
};
