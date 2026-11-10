#include "MQTTClient.h"

#include <WiFi.h>

#include "TopicBuilder.h"

void MQTTClient::begin(
    const String& edgeId,
    const String& nodeId,
    const String& brokerHost,
    uint16_t brokerPort,
    const String& username,
    const String& password
) {
    m_host = brokerHost;
    m_port = brokerPort;

    m_username = username;
    m_password = password;

    m_clientId = nodeId;

    m_telemetryTopic = TopicBuilder::telemetryTopic(
        edgeId,
        nodeId
    );

    // MQTT broker configuration
    m_client.setServer(m_host.c_str(), m_port);
    m_client.setClientId(m_clientId.c_str());
    m_client.setKeepAlive(60);

    if (!m_username.isEmpty()) {
        m_client.setCredentials(
            m_username.c_str(),
            m_password.c_str()
        );
    }

    // Connected callback
    m_client.onConnect([](bool sessionPresent) {
        Serial.println("[MQTT] Connected to broker");

        Serial.print("[MQTT] Session present: ");
        Serial.println(sessionPresent);
    });

    // Disconnected callback
    m_client.onDisconnect([](
        espMqttClientTypes::DisconnectReason reason
    ) {
        Serial.print("[MQTT] Disconnected. Reason: ");
        Serial.println(static_cast<uint8_t>(reason));
    });

    // QoS 1 publish acknowledgement
    m_client.onPublish([](uint16_t packetId) {
        Serial.print("[MQTT] Publish acknowledged. Packet ID: ");
        Serial.println(packetId);
    });

    m_started = true;

    connect();
}

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

void MQTTClient::loop() {
    if (!m_started || !WiFi.isConnected()) {
        return;
    }

    if (m_client.connected()) {
        return;
    }

    uint32_t currentTime = millis();

    if (currentTime - m_lastReconnectAttempt >= RECONNECT_INTERVAL) {
        m_lastReconnectAttempt = currentTime;
        connect();
    }
}

bool MQTTClient::isConnected() const {
    return m_client.connected();
}

bool MQTTClient::publishTelemetry(const String& payload) {
    if (!m_client.connected()) {
        Serial.println("[MQTT] Cannot publish: not connected");
        return false;
    }

    // QoS 1, retain = false
    uint16_t packetId = m_client.publish(
        m_telemetryTopic.c_str(),
        1,
        false,
        payload.c_str()
    );

    if (packetId == 0) {
        Serial.println("[MQTT] Publish failed");
        return false;
    }

    Serial.println("[MQTT] Telemetry queued for publishing");
    Serial.print("[MQTT] Topic: ");
    Serial.println(m_telemetryTopic);

    Serial.print("[MQTT] Packet ID: ");
    Serial.println(packetId);

    return true;
}