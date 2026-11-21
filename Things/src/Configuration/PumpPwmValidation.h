#pragma once
// Strict validation before converting to uint8_t (reject wrapping and non-numbers).
constexpr bool pwmDigit(char c) { return c >= '0' && c <= '9'; }
constexpr bool validPumpPwm(const char* text) {
    return text && pwmDigit(text[0]) &&
        (!text[1] || (pwmDigit(text[1]) &&
         (!text[2] || (pwmDigit(text[2]) && !text[3] &&
          (text[0] - '0') * 100 + (text[1] - '0') * 10 + text[2] - '0' <= 255))));
}
static_assert(validPumpPwm("0") && validPumpPwm("200") && validPumpPwm("255"), "Accept PWM endpoints");
static_assert(!validPumpPwm("256") && !validPumpPwm("-1") && !validPumpPwm("1.5"), "Reject invalid range/types");
static_assert(!validPumpPwm("") && !validPumpPwm("abc") && !validPumpPwm("200x"), "Reject non-numeric input");
static_assert(!validPumpPwm("999999999999999999999999"), "Reject overflow");
