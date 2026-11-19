
#pragma once

#include <Arduino.h>

class PumpController {
public:
    static constexpr uint32_t MAX_DURATION_MS = 60000;

    static constexpr uint32_t PWM_FREQUENCY = 5000;
    static constexpr uint8_t PWM_RESOLUTION = 8;
    static constexpr uint8_t DEFAULT_PWM_PERCENT = 100;

    PumpController(
        uint8_t enablePin,
        uint8_t input1Pin,
        uint8_t input2Pin
    );

    void begin();

    bool start(uint32_t durationMs);

    void stop();

    void update();

    // Set pump speed from 0 to 100 percent.
    void setPwmPercent(uint8_t percent);

    uint8_t getPwmPercent() const;

    bool isRunning() const;

    uint32_t getRemainingTime() const;

private:
    uint8_t m_enablePin;
    uint8_t m_input1Pin;
    uint8_t m_input2Pin;

    uint8_t m_pwmPercent = DEFAULT_PWM_PERCENT;

    bool m_isRunning = false;
    bool m_pwmInitialized = false;

    uint32_t m_startTime = 0;
    uint32_t m_durationMs = 0;

    void turnMotorOn();
    void turnMotorOff();

    uint32_t getPwmDuty() const;
    void writePwmDuty(uint32_t duty);
};
