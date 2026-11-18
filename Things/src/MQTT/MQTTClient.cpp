
#include "MQTTClient.h"

#include <WiFi.h>
#include <ArduinoJson.h>

#include <cstring>
#include <cstdio>
#include <ctime>
#include <cstdint>

// =====================================================
// ISO-8601 TIMESTAMP PARSER
// =====================================================

namespace {

bool isLeapYear(int year) {
    return (
        year % 400 == 0 ||
        (year % 4 == 0 && year % 100 != 0)
    );
}

int getDaysInMonth(int year, int month) {
    static const int days[] = {
        31, 28, 31, 30, 31, 30,
        31, 31, 30, 31, 30, 31
    };

    if (month == 2 && isLeapYear(year)) {
        return 29;
    }

    return days[month - 1];
}

// Convert a civil date to days since 1970-01-01.
int64_t daysFromCivil(int year, unsigned int month, unsigned int day) {
    year -= (month <= 2);

    const int era = (year >= 0 ? year : year - 399) / 400;

    const unsigned int yearOfEra =
        static_cast<unsigned int>(year - era * 400);

    const unsigned int adjustedMonth =
        month > 2 ? month - 3 : month + 9;

    const unsigned int dayOfYear =
        (153 * adjustedMonth + 2) / 5 + day - 1;

    const unsigned int dayOfEra =
        yearOfEra * 365 +
        yearOfEra / 4 -
        yearOfEra / 100 +
        dayOfYear;

    return static_cast<int64_t>(era) * 146097 +
           static_cast<int64_t>(dayOfEra) - 719468;
}

// Supported formats:
// 2026-11-11T14:47:05+00:00
// 2026-11-11T21:47:05+07:00
// 2026-11-11T14:47:05Z
bool parseIso8601(
    const char* timestamp,
    int64_t& unixTimestamp
) {
    if (timestamp == nullptr) {
        return false;
    }

    int year;
    int month;
    int day;

    int hour;
    int minute;
    int second;

    int consumed = 0;

    int parsed = sscanf(
        timestamp,
        "%4d-%2d-%2dT%2d:%2d:%2d%n",
        &year,
        &month,
        &day,
        &hour,
        &minute,
        &second,
        &consumed
    );

    if (parsed != 6) {
        return false;
    }

    // Validate date and time.
    if (year < 1970 || year > 9999) {
        return false;
    }

    if (month < 1 || month > 12) {
        return false;
    }

    if (day < 1 || day > getDaysInMonth(year, month)) {
        return false;
    }

    if (
        hour < 0 || hour > 23 ||
        minute < 0 || minute > 59 ||
        second < 0 || second > 59
    ) {
        return false;
    }

    const char* timezone = timestamp + consumed;

    int offsetSeconds = 0;

    if (
        timezone[0] == 'Z' &&
        timezone[1] == '\0'
    ) {
        offsetSeconds = 0;
    }
    else {
        if (
            (timezone[0] != '+' && timezone[0] != '-') ||
            strlen(timezone) != 6 ||
            timezone[3] != ':'
        ) {
            return false;
        }

        if (
            timezone[1] < '0' || timezone[1] > '9' ||
            timezone[2] < '0' || timezone[2] > '9' ||
            timezone[4] < '0' || timezone[4] > '9' ||
            timezone[5] < '0' || timezone[5] > '9'
        ) {
            return false;
        }

        int offsetHour =
            (timezone[1] - '0') * 10 +
            (timezone[2] - '0');

        int offsetMinute =
            (timezone[4] - '0') * 10 +
            (timezone[5] - '0');

        if (offsetHour > 14 || offsetMinute > 59) {
            return false;
        }

        if (offsetHour == 14 && offsetMinute != 0) {
            return false;
        }

        offsetSeconds =
            offsetHour * 3600 +
            offsetMinute * 60;

        if (timezone[0] == '-') {
            offsetSeconds = -offsetSeconds;
        }
    }

    int64_t days = daysFromCivil(
        year,
        static_cast<unsigned int>(month),
        static_cast<unsigned int>(day)
    );

    int64_t localSeconds =
        days * 86400LL +
        hour * 3600LL +
        minute * 60LL +
        second;

    // Convert local timestamp to UTC.
    unixTimestamp = localSeconds - offsetSeconds;

    return true;
}

} // namespace

// =====================================================
// INITIALIZATION
// =====================================================

