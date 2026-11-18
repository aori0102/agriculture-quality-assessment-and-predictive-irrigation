#include "StatusLed.h"

void StatusLed::begin() {
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    m_patternStartTime = millis();
}

void StatusLed::update(bool mqttConnected) {
    ConnectionState newState;

    // Check Wi-Fi connection first.
    if (WiFi.status() != WL_CONNECTED) {
        newState = ConnectionState::WIFI_DISCONNECTED;
    }
    // Check MQTT connection.
    else if (!mqttConnected) {
        newState = ConnectionState::MQTT_DISCONNECTED;
    }
    // All required connections are ready.
    else {
        newState = ConnectionState::CONNECTED;
    }

    // Reset pattern when connection state changes.
    if (newState != m_currentState) {
        m_currentState = newState;
        m_patternStartTime = millis();
    }

    switch (m_currentState) {
        case ConnectionState::WIFI_DISCONNECTED:
            updatePattern(1);
            break;

        case ConnectionState::MQTT_DISCONNECTED:
            updatePattern(2);
            break;

        case ConnectionState::CONNECTED:
            digitalWrite(LED_PIN, HIGH);
            break;
    }
}

void StatusLed::updatePattern(uint8_t blinkCount) {
    const uint32_t now = millis();

    const uint32_t singleBlinkDuration =
        BLINK_ON_MS + BLINK_OFF_MS;

    const uint32_t cycleDuration =
        blinkCount * singleBlinkDuration + PAUSE_MS;

    const uint32_t cyclePosition =
        (now - m_patternStartTime) % cycleDuration;

    bool ledOn = false;

    for (uint8_t i = 0; i < blinkCount; i++) {
        const uint32_t blinkStart =
            i * singleBlinkDuration;

        if (cyclePosition >= blinkStart &&
            cyclePosition < blinkStart + BLINK_ON_MS) {
            ledOn = true;
            break;
        }
    }

    digitalWrite(LED_PIN, ledOn ? HIGH : LOW);
}