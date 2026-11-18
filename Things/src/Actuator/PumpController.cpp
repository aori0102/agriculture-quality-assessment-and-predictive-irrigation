
#include <Arduino.h>
#include "PumpController.h"

PumpController::PumpController(
    uint8_t enablePin,
    uint8_t input1Pin,
    uint8_t input2Pin
) {
    m_enablePin = enablePin;
    m_input1Pin = input1Pin;
    m_input2Pin = input2Pin;
}

void PumpController::begin() {
    // Set safe output values before enabling output mode.
    digitalWrite(m_enablePin, LOW);
    digitalWrite(m_input1Pin, LOW);
    digitalWrite(m_input2Pin, LOW);

    pinMode(m_enablePin, OUTPUT);
    pinMode(m_input1Pin, OUTPUT);
    pinMode(m_input2Pin, OUTPUT);

    turnMotorOff();

    Serial.println("[PUMP] Controller initialized.");
    Serial.println("[PUMP] Initial state: OFF");
}

bool PumpController::start(uint32_t durationMs) {
    if (durationMs == 0 || durationMs > MAX_DURATION_MS) {
        Serial.println("[PUMP] Rejected: invalid duration.");

        return false;
    }

    if (m_isRunning) {
        Serial.println("[PUMP] Rejected: pump is already running.");

        return false;
    }

    m_durationMs = durationMs;
    m_startTime = millis();

    turnMotorOn();

    m_isRunning = true;

    Serial.println();
    Serial.println("========== PUMP STARTED ==========");

    Serial.print("[PUMP] Duration: ");
    Serial.print(m_durationMs);
    Serial.println(" ms");

    Serial.println("==================================");

    return true;
}

void PumpController::stop() {
    if (!m_isRunning) {
        turnMotorOff();
        return;
    }

    turnMotorOff();

    m_isRunning = false;
    m_durationMs = 0;
    m_startTime = 0;

    Serial.println();
    Serial.println("[PUMP] Pump stopped.");
}

void PumpController::update() {
    if (!m_isRunning) {
        return;
    }

    uint32_t elapsedTime = millis() - m_startTime;

    if (elapsedTime >= m_durationMs) {
        Serial.println("[PUMP] Duration completed.");

        stop();
    }
}

bool PumpController::isRunning() const {
    return m_isRunning;
}

uint32_t PumpController::getRemainingTime() const {
    if (!m_isRunning) {
        return 0;
    }

    uint32_t elapsedTime = millis() - m_startTime;

    if (elapsedTime >= m_durationMs) {
        return 0;
    }

    return m_durationMs - elapsedTime;
}

void PumpController::turnMotorOn() {
    // Set pump direction.
    digitalWrite(m_input1Pin, HIGH);
    digitalWrite(m_input2Pin, LOW);

    // Enable L298N channel A at full speed.
    digitalWrite(m_enablePin, HIGH);
}

void PumpController::turnMotorOff() {
    // Disable motor driver first.
    digitalWrite(m_enablePin, LOW);

    digitalWrite(m_input1Pin, LOW);
    digitalWrite(m_input2Pin, LOW);
}
