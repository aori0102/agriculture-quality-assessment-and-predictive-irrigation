
#include <Arduino.h>
#include <esp_arduino_version.h>

#include "PumpController.h"

namespace {
    constexpr uint8_t PWM_CHANNEL = 0;
    constexpr uint32_t PWM_MAX_DUTY = 255;
}

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

    // Keep the motor disabled during initialization.
    digitalWrite(m_enablePin, LOW);
    digitalWrite(m_input1Pin, LOW);
    digitalWrite(m_input2Pin, LOW);

    pinMode(m_input1Pin, OUTPUT);
    pinMode(m_input2Pin, OUTPUT);

#if ESP_ARDUINO_VERSION_MAJOR >= 3

    m_pwmInitialized = ledcAttach(
        m_enablePin,
        PWM_FREQUENCY,
        PWM_RESOLUTION
    );

#else

    double actualFrequency = ledcSetup(
        PWM_CHANNEL,
        PWM_FREQUENCY,
        PWM_RESOLUTION
    );

    ledcAttachPin(m_enablePin, PWM_CHANNEL);

    m_pwmInitialized = actualFrequency > 0;

#endif

    if (!m_pwmInitialized) {
        Serial.println("[PUMP] PWM initialization failed!");
        return;
    }

    turnMotorOff();

    Serial.println("[PUMP] Controller initialized.");
    Serial.println("[PUMP] PWM frequency: 5000 Hz");
    Serial.println("[PUMP] PWM resolution: 8-bit");
    Serial.println("[PUMP] Initial state: OFF");
}

bool PumpController::start(uint32_t durationMs) {

    if (durationMs == 0 || durationMs > MAX_DURATION_MS) {
        Serial.println("[PUMP] Rejected: invalid duration.");
        return false;
    }

    if (!m_pwmInitialized) {
        Serial.println("[PUMP] Rejected: PWM is not initialized.");
        return false;
    }

    if (m_pwmPercent == 0) {
        Serial.println("[PUMP] Rejected: PWM is set to 0%.");
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

    Serial.print("[PUMP] PWM: ");
    Serial.print(m_pwmPercent);
    Serial.println("%");

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

void PumpController::setPwmPercent(uint8_t percent) {

    // Limit the value to the supported range.
    if (percent > 100) {
        percent = 100;
    }

    m_pwmPercent = percent;

    // Apply the new PWM immediately if the pump is running.
    if (m_isRunning) {

        writePwmDuty(getPwmDuty());
    }

    Serial.print("[PUMP] PWM configured: ");
    Serial.print(m_pwmPercent);
    Serial.println("%");
}

uint8_t PumpController::getPwmPercent() const {
    return m_pwmPercent;
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

uint32_t PumpController::getPwmDuty() const {

    return (
        static_cast<uint32_t>(m_pwmPercent) *
        PWM_MAX_DUTY
    ) / 100;
}

void PumpController::writePwmDuty(uint32_t duty) {

    if (!m_pwmInitialized) {
        return;
    }

#if ESP_ARDUINO_VERSION_MAJOR >= 3

    ledcWrite(m_enablePin, duty);

#else

    ledcWrite(PWM_CHANNEL, duty);

#endif
}

void PumpController::turnMotorOn() {

    if (!m_pwmInitialized || m_pwmPercent == 0) {
        return;
    }

    // Set pump direction.
    digitalWrite(m_input1Pin, HIGH);
    digitalWrite(m_input2Pin, LOW);

    // Enable motor using PWM.
    writePwmDuty(getPwmDuty());
}

void PumpController::turnMotorOff() {

    // Disable motor driver first.
    writePwmDuty(0);

    digitalWrite(m_input1Pin, LOW);
    digitalWrite(m_input2Pin, LOW);
}
