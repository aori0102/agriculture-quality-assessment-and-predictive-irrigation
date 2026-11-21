
#pragma once

#include <Arduino.h>
#include <functional>

class PumpController {
public:
    static constexpr uint32_t MAX_DURATION_MS = 60000;

    static constexpr uint32_t PWM_FREQUENCY = 5000;
    static constexpr uint8_t PWM_RESOLUTION = 8;
    static constexpr uint8_t DEFAULT_PWM_DUTY = 100;

    PumpController(
        uint8_t enablePin,
        uint8_t input1Pin,
        uint8_t input2Pin
    );

    void begin();

    bool start(uint32_t durationMs);

    void stop();

    void update();

    void setStateCallback(std::function<void(bool)> callback) { m_stateCallback = callback; }

    // Raw 8-bit PWM duty: 0..255.
    void setPwmDuty(uint8_t duty);

    uint8_t getPwmDutyValue() const;

    bool isRunning() const;

    uint32_t getRemainingTime() const;

private:
    std::function<void(bool)> m_stateCallback;
    uint8_t m_enablePin;
    uint8_t m_input1Pin;
    uint8_t m_input2Pin;

    uint8_t m_pwmDuty = DEFAULT_PWM_DUTY;

    bool m_isRunning = false;
    bool m_pwmInitialized = false;

    uint32_t m_startTime = 0;
    uint32_t m_durationMs = 0;

    void turnMotorOn();
    void turnMotorOff();

    uint32_t getPwmDuty() const;
    void writePwmDuty(uint32_t duty);
};
