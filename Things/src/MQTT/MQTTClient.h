
#pragma once

#include <Arduino.h>
#include <espMqttClient.h>

class MQTTClient {
private:
    espMqttClient m_client;

    String m_host;
    String m_username;
    String m_password;
    String m_clientId;
    String m_telemetryTopic;

    uint16_t m_port = 1883;

    uint32_t m_lastReconnectAttempt = 0;
    static constexpr uint32_t RECONNECT_INTERVAL = 5000;

    bool m_started = false;

    void connect();

public:
    void begin(
        const String& siteId,
        const String& deviceId,
        const String& brokerHost,
        uint16_t brokerPort,
        const String& username,
        const String& password
    );

    void loop();

    bool isConnected() const;

    bool publishTelemetry(const String& payload);
};
