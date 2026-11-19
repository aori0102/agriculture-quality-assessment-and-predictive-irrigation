
#include <Arduino.h>
#include <WiFi.h>

#include <chrono>
#include <ctime>
#include <cstring>
#include <cstdlib>

#include "Configuration/Config.h"
#include "Configuration/DeviceConfig.h"
#include "Configuration/ConfigPortal.h"

#include "Identity/DeviceIdentity.h"
#include "Sensors/SensorManager.h"
#include "Camera/CameraUARTClient.h"

#include "Telemetry/TelemetryBuilder.h"
#include "MQTT/MQTTClient.h"

#include "Actuator/PowerButton.h"
#include "Actuator/StatusLed.h"
#include "Actuator/PumpController.h"


// =====================================================
// SYSTEM OBJECTS
// =====================================================

PowerButton powerButton;
StatusLed statusLed;

PumpController pumpController(
    18, // L298N ENA
    19, // L298N IN1
    27  // L298N IN2
);


// =====================================================
// CAMERA UART CONFIGURATION
// =====================================================

constexpr uint32_t CAMERA_UART_BAUD_RATE = 921600;

constexpr int CAMERA_UART_RX_PIN = 16;
constexpr int CAMERA_UART_TX_PIN = 17;

constexpr size_t CAMERA_UART_RX_BUFFER_SIZE = 65536;

HardwareSerial cameraSerial(2);

CameraUARTClient cameraClient;

uint32_t sampleSequence = 0;


// =====================================================
// SENSOR CONFIGURATION
// =====================================================

SensorManager sensorManager(
    34,     // Soil moisture pin (ADC1)
    26,     // DHT22 pin
    3200,   // Dry calibration
    1400    // Wet calibration
);


// =====================================================
// DEVICE IDENTITY
// =====================================================

DeviceIdentity deviceIdentity;


// =====================================================
// DEVICE CONFIGURATION
// =====================================================

DeviceConfigManager configManager;

ConfigPortal configPortal;

TelemetryBuilder telemetryBuilder;


// =====================================================
// MQTT
// =====================================================

MQTTClient mqttClient;

String telemetryTopic;
String imageTopic;


// =====================================================
// CAMERA COMMAND
// =====================================================

volatile bool captureCommandPending = false;


// =====================================================
// SCHEDULER
// =====================================================

uint32_t lastTelemetryTime = 0;


// =====================================================
// WIFI CONFIGURATION
// =====================================================

constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 15000;


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

    const DeviceConfig& config = configManager.get();

    const String edgeId = config.edgeId;
    const String nodeId = deviceIdentity.getNodeId();

    const String baseTopic =
        "iot/" + edgeId + "/" + nodeId;

    telemetryTopic = baseTopic + "/telemetry";
    imageTopic = baseTopic + "/image";

    String commandTopic = baseTopic + "/command";

    Serial.println();
    Serial.println("========== MQTT TOPICS ==========");

    Serial.print("[MQTT] Edge ID: ");
    Serial.println(edgeId);

    Serial.print("[MQTT] Node ID: ");
    Serial.println(nodeId);

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
// HANDLE POWER BUTTON ACTION
// =====================================================

void handlePowerButton() {

    PowerButton::Action action = powerButton.update();

    switch (action) {

        case PowerButton::Action::RESTART:

            Serial.println();
            Serial.println("[POWER] Stopping pump before restart...");

            pumpController.stop();

            Serial.println("[POWER] Restarting ESP32...");

            Serial.flush();

            ESP.restart();

            break;


        case PowerButton::Action::DEEP_SLEEP:

            Serial.println(
                "[POWER] Stopping pump before deep sleep..."
            );

            pumpController.stop();

            powerButton.enterDeepSleep();

            break;


        case PowerButton::Action::OPEN_CONFIG:

            Serial.println(
                "[POWER] Stopping pump before opening portal..."
            );

            pumpController.stop();

            Serial.println(
                "[POWER] Opening configuration portal..."
            );

            configPortal.open();

            break;


        case PowerButton::Action::NONE:
        default:

            break;
    }
}


// =====================================================
// HANDLE CAPTURE COMMAND
// =====================================================

void handleCaptureCommand() {

    if (!captureCommandPending) {
        return;
    }

    captureCommandPending = false;

    Serial.println();
    Serial.println("========== CAMERA COMMAND ==========");

    if (cameraClient.isBusy()) {

        Serial.println(
            "[CAMERA] Capture rejected: camera is busy!"
        );

        return;
    }

    sampleSequence++;

    Serial.print("[CAMERA] Capture sequence: ");
    Serial.println(sampleSequence);

    if (!cameraClient.requestCapture(sampleSequence)) {

        Serial.println(
            "[CAMERA] Failed to send capture request!"
        );

        return;
    }

    Serial.println(
        "[CAMERA] Capture request sent to camera!"
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

        if (!mqttClient.isConnected()) {
            return;
        }

        CameraImage image;

        if (cameraClient.takeImage(image)) {

            Serial.println();
            Serial.println(
                "========== CAMERA IMAGE RECEIVED =========="
            );

            Serial.print("[CAMERA] Sample sequence: ");
            Serial.println(image.sampleSeq);

            Serial.print("[CAMERA] Image size: ");
            Serial.print(image.imageSize);
            Serial.println(" bytes");

            Serial.print("[MQTT] Image topic: ");
            Serial.println(imageTopic);

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

            free(image.data);

            image.data = nullptr;

            Serial.println(
                "[CAMERA] Image memory released!"
            );

            Serial.println(
                "==========================================="
            );
        }
    }

    // ---------------------------------------------
    // 2. Handle camera error.
    // ---------------------------------------------

    if (cameraClient.hasError()) {

        Serial.println();
        Serial.println("[CAMERA] Capture failed!");

        cameraClient.reset();
    }
}


