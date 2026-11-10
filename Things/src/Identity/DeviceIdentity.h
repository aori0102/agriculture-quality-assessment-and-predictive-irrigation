#pragma once

#include <stdint.h>

class DeviceIdentity {
public:
    DeviceIdentity() = default;

    void begin();

    const char* getNodeId() const;

    const char* getBootId() const;

private:
    void generateNodeId();
    void generateBootId();

    char m_nodeId[13] = {};
    char m_bootId[37] = {};

    bool m_initialized = false;
};