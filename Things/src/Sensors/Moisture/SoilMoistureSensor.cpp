#include "SoilMoistureSensor.h"

SoilMoistureSensor::SoilMoistureSensor(
    int pin,
    int dryValue,
    int wetValue
) {
    m_pin = pin;
    m_dryValue = dryValue;
    m_wetValue = wetValue;
}

void SoilMoistureSensor::begin() {
    pinMode(m_pin, INPUT);

    analogReadResolution(12);
    analogSetPinAttenuation(
        m_pin,
        ADC_11db
    );
}

int SoilMoistureSensor::readRawValue() {
    const int SAMPLE_COUNT = 10;

    long total = 0;

    for (int i = 0; i < SAMPLE_COUNT; i++) {
        total += analogRead(m_pin);
    }

    return total / SAMPLE_COUNT;
}

SoilSensorData SoilMoistureSensor::read() {
    SoilSensorData result;

    if (m_dryValue == m_wetValue) {
        return result;
    }

    result.rawValue = readRawValue();

    if (result.rawValue < 0 || result.rawValue > 4095) {
        return result;
    }

    float moisture =
        (float)(m_dryValue - result.rawValue) /
        (m_dryValue - m_wetValue) * 100.0f;

    result.moisturePercent = constrain(
        moisture,
        0.0f,
        100.0f
    );

    result.isValid = true;

    return result;
}
