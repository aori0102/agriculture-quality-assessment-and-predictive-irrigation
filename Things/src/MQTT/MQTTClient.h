
#pragma once

#include <Arduino.h>
#include <espMqttClient.h>
#include <functional>
#include <deque>
#include <mutex>

class MQTTClient {
private:
    espMqttClient m_client;
    std::mutex m_queueMutex;
    std::deque<std::pair<String, String>> m_controlQueue;
    std::function<void(const String&, const String&)> m_controlCallback;
    String m_configTopic, m_heartbeatTopic, m_ackTopic;

    String m_edgeId;
    String m_host;
    String m_username;
    String m_password;
    String m_clientId;

    String m_telemetryTopic;
    String m_imageTopic;
    String m_commandTopic;
    String m_eventTopic;
    String m_bootId;
    String m_activeRunId;
    uint32_t m_eventSequence = 0;

    // Incoming command buffer.
    String m_incomingTopic;
    String m_incomingPayload;
    size_t m_expectedPayloadSize = 0;

    uint16_t m_port = 1883;

    uint32_t m_lastReconnectAttempt = 0;

    static constexpr uint32_t RECONNECT_INTERVAL = 5000;

    static constexpr size_t MAX_COMMAND_SIZE = 2048;

    static constexpr uint32_t MAX_PUMP_DURATION_MS = 60000;

    bool m_started = false;

    // Called when a valid capture command is received.
    std::function<void()> m_captureCommandCallback;

    // Called when a valid start_pump command is received.
    // Returns true if the pump starts successfully.
    std::function<bool(uint32_t)> m_pumpCommandCallback;
    std::function<void()> m_stopPumpCallback;
    std::function<bool()> m_pumpStateCallback;

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
    void setControlCallback(std::function<void(const String&, const String&)> fn) { m_controlCallback = fn; }
    bool publishControlEvent(const String& value) { return m_client.connected() && m_client.publish(m_eventTopic.c_str(), 1, false, value.c_str()) != 0; }
    void begin(
        const String& edgeId,
        const String& deviceId,
        const String& brokerHost,
        uint16_t brokerPort,
        const String& username,
        const String& password
    );

    void loop();

    void setCaptureCommandCallback(
        std::function<void()> callback
    );

    void setPumpCommandCallback(
        std::function<bool(uint32_t)> callback
    );
    void setStopPumpCallback(std::function<void()> callback);
    void setPumpStateCallback(std::function<bool()> callback);
    bool publishPumpState(bool isOn, const String& commandId = "");

    bool isConnected() const;

    bool publishEvent(const char* event, uint32_t sampleSeq = 0);

    bool publishTelemetry(const String& payload);

    bool publishImage(
        uint32_t sampleSeq,
        const uint8_t* imageData,
        size_t imageSize
    );
};
