
#pragma once

#include <Arduino.h>

class PumpController {
public:
    // Maximum allowed pump duration: 60 seconds.
    static constexpr uint32_t MAX_DURATION_MS = 60000;

    PumpController(
        uint8_t enablePin,
        uint8_t input1Pin,
        uint8_t input2Pin
    );

    void begin();

    // Start pump for the specified duration.
    // Returns false if duration is invalid or pump is already running.
    bool start(uint32_t durationMs);

    // Stop pump immediately.
    void stop();

    // Must be called continuously inside loop().
    void update();

    bool isRunning() const;

    uint32_t getRemainingTime() const;

private:
    uint8_t m_enablePin;
    uint8_t m_input1Pin;
    uint8_t m_input2Pin;

    bool m_isRunning = false;

    uint32_t m_startTime = 0;
    uint32_t m_durationMs = 0;

    void turnMotorOn();
    void turnMotorOff();
};
