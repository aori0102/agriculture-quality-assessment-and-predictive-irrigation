#pragma once

#include <Arduino.h>

class PowerButton {
public:
    enum class Action {
        NONE,
        RESTART,
        DEEP_SLEEP,
        OPEN_CONFIG
    };

    void begin();
    Action update();

    void enterDeepSleep();

private:
    static constexpr uint8_t BUTTON_PIN = 25;

    static constexpr uint32_t DEBOUNCE_MS = 30;
    static constexpr uint32_t DOUBLE_CLICK_MS = 400;
    static constexpr uint32_t HOLD_ACTION_MS = 1000;
    static constexpr uint32_t LONG_PRESS_MS = 5000;

    bool m_rawState = HIGH;
    bool m_stableState = HIGH;

    bool m_waitingSecondClick = false;
    bool m_secondPress = false;
    bool m_longPressTriggered = false;

    uint32_t m_lastDebounceTime = 0;
    uint32_t m_pressStartTime = 0;
    uint32_t m_firstClickTime = 0;

    bool isPressed() const;
};