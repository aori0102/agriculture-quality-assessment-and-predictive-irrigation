#include "PowerButton.h"

#include <WiFi.h>
#include <esp_sleep.h>

// =====================================================
// INITIALIZATION
// =====================================================

void PowerButton::begin() {
    pinMode(BUTTON_PIN, INPUT_PULLUP);

    m_rawState = digitalRead(BUTTON_PIN);
    m_stableState = m_rawState;

    m_lastDebounceTime = millis();

    Serial.println("[POWER] Button initialized on GPIO25");
}

// =====================================================
// BUTTON STATE
// =====================================================

bool PowerButton::isPressed() const {
    return m_stableState == LOW;
}

// =====================================================
// BUTTON UPDATE
// =====================================================

PowerButton::Action PowerButton::update() {
    const uint32_t now = millis();

    const bool reading = digitalRead(BUTTON_PIN);

    // ---------------------------------------------
    // 1. Debounce.
    // ---------------------------------------------

    if (reading != m_rawState) {
        m_rawState = reading;
        m_lastDebounceTime = now;
    }

    if (
        reading != m_stableState &&
        now - m_lastDebounceTime >= DEBOUNCE_MS
    ) {
        m_stableState = reading;

        // Button pressed.
        if (isPressed()) {

            // Check whether this is the second click.
            if (
                m_waitingSecondClick &&
                now - m_firstClickTime <= DOUBLE_CLICK_MS
            ) {
                m_secondPress = true;
                m_waitingSecondClick = false;

                Serial.println("[POWER] Double click detected!");
            }
            else {
                m_secondPress = false;
                m_waitingSecondClick = false;
            }

            m_pressStartTime = now;
            m_longPressTriggered = false;
        }

        // Button released.
        else {
            const uint32_t holdDuration =
                now - m_pressStartTime;

            if (m_longPressTriggered) {
                return Action::NONE;
            }

            // Second click followed by a hold.
            if (m_secondPress) {
                m_secondPress = false;

                if (
                    holdDuration >= HOLD_ACTION_MS &&
                    holdDuration < LONG_PRESS_MS
                ) {
                    Serial.println("[POWER] Deep sleep requested!");

                    return Action::DEEP_SLEEP;
                }
            }

            // Single click followed by a hold.
            else if (
                holdDuration >= HOLD_ACTION_MS &&
                holdDuration < LONG_PRESS_MS
            ) {
                Serial.println("[POWER] Restart requested!");

                return Action::RESTART;
            }

            // Short first click: wait for a possible second click.
            else if (holdDuration < HOLD_ACTION_MS) {
                m_waitingSecondClick = true;
                m_firstClickTime = now;
            }
        }
    }

    // ---------------------------------------------
    // 2. Detect long press while button is held.
    // ---------------------------------------------

    if (
        isPressed() &&
        !m_longPressTriggered &&
        now - m_pressStartTime >= LONG_PRESS_MS
    ) {
        m_longPressTriggered = true;
        m_waitingSecondClick = false;
        m_secondPress = false;

        Serial.println("[POWER] Configuration mode requested!");

        return Action::OPEN_CONFIG;
    }

    // ---------------------------------------------
    // 3. Expire double-click window.
    // ---------------------------------------------

    if (
        m_waitingSecondClick &&
        now - m_firstClickTime > DOUBLE_CLICK_MS
    ) {
        m_waitingSecondClick = false;
    }

    return Action::NONE;
}

// =====================================================
// ENTER DEEP SLEEP
// =====================================================

void PowerButton::enterDeepSleep() {
    Serial.println();
    Serial.println("========== POWER OFF ==========");

    Serial.println("[POWER] Disconnecting WiFi...");

    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);

    Serial.println("[POWER] Configuring GPIO25 wake-up...");

    // Wake ESP32 when GPIO25 is pulled LOW.
    esp_err_t result = esp_sleep_enable_ext0_wakeup(
        GPIO_NUM_25,
        0
    );

    if (result != ESP_OK) {
        Serial.printf(
            "[POWER] Failed to configure wake-up: %d\n",
            result
        );

        return;
    }

    Serial.println("[POWER] Entering deep sleep...");
    Serial.flush();

    esp_deep_sleep_start();
}