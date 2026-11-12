
#include <Arduino.h>
#include <WiFi.h>

#include <chrono>
#include <ctime>
#include <cstring>
#include <cstdlib>

#include "Config.h"

#include "Identity/DeviceIdentity.h"
#include "Sensors/SensorManager.h"
#include "Camera/CameraUARTClient.h"

#include "Telemetry/TelemetryBuilder.h"
#include "MQTT/MQTTClient.h"

// =====================================================
// CAMERA UART CONFIGURATION
// =====================================================

constexpr uint32_t CAMERA_UART_BAUD_RATE = 921600;

constexpr int CAMERA_UART_RX_PIN = 16;
constexpr int CAMERA_UART_TX_PIN = 17;

constexpr size_t CAMERA_UART_RX_BUFFER_SIZE = 8192;

HardwareSerial cameraSerial(2);

CameraUARTClient cameraClient;

uint32_t sampleSequence = 0;

// =====================================================
// SENSOR CONFIGURATION
// =====================================================

SensorManager sensorManager(
    27,     // Soil moisture pin
    26,     // DHT22 pin
    3200,   // Dry calibration
    1400    // Wet calibration
);

// =====================================================
// DEVICE IDENTITY
// =====================================================

DeviceIdentity deviceIdentity;

// =====================================================
// TELEMETRY
// =====================================================

TelemetryBuilder telemetryBuilder(
    Config::ZONE_ID
);

// =====================================================
// MQTT
// =====================================================

MQTTClient mqttClient;

String telemetryTopic;
String imageTopic;

// =====================================================
// CAMERA COMMAND
// =====================================================

// Set to true when an Edge capture command is received.
// Camera UART request will be handled in loop().
volatile bool captureCommandPending = false;

// =====================================================
// SCHEDULER
// =====================================================

uint32_t lastTelemetryTime = 0;

// =====================================================
// NTP CONFIGURATION
// =====================================================

const char* NTP_SERVER = "pool.ntp.org";
const char* SECONDARY_NTP_SERVER = "time.nist.gov";

constexpr std::time_t MIN_VALID_TIMESTAMP = 1700000000;

// =====================================================
// BUILD MQTT TOPICS
// =====================================================

void buildAndPrintTopics() {
    const String siteId = Config::SITE_ID;
    const String nodeId = deviceIdentity.getNodeId();

    telemetryTopic =
        "iot/" + siteId + "/" + nodeId + "/telemetry";

    imageTopic =
        "iot/" + siteId + "/" + nodeId + "/image";

    String commandTopic =
        "iot/" +
        String(Config::EDGE_ID) + "/" +
        nodeId +
        "/command";

    Serial.println();
    Serial.println("========== MQTT TOPICS ==========");

    Serial.print("[MQTT] Telemetry topic: ");
    Serial.println(telemetryTopic);

    Serial.print("[MQTT] Image topic: ");
    Serial.println(imageTopic);

    Serial.print("[MQTT] Command topic: ");
    Serial.println(commandTopic);

    Serial.println("=================================");
}

// =====================================================
// TIMESTAMP
// =====================================================

String getVietnamTimestamp() {
    auto now = std::chrono::system_clock::now();

    std::time_t currentTime =
        std::chrono::system_clock::to_time_t(now);

    if (currentTime < MIN_VALID_TIMESTAMP) {
        return "";
    }

    // Convert UTC to Vietnam time (UTC+7).
    std::time_t vietnamTime = currentTime + 7 * 3600;

    struct tm localTime;

    gmtime_r(&vietnamTime, &localTime);

    char timestamp[32];

    strftime(
        timestamp,
        sizeof(timestamp),
        "%Y-%m-%dT%H:%M:%S",
        &localTime
    );

    strcat(timestamp, "+07:00");

    return String(timestamp);
}

// =====================================================
// HANDLE CAMERA COMMAND
// =====================================================

void handleCaptureCommand() {

    if (!captureCommandPending) {
        return;
    }

    // Clear pending flag before processing.
    captureCommandPending = false;

    Serial.println();
    Serial.println("========== CAMERA COMMAND ==========");

    // Check whether camera is processing another request.
    if (cameraClient.isBusy()) {
        Serial.println(
            "[CAMERA] Capture rejected: camera is busy!"
        );

        return;
    }

    // Generate a new sequence for this capture.
    sampleSequence++;

    Serial.print("[CAMERA] Capture sequence: ");
    Serial.println(sampleSequence);

    // Send capture request to ESP32-S3.
    if (!cameraClient.requestCapture(sampleSequence)) {
        Serial.println(
            "[CAMERA] Failed to send capture request!"
        );

        return;
    }

    Serial.println(
        "[CAMERA] Capture request sent to ESP32-S3!"
    );

    Serial.println("====================================");
}

