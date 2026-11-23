
#include "MQTTClient.h"

#include <WiFi.h>
#include <ArduinoJson.h>
#include <Preferences.h>

#include <cstring>
#include <cstdio>
#include <ctime>
#include <sys/time.h>
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
    m_configTopic = baseTopic + "/config";
    m_heartbeatTopic = baseTopic + "/heartbeat";
    m_ackTopic = baseTopic + "/fallback_ack";
    m_debugTopic = baseTopic + "/debug";
    m_eventTopic = baseTopic + "/events";
    m_bootId = String(esp_random(), HEX) + String(esp_random(), HEX);

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
        if (m_pumpStateCallback) {
            publishPumpState(m_pumpStateCallback());
        }
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

    // Routine QoS publish acknowledgements are intentionally quiet.

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
        if (properties.retain) return;

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

void MQTTClient::setStopPumpCallback(std::function<void()> callback) {
    m_stopPumpCallback = callback;
}

void MQTTClient::setPumpStateCallback(std::function<bool()> callback) {
    m_pumpStateCallback = callback;
}

bool MQTTClient::publishPumpStartRejected(const String& commandId, const char* reason, const String& detail, uint32_t retryMs) {
    Serial.printf("[PUMP REJECT] id=%s reason=%s retry_ms=%lu detail=%s\n", commandId.c_str(),
        reason, static_cast<unsigned long>(retryMs), detail.c_str());
    const time_t now = time(nullptr);
    if (!m_client.connected() || now < 1700000000) return false;
    auto timestamp = [](time_t value) {
        struct tm utc; gmtime_r(&value, &utc); char buffer[25];
        strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &utc); return String(buffer);
    };
    JsonDocument d;
    d["node_id"] = m_clientId; d["event"] = "pump_start_rejected";
    d["boot_id"] = m_bootId; d["sequence"] = ++m_eventSequence; d["command_id"] = commandId;
    d["reason"] = reason; d["detail"] = detail; d["retry_after_ms"] = retryMs;
    d["sent_at"] = timestamp(now); d["valid_until"] = timestamp(now + 10);
    String value; serializeJson(d, value);
    return publishControlEvent(value);
}

