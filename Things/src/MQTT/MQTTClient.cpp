#include "MQTTClient.h"

#include <WiFi.h>
#include <ArduinoJson.h>
#include <cstring>

#include "TopicBuilder.h"

// =====================================================
// INITIALIZATION
// =====================================================

void MQTTClient::begin(
    const String& siteId,
    const String& deviceId,
    const String& edgeId,
    const String& brokerHost,
    uint16_t brokerPort,
    const String& username,
    const String& password
) {
    m_host = brokerHost;
    m_port = brokerPort;

    m_username = username;
    m_password = password;

    m_clientId = deviceId;

    // ---------------------------------------------
    // Build MQTT topics.
    // ---------------------------------------------

    m_telemetryTopic = TopicBuilder::telemetryTopic(
        siteId,
        deviceId
    );

    m_imageTopic =
        "iot/" +
        siteId + "/" +
        deviceId +
        "/image";

    m_commandTopic =
        "iot/" +
        edgeId + "/" +
        deviceId +
        "/commands";

    // ---------------------------------------------
    // MQTT configuration.
    // ---------------------------------------------

    m_client.setServer(m_host.c_str(), m_port);
    m_client.setClientId(m_clientId.c_str());
    m_client.setKeepAlive(60);

    if (!m_username.isEmpty()) {
        m_client.setCredentials(
            m_username.c_str(),
            m_password.c_str()
        );
    }

    // ---------------------------------------------
    // Connected callback.
    // ---------------------------------------------

    m_client.onConnect([this](bool sessionPresent) {
        Serial.println("[MQTT] Connected to broker");

        Serial.print("[MQTT] Session present: ");
        Serial.println(sessionPresent);

        // Subscribe whenever the MQTT connection is established.
        subscribeCommandTopic();
    });

    // ---------------------------------------------
    // Disconnected callback.
    // ---------------------------------------------

    m_client.onDisconnect([](
        espMqttClientTypes::DisconnectReason reason
    ) {
        Serial.print("[MQTT] Disconnected. Reason: ");
        Serial.println(static_cast<uint8_t>(reason));
    });

    // ---------------------------------------------
    // Publish acknowledgement callback.
    // ---------------------------------------------

    m_client.onPublish([](uint16_t packetId) {
        Serial.print("[MQTT] Publish acknowledged. Packet ID: ");
        Serial.println(packetId);
    });

    // ---------------------------------------------
    // Incoming message callback.
    // ---------------------------------------------

    m_client.onMessage([this](
        const espMqttClientTypes::MessageProperties& properties,
        const char* topic,
        const uint8_t* payload,
        size_t len,
        size_t index,
        size_t total
    ) {
        handleIncomingMessage(
            topic,
            payload,
            len,
            index,
            total
        );
    });

    m_started = true;

    connect();
}

// =====================================================
// CAPTURE CALLBACK
// =====================================================

void MQTTClient::setCaptureCommandCallback(
    std::function<void()> callback
) {
    m_captureCommandCallback = callback;
}

// =====================================================
// CONNECTION
// =====================================================

void MQTTClient::connect() {
    if (!WiFi.isConnected()) {
        Serial.println("[MQTT] Waiting for WiFi...");
        return;
    }

    if (m_client.connected()) {
        return;
    }

    Serial.print("[MQTT] Connecting to ");
    Serial.println(m_host);

    m_client.connect();
}

// =====================================================
// SUBSCRIBE COMMAND TOPIC
// =====================================================

void MQTTClient::subscribeCommandTopic() {
    if (!m_client.connected()) {
        return;
    }

    Serial.println("\n========== MQTT SUBSCRIBE ==========");

    Serial.print("[MQTT] Command topic: ");
    Serial.println(m_commandTopic);

    uint16_t packetId = m_client.subscribe(
        m_commandTopic.c_str(),
        1
    );

    if (packetId == 0) {
        Serial.println("[MQTT] Command subscription failed!");
        return;
    }

    Serial.print("[MQTT] Subscribe request sent. Packet ID: ");
    Serial.println(packetId);
}

// =====================================================
// RECEIVE MQTT MESSAGE
// =====================================================

void MQTTClient::handleIncomingMessage(
    const char* topic,
    const uint8_t* payload,
    size_t len,
    size_t index,
    size_t total
) {
    // Only process our command topic.
    if (m_commandTopic != topic) {
        return;
    }

    // Reject oversized commands.
    if (total > MAX_COMMAND_SIZE) {
        Serial.println("[MQTT] Command payload is too large!");

        m_incomingPayload = "";
        m_incomingTopic = "";
        m_expectedPayloadSize = 0;

        return;
    }

    // Start receiving a new MQTT message.
    if (index == 0) {
        m_incomingPayload = "";
        m_incomingTopic = topic;
        m_expectedPayloadSize = total;

        m_incomingPayload.reserve(total + 1);
    }

    // Make sure this chunk belongs to the current message.
    if (m_incomingTopic != topic) {
        return;
    }

    // Append the received chunk.
    m_incomingPayload.concat(
        reinterpret_cast<const char*>(payload),
        len
    );

    // Wait until the complete payload is received.
    if (m_incomingPayload.length() < m_expectedPayloadSize) {
        return;
    }

    if (m_incomingPayload.length() != m_expectedPayloadSize) {
        Serial.println("[MQTT] Invalid command payload length!");

        m_incomingPayload = "";
        m_incomingTopic = "";

        return;
    }

    Serial.println("\n========== MQTT COMMAND RECEIVED ==========");

    Serial.print("[MQTT] Topic: ");
    Serial.println(topic);

    Serial.println("[MQTT] Payload:");
    Serial.println(m_incomingPayload);

    processCommand(m_incomingPayload);

    // Clear buffer after processing.
    m_incomingPayload = "";
    m_incomingTopic = "";
    m_expectedPayloadSize = 0;
}