// =====================================================
// TELEMETRY HANDLING
// =====================================================

void handleTelemetry() {

    const DeviceConfig& config = configManager.get();

    uint32_t currentTime = millis();

    if (
        currentTime - lastTelemetryTime <
        config.telemetryIntervalMs
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

    Serial.print("[TELEMETRY] Interval: ");
    Serial.print(config.telemetryIntervalMs);
    Serial.println(" ms");

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
// WIFI CONNECTION
// =====================================================

bool connectWiFi() {

    const DeviceConfig& config = configManager.get();

    Serial.println();
    Serial.println("========== WIFI CONNECTION ==========");

    WiFi.mode(WIFI_STA);

    Serial.print("[WIFI] SSID: ");
    Serial.println(config.wifiSSID);

    WiFi.begin(
        config.wifiSSID.c_str(),
        config.wifiPassword.c_str()
    );

    Serial.print("[WIFI] Connecting");

    uint32_t startTime = millis();
    uint32_t lastDotTime = startTime;

    while (
        WiFi.status() != WL_CONNECTED &&
        millis() - startTime < WIFI_CONNECT_TIMEOUT_MS
    ) {

        handlePowerButton();

        statusLed.update(false);

        pumpController.update();

        if (millis() - lastDotTime >= 250) {

            Serial.print(".");

            lastDotTime = millis();
        }

        delay(10);
    }

    Serial.println();

    if (WiFi.status() != WL_CONNECTED) {

        Serial.println("[WIFI] Connection failed!");

        Serial.println(
            "[WIFI] Device will continue running. "
            "Use GPIO25 to open configuration portal."
        );

        return false;
    }

    Serial.println("[WIFI] Connected!");

    Serial.print("[WIFI] IP: ");
    Serial.println(WiFi.localIP());

    return true;
}


// =====================================================
// NTP INITIALIZATION
// =====================================================

void initializeNTP() {

    if (WiFi.status() != WL_CONNECTED) {

        Serial.println(
            "[NTP] Skipped because WiFi is disconnected."
        );

        return;
    }

    Serial.println();
    Serial.println("========== NTP INITIALIZATION ==========");

    configTime(
        0,
        0,
        NTP_SERVER,
        SECONDARY_NTP_SERVER
    );

    Serial.println(
        "[NTP] Waiting for time synchronization..."
    );

    uint32_t startTime = millis();

    while (
        std::time(nullptr) < MIN_VALID_TIMESTAMP &&
        millis() - startTime < 10000
    ) {

        handlePowerButton();

        statusLed.update(false);

        pumpController.update();

        delay(100);
    }

    if (std::time(nullptr) >= MIN_VALID_TIMESTAMP) {

        Serial.println(
            "[NTP] Time synchronized successfully."
        );
    }
    else {

        Serial.println(
            "[NTP] Time synchronization failed!"
        );

        Serial.println(
            "[NTP] Commands will be rejected until time is valid."
        );
    }
}


// =====================================================
// MQTT INITIALIZATION
// =====================================================

void initializeMQTT() {

    const DeviceConfig& config = configManager.get();

    Serial.println();
    Serial.println("========== MQTT INITIALIZATION ==========");

    // ---------------------------------------------
    // 1. Capture command callback.
    // ---------------------------------------------

    mqttClient.setCaptureCommandCallback([]() {

        Serial.println(
            "[COMMAND] Capture request received from Edge!"
        );

        captureCommandPending = true;
    });

    // ---------------------------------------------
    // 2. Pump command callback.
    // ---------------------------------------------

    mqttClient.setPumpCommandCallback([](uint32_t durationMs) {

        Serial.println();
        Serial.println("[COMMAND] Starting water pump...");

        return pumpController.start(durationMs);
    });

    // ---------------------------------------------
    // 3. Initialize MQTT.
    // ---------------------------------------------

    mqttClient.begin(
        config.edgeId,
        deviceIdentity.getNodeId(),
        config.mqttHost,
        config.mqttPort,
        config.mqttUsername,
        config.mqttPassword
    );

    buildAndPrintTopics();
}


// =====================================================
// SETUP
// =====================================================

void setup() {

    Serial.begin(115200);

    // ---------------------------------------------
    // 1. Initialize power button and status LED.
    // ---------------------------------------------

    powerButton.begin();

    statusLed.begin();

    // Initialize pump controller.
    pumpController.begin();

    delay(1000);

    Serial.println();
    Serial.println("======================================");
    Serial.println("       ESP32 MAIN CONTROLLER");
    Serial.println("======================================");

    // ---------------------------------------------
    // 2. Initialize camera UART.
    // ---------------------------------------------

    Serial.println();
    Serial.println(
        "========== CAMERA UART INITIALIZATION =========="
    );

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
    // 3. Initialize device identity.
    // ---------------------------------------------

    Serial.println();
    Serial.println("========== DEVICE IDENTITY ==========");

    deviceIdentity.begin();

    Serial.print("[DEVICE] Node ID: ");
    Serial.println(deviceIdentity.getNodeId());

    // ---------------------------------------------
    // 4. Load configuration from NVS.
    // ---------------------------------------------

    Serial.println();
    Serial.println(
        "========== CONFIGURATION INITIALIZATION =========="
    );

    if (!configManager.begin()) {

        Serial.println(
            "[CONFIG] Failed to load configuration!"
        );

        Serial.println(
            "[CONFIG] Device will use default configuration."
        );
    }

    const DeviceConfig& config = configManager.get();

    Serial.println("[CONFIG] Runtime configuration:");

    Serial.print("[CONFIG] WiFi SSID: ");
    Serial.println(config.wifiSSID);

    Serial.print("[CONFIG] Edge ID: ");
    Serial.println(config.edgeId);

    Serial.print("[CONFIG] MQTT host: ");
    Serial.println(config.mqttHost);

    Serial.print("[CONFIG] MQTT port: ");
    Serial.println(config.mqttPort);

    Serial.print("[CONFIG] Telemetry interval: ");
    Serial.print(config.telemetryIntervalMs);
    Serial.println(" ms");

    // ---------------------------------------------
    // 5. Apply pump PWM configuration.
    // ---------------------------------------------

    pumpController.setPwmPercent(
        config.pumpPwmPercent
    );

    Serial.print("[CONFIG] Pump PWM applied: ");
    Serial.print(pumpController.getPwmPercent());
    Serial.println("%");

    // ---------------------------------------------
    // 6. Initialize configuration portal.
    // ---------------------------------------------

    Serial.println();
    Serial.println(
        "========== CONFIGURATION PORTAL =========="
    );

    configPortal.begin(&configManager);

    Serial.println("[PORTAL] Ready!");

    Serial.println(
        "[PORTAL] Hold GPIO25 for 5 seconds to open AP."
    );

    // ---------------------------------------------
    // 7. Initialize sensors.
    // ---------------------------------------------

    Serial.println();
    Serial.println("========== SENSOR INITIALIZATION ==========");

    sensorManager.begin();

    // ---------------------------------------------
    // 8. Connect Wi-Fi.
    // ---------------------------------------------

    bool wifiConnected = connectWiFi();

    // ---------------------------------------------
    // 9. Initialize NTP.
    // ---------------------------------------------

    if (wifiConnected) {
        initializeNTP();
    }

    // ---------------------------------------------
    // 10. Initialize MQTT.
    // ---------------------------------------------

    initializeMQTT();

    // ---------------------------------------------
    // 11. Initialize telemetry scheduler.
    // ---------------------------------------------

    lastTelemetryTime = millis();

    Serial.println();
    Serial.println("========== SYSTEM READY ==========");

    Serial.print("[SYSTEM] Pump PWM: ");
    Serial.print(pumpController.getPwmPercent());
    Serial.println("%");

    if (!wifiConnected) {

        Serial.println(
            "[SYSTEM] WiFi unavailable. Configuration portal "
            "can be opened using GPIO25."
        );
    }
}


// =====================================================
// LOOP
// =====================================================

void loop() {

    // ---------------------------------------------
    // 1. Handle power button.
    // ---------------------------------------------

    handlePowerButton();

    // ---------------------------------------------
    // 2. Update pump controller.
    // ---------------------------------------------

    pumpController.update();

    // ---------------------------------------------
    // 3. Update connection status LED.
    // ---------------------------------------------

    statusLed.update(mqttClient.isConnected());

    // ---------------------------------------------
    // 4. Handle configuration portal.
    // ---------------------------------------------

    configPortal.loop();

    // ---------------------------------------------
    // 5. Maintain MQTT connection.
    // ---------------------------------------------

    mqttClient.loop();

    statusLed.update(mqttClient.isConnected());

    // ---------------------------------------------
    // 6. Handle pending Edge capture command.
    // ---------------------------------------------

    handleCaptureCommand();

    // ---------------------------------------------
    // 7. Receive camera data continuously.
    // ---------------------------------------------

    cameraClient.loop();

    // ---------------------------------------------
    // 8. Process camera response.
    // ---------------------------------------------

    handleCameraResponse();

    // ---------------------------------------------
    // 9. Wait for MQTT connection.
    // ---------------------------------------------

    if (!mqttClient.isConnected()) {

        delay(1);

        return;
    }

    // ---------------------------------------------
    // 10. Read sensors and publish telemetry.
    // ---------------------------------------------

    handleTelemetry();
}
