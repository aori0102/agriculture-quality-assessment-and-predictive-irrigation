
#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>

#include "DeviceConfig.h"

class ConfigPortal {
public:
    void begin(DeviceConfigManager* configManager);
    void loop();

    bool isActive() const;

private:
    static constexpr uint8_t BUTTON_PIN = 25;
    static constexpr uint32_t LONG_PRESS_MS = 5000;

    static constexpr uint16_t DNS_PORT = 53;
    static constexpr uint16_t HTTP_PORT = 80;

    DeviceConfigManager* m_configManager = nullptr;

    WebServer m_server{HTTP_PORT};
    DNSServer m_dnsServer;

    bool m_isActive = false;
    bool m_buttonPressed = false;
    bool m_longPressTriggered = false;

    uint32_t m_buttonPressTime = 0;

    void handleButton();

    void startPortal();
    void stopPortal();

    void configureRoutes();

    void handleRoot();
    void handleSave();
    void handleNotFound();

    String buildHtml() const;
    String getNodeId() const;
};
