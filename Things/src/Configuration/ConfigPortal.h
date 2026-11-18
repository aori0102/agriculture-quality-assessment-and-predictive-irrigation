
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

    // Called by PowerButton when the user holds the button for 5 seconds.
    void open();

private:
    static constexpr uint16_t DNS_PORT = 53;
    static constexpr uint16_t HTTP_PORT = 80;

    DeviceConfigManager* m_configManager = nullptr;

    WebServer m_server{HTTP_PORT};
    DNSServer m_dnsServer;

    bool m_isActive = false;

    void startPortal();
    void stopPortal();

    void configureRoutes();

    void handleRoot();
    void handleSave();
    void handleNotFound();

    String buildHtml() const;
    String getNodeId() const;
};