// =====================================================
// CAMERA RESPONSE HANDLING
// =====================================================

void handleCameraResponse() {

    // ---------------------------------------------
    // 1. Handle successfully received image.
    // ---------------------------------------------

    if (cameraClient.hasImage()) {

        // Keep the image in CameraUARTClient until
        // MQTT is connected.
        if (!mqttClient.isConnected()) {
            return;
        }

        CameraImage image;

        if (cameraClient.takeImage(image)) {

            Serial.println();
            Serial.println("========== CAMERA IMAGE RECEIVED ==========");

            Serial.print("[CAMERA] Sample sequence: ");
            Serial.println(image.sampleSeq);

            Serial.print("[CAMERA] Image size: ");
            Serial.print(image.imageSize);
            Serial.println(" bytes");

            Serial.print("[MQTT] Image topic: ");
            Serial.println(imageTopic);

            // -----------------------------------------
            // Publish JPEG image to MQTT.
            // -----------------------------------------

            bool published = mqttClient.publishImage(
                image.sampleSeq,
                image.data,
                image.imageSize
            );

            if (published) {
                Serial.println(
                    "[CAMERA] Image submitted to MQTT successfully!"
                );
            }
            else {
                Serial.println(
                    "[CAMERA] Image MQTT publish failed!"
                );
            }

            // -----------------------------------------
            // Release image memory.
            // -----------------------------------------

            free(image.data);

            image.data = nullptr;

            Serial.println("[CAMERA] Image memory released!");

            Serial.println("===========================================");
        }
    }

    // ---------------------------------------------
    // 2. Handle camera error.
    // ---------------------------------------------

    if (cameraClient.hasError()) {

        Serial.println();
        Serial.println("[CAMERA] Capture failed!");

        // Reset parser so that the next request
        // can be processed.
        cameraClient.reset();
    }
}

// =====================================================
// TELEMETRY HANDLING
// =====================================================

void handleTelemetry() {

    uint32_t currentTime = millis();

    if (
        currentTime - lastTelemetryTime <
        Config::TELEMETRY_INTERVAL_MS
    ) {
        return;
    }

    lastTelemetryTime = currentTime;

    // ---------------------------------------------
    // 1. Read sensors.
    // ---------------------------------------------

    SensorData sensorData = sensorManager.readAll();

    // ---------------------------------------------
    // 2. Generate sample sequence.
    // ---------------------------------------------

    sampleSequence++;

    // ---------------------------------------------
    // 3. Get timestamp.
    // ---------------------------------------------

    String sampledAt = getVietnamTimestamp();

    // ---------------------------------------------
    // 4. Build telemetry.
    // ---------------------------------------------

    TelemetryData telemetry = telemetryBuilder.build(
        sensorData,
        deviceIdentity,
        sampledAt
    );

    // ---------------------------------------------
    // 5. Convert telemetry to JSON.
    // ---------------------------------------------

    String payload = telemetryBuilder.toJson(telemetry);

    // ---------------------------------------------
    // 6. Print telemetry.
    // ---------------------------------------------

    Serial.println();
    Serial.println("========== TELEMETRY ==========");

    Serial.print("[TELEMETRY] Sample sequence: ");
    Serial.println(sampleSequence);

    Serial.print("[MQTT] Telemetry topic: ");
    Serial.println(telemetryTopic);

    Serial.println("[MQTT] Payload:");
    Serial.println(payload);

    // ---------------------------------------------
    // 7. Publish telemetry.
    // ---------------------------------------------

    bool telemetryPublished =
        mqttClient.publishTelemetry(payload);

    if (telemetryPublished) {
        Serial.println(
            "[TELEMETRY] Publish request accepted!"
        );
    }
    else {
        Serial.println(
            "[TELEMETRY] Publish request failed!"
        );
    }
}

// =====================================================
// SETUP
// =====================================================

