#include "TelemetryBuilder.h"

#include <ArduinoJson.h>
#include <math.h>

TelemetryBuilder::TelemetryBuilder(
    const String& siteId,
    const String& zoneId
) {
    m_siteId = siteId;
    m_zoneId = zoneId;
}

TelemetryData TelemetryBuilder::build(
    const SensorData& sensorData,
    const DeviceIdentity& identity,
    bool clockSynced,
    const String& sampledAt
) {
    TelemetryData data;

    data.siteId = m_siteId;
    data.zoneId = m_zoneId;

    // Identity
    data.deviceId = identity.getNodeId();
    data.bootId = identity.getBootId();

    // Sequence
    data.seq = m_sequence++;

    // Time
    data.sampledAt = sampledAt;
    data.clockSynced = clockSynced;
    data.uptimeMs = millis();

    // Soil moisture
    data.soilValid =
        sensorData.soil.isValid &&
        isfinite(sensorData.soil.moisturePercent);

    if (data.soilValid) {
        data.soilRaw = sensorData.soil.rawValue;
        data.soilIndexPct = sensorData.soil.moisturePercent;
    }

    // DHT11
    data.dhtValid =
        sensorData.dht.isValid &&
        isfinite(sensorData.dht.temperature) &&
        isfinite(sensorData.dht.humidity) &&
        sensorData.dht.humidity >= 0.0f &&
        sensorData.dht.humidity <= 100.0f;

    if (data.dhtValid) {
        data.temperatureC = sensorData.dht.temperature;
        data.airHumidityPct = sensorData.dht.humidity;
    }

    return data;
}

String TelemetryBuilder::toJson(const TelemetryData& data) {
    JsonDocument doc;

    doc["schema_version"] = data.schemaVersion;

    doc["site_id"] = data.siteId;
    doc["zone_id"] = data.zoneId;
    doc["device_id"] = data.deviceId;
    doc["boot_id"] = data.bootId;

    doc["seq"] = data.seq;

    if (data.clockSynced && !data.sampledAt.isEmpty()) {
        doc["sampled_at"] = data.sampledAt;
    } else {
        doc["sampled_at"] = nullptr;
    }

    doc["clock_synced"] = data.clockSynced;
    doc["uptime_ms"] = data.uptimeMs;

    // Soil
    if (data.soilValid) {
        doc["soil_raw"] = data.soilRaw;
        doc["soil_index_pct"] = data.soilIndexPct;
    } else {
        doc["soil_raw"] = nullptr;
        doc["soil_index_pct"] = nullptr;
    }

    // DHT22
    if (data.dhtValid) {
        doc["temperature_c"] = data.temperatureC;
        doc["air_humidity_pct"] = data.airHumidityPct;
    } else {
        doc["temperature_c"] = nullptr;
        doc["air_humidity_pct"] = nullptr;
    }

    // Sensor quality
    JsonObject quality = doc["quality"].to<JsonObject>();

    quality["soil"] = data.soilValid ? "valid" : "invalid";
    quality["dht"] = data.dhtValid ? "valid" : "invalid";

    doc["calibration_version"] = data.calibrationVersion;

    String payload;
    serializeJson(doc, payload);

    return payload;
}