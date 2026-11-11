#pragma once

#include <Arduino.h>
#include <WiFi.h>

class DeviceIdentity {
public:
    void begin();

    String getNodeId() const;

private:
    String m_nodeId;
};