#pragma once

#include <Arduino.h>
#include <espMqttClient.h>
#include <functional>

class MQTTClient {
private:
    espMqttClient m_client;

    String m_host;
    String m_username;
    String m_password;
    String m_clientId;

    String m_telemetryTopic;
    String m_imageTopic;
    String m_commandTopic;

    // Incoming command buffer.
    String m_incomingTopic;
    String m_incomingPayload;
    size_t m_expectedPayloadSize = 0;

    uint16_t m_port = 1883;

    uint32_t m_lastReconnectAttempt = 0;
    static constexpr uint32_t RECONNECT_INTERVAL = 5000;

    static constexpr size_t MAX_COMMAND_SIZE = 1024;

    bool m_started = false;

    // Called when a valid capture command is received.
    std::function<void()> m_captureCommandCallback;

    void connect();

    void subscribeCommandTopic();

    void handleIncomingMessage(
        const char* topic,
        const uint8_t* payload,
        size_t len,
        size_t index,
        size_t total
    );

    void processCommand(
        const String& payload
    );

public:
    void begin(
        const String& siteId,
        const String& deviceId,
        const String& edgeId,
        const String& brokerHost,
        uint16_t brokerPort,
        const String& username,
        const String& password
    );

    void loop();

    void setCaptureCommandCallback(
        std::function<void()> callback
    );

    bool isConnected() const;

    bool publishTelemetry(const String& payload);

    bool publishImage(
        uint32_t sampleSeq,
        const uint8_t* imageData,
        size_t imageSize
    );
};