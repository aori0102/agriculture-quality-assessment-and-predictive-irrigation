#include "DeviceIdentity.h"

void DeviceIdentity::begin() {
    m_nodeId = ESP.getEfuseMac();
    m_nodeId.toLowerCase();
}

String DeviceIdentity::getNodeId() const {
    return m_nodeId;
}