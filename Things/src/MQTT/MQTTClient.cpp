
#include "MQTTClient.h"

#include <WiFi.h>

#include "TopicBuilder.h"

// =====================================================
// INITIALIZATION
// =====================================================

void MQTTClient::begin(
    const String& siteId,
    const String& deviceId,
    const String& brokerHost,
    uint16_t brokerPort,
    const String& username,
    const String& password
) {
    m_host = brokerHost;
    m_port = brokerPort;

    m_username = username;
    m_password = password;

    // Use ESP32 MAC address as MQTT client ID.
    m_clientId = deviceId;

    // Build telemetry topic.
    m_telemetryTopic = TopicBuilder::telemetryTopic(
        siteId,
        deviceId
    );

    // Build image topic.
    // Example: iot/site01/esp01/image
    m_imageTopic =
        "iot/" +
        siteId + "/" +
        deviceId +
        "/image";

    // MQTT broker configuration.
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

    m_client.onConnect([](bool sessionPresent) {
        Serial.println("[MQTT] Connected to broker");

        Serial.print("[MQTT] Session present: ");
        Serial.println(sessionPresent);
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

    m_started = true;

    connect();
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

    // QoS 1, retain = false.
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

    Serial.println(
        "[MQTT] Telemetry queued for publishing"
    );

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
    // ---------------------------------------------
    // 1. Check MQTT connection.
    // ---------------------------------------------

    if (!m_client.connected()) {
        Serial.println(
            "[MQTT] Cannot publish image: disconnected"
        );

        return false;
    }

    // ---------------------------------------------
    // 2. Validate image data.
    // ---------------------------------------------

    if (imageData == nullptr || imageSize == 0) {
        Serial.println(
            "[MQTT] Cannot publish image: invalid data"
        );

        return false;
    }

    // ---------------------------------------------
    // 3. Print image information.
    // ---------------------------------------------

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

    // ---------------------------------------------
    // 4. Publish raw JPEG binary.
    // ---------------------------------------------

    uint16_t packetId = m_client.publish(
        m_imageTopic.c_str(),
        1,              // QoS 1
        false,          // retain = false
        imageData,
        imageSize
    );

    // ---------------------------------------------
    // 5. Check publish result.
    // ---------------------------------------------

    if (packetId == 0) {
        Serial.println("[MQTT] Image publish failed!");

        return false;
    }

    Serial.println(
        "[MQTT] Image queued successfully"
    );

    Serial.print("[MQTT] Packet ID: ");
    Serial.println(packetId);

    return true;
}
