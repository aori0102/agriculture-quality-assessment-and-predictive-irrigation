
#include "TelemetryBuilder.h"

#include <ArduinoJson.h>
#include <math.h>

TelemetryBuilder::TelemetryBuilder(const String& zoneId) {
    m_zoneId = zoneId;
}

TelemetryData TelemetryBuilder::build(
    const SensorData& sensorData,
    const DeviceIdentity& identity,
    const String& sampledAt
) {
    TelemetryData data;

    // Device information
    data.zoneId = m_zoneId;
    data.nodeId = identity.getNodeId();
    data.sampledAt = sampledAt;

    // Soil moisture
    data.soilValid =
        sensorData.soil.isValid &&
        isfinite(sensorData.soil.moisturePercent) &&
        sensorData.soil.moisturePercent >= 0.0f &&
        sensorData.soil.moisturePercent <= 100.0f;

    if (data.soilValid) {
        data.soilIndexPct = sensorData.soil.moisturePercent;
    }

    // DHT22
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

    // Device information
    doc["zone_id"] = data.zoneId;
    doc["node_id"] = data.nodeId;

    // Timestamp
    if (!data.sampledAt.isEmpty()) {
        doc["sampled_at"] = data.sampledAt;
    } else {
        doc["sampled_at"] = nullptr;
    }

    // Soil moisture
    if (data.soilValid) {
        doc["soil_index_pct"] = data.soilIndexPct;
    } else {
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

    String payload;
    serializeJson(doc, payload);

    return payload;
}