void setup() {

    Serial.begin(115200);

    delay(1000);

    Serial.println();
    Serial.println("======================================");
    Serial.println("       ESP32 MAIN CONTROLLER");
    Serial.println("======================================");

    // ---------------------------------------------
    // 1. Initialize camera UART.
    // ---------------------------------------------

    Serial.println();
    Serial.println("========== CAMERA UART INITIALIZATION ==========");

    // Increase RX buffer BEFORE HardwareSerial.begin().
    cameraSerial.setRxBufferSize(
        CAMERA_UART_RX_BUFFER_SIZE
    );

    cameraClient.begin(
        cameraSerial,
        CAMERA_UART_BAUD_RATE,
        CAMERA_UART_RX_PIN,
        CAMERA_UART_TX_PIN
    );

    Serial.println("[UART] Camera UART initialized!");

    Serial.printf(
        "[UART] RX: GPIO%d\n",
        CAMERA_UART_RX_PIN
    );

    Serial.printf(
        "[UART] TX: GPIO%d\n",
        CAMERA_UART_TX_PIN
    );

    Serial.printf(
        "[UART] Baud rate: %lu\n",
        static_cast<unsigned long>(CAMERA_UART_BAUD_RATE)
    );

    Serial.printf(
        "[UART] RX buffer requested: %u bytes\n",
        static_cast<unsigned int>(CAMERA_UART_RX_BUFFER_SIZE)
    );

    // ---------------------------------------------
    // 2. Initialize WiFi.
    // ---------------------------------------------

    WiFi.mode(WIFI_AP_STA);

    // ---------------------------------------------
    // 3. Initialize device identity.
    // ---------------------------------------------

    Serial.println();
    Serial.println("========== DEVICE IDENTITY ==========");

    deviceIdentity.begin();

    Serial.print("[DEVICE] Node ID: ");
    Serial.println(deviceIdentity.getNodeId());

    // ---------------------------------------------
    // 4. Initialize sensors.
    // ---------------------------------------------

    Serial.println();
    Serial.println("========== SENSOR INITIALIZATION ==========");

    sensorManager.begin();

    // ---------------------------------------------
    // 5. Connect WiFi.
    // ---------------------------------------------

    Serial.println();
    Serial.println("========== WIFI CONNECTION ==========");

    WiFi.begin(
        Config::WIFI_SSID,
        Config::WIFI_PASSWORD
    );

    Serial.print("[WIFI] Connecting");

    while (WiFi.status() != WL_CONNECTED) {
        delay(500);

        Serial.print(".");
    }

    Serial.println();
    Serial.println("[WIFI] Connected!");

    Serial.print("[WIFI] IP: ");
    Serial.println(WiFi.localIP());

    // ---------------------------------------------
    // 6. Synchronize time using NTP.
    // ---------------------------------------------

    Serial.println();
    Serial.println("========== NTP SYNCHRONIZATION ==========");

    configTime(
        0,
        0,
        NTP_SERVER,
        SECONDARY_NTP_SERVER
    );

    String sampledAt = "";

    uint32_t ntpStartTime = millis();

    while (
        sampledAt.isEmpty() &&
        millis() - ntpStartTime < 15000
    ) {
        sampledAt = getVietnamTimestamp();

        if (sampledAt.isEmpty()) {
            delay(500);

            Serial.print(".");
        }
    }

    if (!sampledAt.isEmpty()) {
        Serial.println();
        Serial.println("[NTP] Time synchronized!");

        Serial.print("[NTP] Vietnam time: ");
        Serial.println(sampledAt);
    }
    else {
        Serial.println();
        Serial.println("[NTP] Synchronization failed!");

        Serial.println(
            "[NTP] Timestamp unavailable until synchronized."
        );
    }

    // ---------------------------------------------
    // 7. Configure capture command callback.
    // ---------------------------------------------

    mqttClient.setCaptureCommandCallback([]() {
        Serial.println(
            "[COMMAND] Capture request received from Edge!"
        );

        captureCommandPending = true;
    });

    // ---------------------------------------------
    // 8. Initialize MQTT.
    // ---------------------------------------------

    Serial.println();
    Serial.println("========== MQTT INITIALIZATION ==========");

    mqttClient.begin(
        Config::SITE_ID,
        deviceIdentity.getNodeId(),
        Config::EDGE_ID,
        Config::MQTT_HOST,
        Config::MQTT_PORT,
        Config::MQTT_USERNAME,
        Config::MQTT_PASSWORD
    );

    // Print MQTT topics.
    buildAndPrintTopics();

    // ---------------------------------------------
    // 9. Initialize sampling timer.
    // ---------------------------------------------

    lastTelemetryTime = millis();

    Serial.println();
    Serial.println("========== SYSTEM READY ==========");
}

// =====================================================
// LOOP
// =====================================================

void loop() {

    // ---------------------------------------------
    // 1. Maintain MQTT connection.
    // ---------------------------------------------

    mqttClient.loop();

    // ---------------------------------------------
    // 2. Handle pending Edge capture command.
    // ---------------------------------------------

    handleCaptureCommand();

    // ---------------------------------------------
    // 3. Receive camera data continuously.
    // ---------------------------------------------

    cameraClient.loop();

    // ---------------------------------------------
    // 4. Process camera response.
    // ---------------------------------------------

    handleCameraResponse();

    // ---------------------------------------------
    // 5. Wait for MQTT connection.
    // ---------------------------------------------

    if (!mqttClient.isConnected()) {
        delay(1);

        return;
    }

    // ---------------------------------------------
    // 6. Read sensors and publish telemetry.
    // ---------------------------------------------

    handleTelemetry();
}