// =====================================================
// PROCESS COMMAND
// =====================================================

void MQTTClient::processCommand(
    const String& payload
) {
    StaticJsonDocument<512> document;

    DeserializationError error = deserializeJson(
        document,
        payload
    );

    if (error) {
        Serial.print("[COMMAND] JSON parsing failed: ");
        Serial.println(error.c_str());

        return;
    }

    // ---------------------------------------------
    // Read command fields.
    // ---------------------------------------------

    const char* nodeId = document["node_id"] | "";
    const char* action = document["action"] | "";

    Serial.print("[COMMAND] Node ID: ");
    Serial.println(nodeId);

    Serial.print("[COMMAND] Action: ");
    Serial.println(action);

    // ---------------------------------------------
    // Validate target node.
    // ---------------------------------------------

    if (m_clientId != nodeId) {
        Serial.println("[COMMAND] Rejected: wrong node ID!");
        return;
    }

    // ---------------------------------------------
    // Process capture command.
    // ---------------------------------------------

    if (strcmp(action, "capture") == 0) {
        Serial.println("[COMMAND] Capture command accepted!");

        if (!m_captureCommandCallback) {
            Serial.println(
                "[COMMAND] Capture callback is not configured!"
            );

            return;
        }

        m_captureCommandCallback();

        return;
    }

    // ---------------------------------------------
    // Unsupported action.
    // ---------------------------------------------

    Serial.print("[COMMAND] Unsupported action: ");
    Serial.println(action);
}

// =====================================================
// UPDATE
// =====================================================

void MQTTClient::loop() {
    if (!m_started || !WiFi.isConnected()) {
        return;
    }

    if (m_client.connected()) {
        return;
    }

    uint32_t currentTime = millis();

    if (
        currentTime - m_lastReconnectAttempt >=
        RECONNECT_INTERVAL
    ) {
        m_lastReconnectAttempt = currentTime;

        connect();
    }
}

// =====================================================
// CONNECTION STATUS
// =====================================================

bool MQTTClient::isConnected() const {
    return m_client.connected();
}

// =====================================================
// PUBLISH TELEMETRY
// =====================================================

bool MQTTClient::publishTelemetry(const String& payload) {
    if (!m_client.connected()) {
        Serial.println(
            "[MQTT] Cannot publish telemetry: disconnected"
        );

        return false;
    }

    uint16_t packetId = m_client.publish(
        m_telemetryTopic.c_str(),
        1,
        false,
        payload.c_str()
    );

    if (packetId == 0) {
        Serial.println("[MQTT] Telemetry publish failed");
        return false;
    }

    Serial.println("[MQTT] Telemetry queued for publishing");

    Serial.print("[MQTT] Topic: ");
    Serial.println(m_telemetryTopic);

    Serial.print("[MQTT] Packet ID: ");
    Serial.println(packetId);

    return true;
}

// =====================================================
// PUBLISH CAMERA IMAGE
// =====================================================

bool MQTTClient::publishImage(
    uint32_t sampleSeq,
    const uint8_t* imageData,
    size_t imageSize
) {
    if (!m_client.connected()) {
        Serial.println(
            "[MQTT] Cannot publish image: disconnected"
        );

        return false;
    }

    if (imageData == nullptr || imageSize == 0) {
        Serial.println(
            "[MQTT] Cannot publish image: invalid data"
        );

        return false;
    }

    Serial.println(
        "\n========== MQTT IMAGE PUBLISH =========="
    );

    Serial.print("[MQTT] Topic: ");
    Serial.println(m_imageTopic);

    Serial.print("[MQTT] Sample sequence: ");
    Serial.println(sampleSeq);

    Serial.print("[MQTT] Image size: ");
    Serial.print(imageSize);
    Serial.println(" bytes");

    uint16_t packetId = m_client.publish(
        m_imageTopic.c_str(),
        1,
        false,
        imageData,
        imageSize
    );

    if (packetId == 0) {
        Serial.println("[MQTT] Image publish failed!");
        return false;
    }

    Serial.println("[MQTT] Image queued successfully");

    Serial.print("[MQTT] Packet ID: ");
    Serial.println(packetId);

    return true;
}