#pragma once

#include <Arduino.h>
#include <WiFi.h>

class StatusLed {
public:
    void begin();
    void update(bool mqttConnected, bool configOpen = false);

private:
    static constexpr uint8_t LED_PIN = 23;

    static constexpr uint32_t BLINK_ON_MS = 150;
    static constexpr uint32_t BLINK_OFF_MS = 150;
    static constexpr uint32_t PAUSE_MS = 1000;

    enum class ConnectionState {
        CONFIG_OPEN,
        WIFI_DISCONNECTED,
        MQTT_DISCONNECTED,
        CONNECTED
    };

    ConnectionState m_currentState =
        ConnectionState::WIFI_DISCONNECTED;

    uint32_t m_patternStartTime = 0;

    void updatePattern(uint8_t blinkCount);
};