void MQTTClient::begin(
    const String& edgeId,
    const String& deviceId,
    const String& brokerHost,
    uint16_t brokerPort,
    const String& username,
    const String& password
) {
    m_edgeId = edgeId;

    m_host = brokerHost;
    m_port = brokerPort;

    m_username = username;
    m_password = password;

    m_clientId = deviceId;

    // ---------------------------------------------
    // Build MQTT topics.
    // ---------------------------------------------

    const String baseTopic =
        "iot/" + edgeId + "/" + deviceId;

    m_telemetryTopic = baseTopic + "/telemetry";
    m_imageTopic = baseTopic + "/image";
    m_commandTopic = baseTopic + "/command";

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
        (void)properties;

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
// COMMAND CALLBACKS
// =====================================================

void MQTTClient::setCaptureCommandCallback(
    std::function<void()> callback
) {
    m_captureCommandCallback = callback;
}

void MQTTClient::setPumpCommandCallback(
    std::function<bool(uint32_t)> callback
) {
    m_pumpCommandCallback = callback;
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
    if (topic == nullptr || payload == nullptr) {
        return;
    }

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

    // Append received chunk.
    m_incomingPayload.concat(
        reinterpret_cast<const char*>(payload),
        len
    );

    // Wait until complete payload is received.
    if (m_incomingPayload.length() < m_expectedPayloadSize) {
        return;
    }

    if (m_incomingPayload.length() != m_expectedPayloadSize) {
        Serial.println("[MQTT] Invalid command payload length!");

        m_incomingPayload = "";
        m_incomingTopic = "";
        m_expectedPayloadSize = 0;

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
    // 1. Validate required fields.
    // ---------------------------------------------

    if (
        !document["edge_id"].is<const char*>() ||
        !document["node_id"].is<const char*>() ||
        !document["action"].is<const char*>() ||
        !document["issued_at"].is<const char*>() ||
        !document["ttl_ms"].is<uint32_t>()
    ) {
        Serial.println("[COMMAND] Rejected: missing or invalid fields!");
        return;
    }

    const char* edgeId = document["edge_id"];
    const char* nodeId = document["node_id"];
    const char* action = document["action"];
    const char* issuedAt = document["issued_at"];

    uint32_t ttlMs = document["ttl_ms"].as<uint32_t>();

    if (
        edgeId == nullptr ||
        nodeId == nullptr ||
        action == nullptr ||
        issuedAt == nullptr
    ) {
        Serial.println("[COMMAND] Rejected: null command field!");
        return;
    }

    // ---------------------------------------------
    // 2. Validate Edge ID.
    // ---------------------------------------------

    if (m_edgeId != edgeId) {
        Serial.println("[COMMAND] Rejected: wrong Edge ID!");
        return;
    }

    // ---------------------------------------------
    // 3. Validate Node ID.
    // ---------------------------------------------

    if (m_clientId != nodeId) {
        Serial.println("[COMMAND] Rejected: wrong Node ID!");
        return;
    }

    // ---------------------------------------------
    // 4. Validate TTL.
    // ---------------------------------------------

    if (ttlMs == 0) {
        Serial.println("[COMMAND] Rejected: TTL must be greater than zero!");
        return;
    }

    int64_t issuedAtSeconds = 0;

    if (!parseIso8601(issuedAt, issuedAtSeconds)) {
        Serial.println("[COMMAND] Rejected: invalid issued_at format!");
        return;
    }

    time_t currentTime = time(nullptr);

    // NTP must have synchronized the clock.
    if (currentTime < 1700000000) {
        Serial.println("[COMMAND] Rejected: system time is not synchronized!");
        return;
    }

    int64_t nowMs =
        static_cast<int64_t>(currentTime) * 1000LL;

    int64_t issuedAtMs =
        issuedAtSeconds * 1000LL;

    // Allow up to 5 seconds of clock difference.
    constexpr int64_t CLOCK_SKEW_TOLERANCE_MS = 5000;

    if (issuedAtMs > nowMs + CLOCK_SKEW_TOLERANCE_MS) {
        Serial.println("[COMMAND] Rejected: issued_at is in the future!");
        return;
    }

    if (
        nowMs >= issuedAtMs &&
        nowMs - issuedAtMs >= static_cast<int64_t>(ttlMs)
    ) {
        Serial.println("[COMMAND] Rejected: command has expired!");
        return;
    }

    Serial.println("[COMMAND] Edge ID: valid");
    Serial.println("[COMMAND] Node ID: valid");
    Serial.println("[COMMAND] TTL: valid");

    Serial.print("[COMMAND] Action: ");
    Serial.println(action);

    // ---------------------------------------------
    // 5. Process capture command.
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
    // 6. Process start_pump command.
    // ---------------------------------------------

    if (strcmp(action, "start_pump") == 0) {

        if (!document["duration_ms"].is<uint32_t>()) {
            Serial.println(
                "[COMMAND] Rejected: invalid duration_ms!"
            );

            return;
        }

        uint32_t durationMs =
            document["duration_ms"].as<uint32_t>();

        if (
            durationMs == 0 ||
            durationMs > MAX_PUMP_DURATION_MS
        ) {
            Serial.println(
                "[COMMAND] Rejected: duration must be between 1 and 60000 ms!"
            );

            return;
        }

        if (!m_pumpCommandCallback) {
            Serial.println(
                "[COMMAND] Pump callback is not configured!"
            );

            return;
        }

        Serial.println("[COMMAND] Pump command accepted!");

        Serial.print("[COMMAND] Pump duration: ");
        Serial.print(durationMs);
        Serial.println(" ms");

        bool started = m_pumpCommandCallback(durationMs);

        if (started) {
            Serial.println("[COMMAND] Pump started successfully!");
        }
        else {
            Serial.println(
                "[COMMAND] Pump start failed or pump is already running!"
            );
        }

        return;
    }

    // ---------------------------------------------
    // 7. Unsupported action.
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
