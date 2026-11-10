#include "DeviceIdentity.h"

#include <Arduino.h>
#include <esp_random.h>
#include <stdio.h>

void DeviceIdentity::begin() {
    if (m_initialized) {
        return;
    }

    generateNodeId();
    generateBootId();

    m_initialized = true;
}

void DeviceIdentity::generateNodeId() {
    uint64_t macAddress = ESP.getEfuseMac();

    snprintf(
        m_nodeId,
        sizeof(m_nodeId),
        "%04x%08x",
        static_cast<uint16_t>(macAddress >> 32),
        static_cast<uint32_t>(macAddress)
    );
}

void DeviceIdentity::generateBootId() {
    uint8_t uuid[16];

    // Generate 16 random bytes.
    for (int i = 0; i < 16; i += 4) {
        uint32_t randomValue = esp_random();

        uuid[i]     = static_cast<uint8_t>(randomValue);
        uuid[i + 1] = static_cast<uint8_t>(randomValue >> 8);
        uuid[i + 2] = static_cast<uint8_t>(randomValue >> 16);
        uuid[i + 3] = static_cast<uint8_t>(randomValue >> 24);
    }

    // Set UUID version to 4.
    uuid[6] = (uuid[6] & 0x0F) | 0x40;

    // Set UUID variant to RFC 4122.
    uuid[8] = (uuid[8] & 0x3F) | 0x80;

    snprintf(
        m_bootId,
        sizeof(m_bootId),
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        uuid[0], uuid[1], uuid[2], uuid[3],
        uuid[4], uuid[5],
        uuid[6], uuid[7],
        uuid[8], uuid[9],
        uuid[10], uuid[11], uuid[12],
        uuid[13], uuid[14], uuid[15]
    );
}

const char* DeviceIdentity::getNodeId() const {
    return m_nodeId;
}

const char* DeviceIdentity::getBootId() const {
    return m_bootId;
}