bool MQTTClient::publishPumpState(bool isOn, const String& commandId) {
    if (!m_hasSerialPumpState || m_serialPumpOn != isOn) {
        const String& id = commandId.isEmpty() ? m_activeRunId : commandId;
        Serial.printf("[PUMP %s] id=%s\n", isOn ? "ON" : "OFF", id.isEmpty() ? "-" : id.c_str());
        m_hasSerialPumpState = true; m_serialPumpOn = isOn;
    }
    const time_t now = time(nullptr);
    if (!m_client.connected() || now < 1700000000) return false;
    auto timestamp = [](time_t value) {
        struct tm utc;
        gmtime_r(&value, &utc);
        char buffer[25];
        strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &utc);
        return String(buffer);
    };
    JsonDocument document;
    document["node_id"] = m_clientId;
    document["event"] = isOn ? "pump_started" : "pump_stopped";
    document["boot_id"] = m_bootId;
    document["sequence"] = ++m_eventSequence;
    document["command_id"] = commandId;
    document["run_id"] = m_activeRunId;
    document["sent_at"] = timestamp(now);
    document["valid_until"] = timestamp(now + 10);
    String payload;
    serializeJson(document, payload);
    const bool queued = m_client.publish(m_eventTopic.c_str(), 1, false, payload.c_str()) != 0;
    if (!isOn) m_activeRunId = "";
    return queued;
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

    m_client.subscribe(m_configTopic.c_str(), 1);
    m_client.subscribe(m_heartbeatTopic.c_str(), 0);
    m_client.subscribe(m_ackTopic.c_str(), 1);
    m_client.subscribe(m_debugTopic.c_str(), 1);
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

    if (m_commandTopic != topic && m_configTopic != topic && m_heartbeatTopic != topic && m_ackTopic != topic && m_debugTopic != topic) {
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
    if (index != m_incomingPayload.length() || len > total || index > total - len) {
        m_incomingPayload = "";
        m_incomingTopic = "";
        m_expectedPayloadSize = 0;
        return;
    }
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

    // Config/heartbeat traffic is routine; command logs are emitted only
    // by processCommand on the application loop, never as raw payload dumps.

    {
        std::lock_guard<std::mutex> lock(m_queueMutex);
        if (m_controlQueue.size() < 32) m_controlQueue.emplace_back(m_incomingTopic, m_incomingPayload);
    }

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

    const char* logAction = document["action"].is<const char*>() ? document["action"].as<const char*>() : "?";
    const char* logId = document["command_id"].is<const char*>() ? document["command_id"].as<const char*>() : "-";
    Serial.printf("[CMD RX] action=%s id=%s duration_ms=%lu\n", logAction, logId,
        static_cast<unsigned long>(document["duration_ms"].as<uint32_t>()));

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

        if (!document["command_id"].is<const char*>()) return;
        const String commandId = document["command_id"].as<String>();
        if (commandId.length() != 32) return;

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

        Preferences history;
        if (!history.begin("pump_commands", false)) {
            Serial.printf("[CMD REJECT] id=%s reason=command_storage_unavailable\n", commandId.c_str()); return;
        }
        if (history.getString("last_start", "") == commandId) {
            history.end();
            Serial.printf("[CMD SKIP] id=%s reason=duplicate_start\n", commandId.c_str()); return;
        }
        if (history.putString("last_start", commandId) != commandId.length()) {
            history.end();
            Serial.printf("[CMD REJECT] id=%s reason=command_persist_failed\n", commandId.c_str()); return;
        }
        history.end();

        bool started = m_pumpCommandCallback(durationMs);

        if (started) {
            m_activeRunId = commandId;
            publishPumpState(true, commandId);

        }
        else {
            if (m_pumpStartRejectedCallback) m_pumpStartRejectedCallback(commandId);
            if (!m_pumpStartRejectedCallback) Serial.printf("[PUMP REJECT] id=%s reason=unspecified_callback_failure\n", commandId.c_str());
        }

        return;
    }

    if (strcmp(action, "stop_pump") == 0) {
        if (!document["command_id"].is<const char*>() || !m_stopPumpCallback) return;
        const String commandId = document["command_id"].as<String>();
        if (commandId.length() != 32) return;
        m_stopPumpCallback();
        publishPumpState(false, commandId);
        m_activeRunId = "";
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
    std::deque<std::pair<String, String>> pending;
    { std::lock_guard<std::mutex> lock(m_queueMutex); pending.swap(m_controlQueue); }
    for (const auto& message: pending) {
        if (message.first == m_commandTopic) processCommand(message.second);
        else if (m_controlCallback) m_controlCallback(message.first, message.second);
    }
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


bool MQTTClient::publishEvent(const char* event, uint32_t sampleSeq, const char* reason, uint32_t errorCode) {
    if (!m_client.connected()) return false;
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    if (tv.tv_sec < 1700000000) return false;
    auto timestamp = [](time_t seconds, long micros) {
        struct tm utc;
        gmtime_r(&seconds, &utc);
        char base[24], output[40];
        strftime(base, sizeof(base), "%Y-%m-%dT%H:%M:%S", &utc);
        snprintf(output, sizeof(output), "%s.%06ldZ", base, micros);
        return String(output);
    };
    JsonDocument document;
    document["node_id"] = m_clientId;
    document["event"] = event;
    document["sent_at"] = timestamp(tv.tv_sec, tv.tv_usec);
    document["valid_until"] = timestamp(tv.tv_sec + 60, tv.tv_usec);
    if (strcmp(event, "capture_succeeded") == 0 || strcmp(event, "capture_failed") == 0)
        document["sample_seq"] = sampleSeq;
    if (strcmp(event, "capture_failed") == 0) {
        document["reason"] = reason ? reason : "unknown_capture_failure";
        document["error_code"] = errorCode;
    }
    String payload;
    serializeJson(document, payload);
    String topic = "iot/" + m_edgeId + "/" + m_clientId + "/events";
    return m_client.publish(topic.c_str(), 1, false, payload.c_str()) != 0;
}
