
#include "ConfigPortal.h"

#include <esp_system.h>

namespace {
    constexpr char AP_PASSWORD[] = "";
    constexpr char AP_PREFIX[] = "";
}

void ConfigPortal::begin(DeviceConfigManager* configManager) {
    m_configManager = configManager;

    pinMode(BUTTON_PIN, INPUT_PULLUP);

    Serial.println("[PORTAL] Button initialized on GPIO25");
}

void ConfigPortal::loop() {
    handleButton();

    if (!m_isActive) {
        return;
    }

    m_dnsServer.processNextRequest();
    m_server.handleClient();
}

bool ConfigPortal::isActive() const {
    return m_isActive;
}

void ConfigPortal::handleButton() {
    const bool pressed = digitalRead(BUTTON_PIN) == LOW;
    const uint32_t currentTime = millis();

    if (pressed && !m_buttonPressed) {
        m_buttonPressed = true;
        m_longPressTriggered = false;
        m_buttonPressTime = currentTime;
    }

    if (!pressed && m_buttonPressed) {
        m_buttonPressed = false;
        m_longPressTriggered = false;
    }

    if (
        pressed &&
        m_buttonPressed &&
        !m_longPressTriggered &&
        currentTime - m_buttonPressTime >= LONG_PRESS_MS
    ) {
        m_longPressTriggered = true;

        Serial.println("[PORTAL] Long press detected!");

        if (!m_isActive) {
            startPortal();
        }
    }
}

void ConfigPortal::startPortal() {
    if (m_isActive) {
        return;
    }

    Serial.println();
    Serial.println("========== CONFIGURATION MODE ==========");

    WiFi.mode(WIFI_AP_STA);

    const uint64_t mac = ESP.getEfuseMac();

    char suffix[5];

    snprintf(
        suffix,
        sizeof(suffix),
        "%04X",
        static_cast<uint16_t>(mac & 0xFFFF)
    );

    String apSSID = String(AP_PREFIX) + suffix;

    if (!WiFi.softAP(apSSID.c_str(), AP_PASSWORD)) {
        Serial.println("[PORTAL] Failed to start AP!");
        return;
    }

    IPAddress apIP = WiFi.softAPIP();

    Serial.print("[PORTAL] AP SSID: ");
    Serial.println(apSSID);

    Serial.print("[PORTAL] AP IP: ");
    Serial.println(apIP);

    m_dnsServer.start(
        DNS_PORT,
        "*",
        apIP
    );

    configureRoutes();

    m_server.begin();

    m_isActive = true;

    Serial.println("[PORTAL] DNS server started!");
    Serial.println("[PORTAL] HTTP server started!");
    Serial.println("[PORTAL] Configuration mode is ready!");
}

void ConfigPortal::stopPortal() {
    if (!m_isActive) {
        return;
    }

    Serial.println("[PORTAL] Stopping configuration portal...");

    m_server.stop();
    m_dnsServer.stop();

    WiFi.softAPdisconnect(true);

    m_isActive = false;

    Serial.println("[PORTAL] AP stopped!");
}

void ConfigPortal::configureRoutes() {
    m_server.on("/", HTTP_GET, [this]() {
        handleRoot();
    });

    m_server.on("/save", HTTP_POST, [this]() {
        handleSave();
    });

    // Captive portal detection endpoints.
    m_server.on("/generate_204", HTTP_GET, [this]() {
        handleRoot();
    });

    m_server.on("/hotspot-detect.html", HTTP_GET, [this]() {
        handleRoot();
    });

    m_server.on("/connecttest.txt", HTTP_GET, [this]() {
        handleRoot();
    });

    m_server.on("/ncsi.txt", HTTP_GET, [this]() {
        handleRoot();
    });

    m_server.onNotFound([this]() {
        handleNotFound();
    });
}

void ConfigPortal::handleRoot() {
    m_server.send(
        200,
        "text/html; charset=utf-8",
        buildHtml()
    );
}

void ConfigPortal::handleNotFound() {
    m_server.sendHeader(
        "Location",
        String("http://") + WiFi.softAPIP().toString() + "/",
        true
    );

    m_server.send(302, "text/plain", "");
}

void ConfigPortal::handleSave() {
    DeviceConfig config = m_configManager->get();

    if (m_server.hasArg("wifi_ssid")) {
        config.wifiSSID = m_server.arg("wifi_ssid");
    }

    if (m_server.hasArg("wifi_password")) {
        config.wifiPassword = m_server.arg("wifi_password");
    }

    if (m_server.hasArg("site_id")) {
        config.siteId = m_server.arg("site_id");
    }

    if (m_server.hasArg("zone_id")) {
        config.zoneId = m_server.arg("zone_id");
    }

    if (m_server.hasArg("edge_id")) {
        config.edgeId = m_server.arg("edge_id");
    }

    if (m_server.hasArg("mqtt_host")) {
        config.mqttHost = m_server.arg("mqtt_host");
    }

    if (m_server.hasArg("mqtt_port")) {
        long port = m_server.arg("mqtt_port").toInt();

        if (port < 1 || port > 65535) {
            m_server.send(
                400,
                "text/plain",
                "Invalid MQTT port!"
            );
            return;
        }

        config.mqttPort = static_cast<uint16_t>(port);
    }

    if (m_server.hasArg("mqtt_username")) {
        config.mqttUsername = m_server.arg("mqtt_username");
    }

    if (m_server.hasArg("mqtt_password")) {
        config.mqttPassword = m_server.arg("mqtt_password");
    }

    if (m_server.hasArg("telemetry_interval")) {
        uint32_t interval = strtoul(
            m_server.arg("telemetry_interval").c_str(),
            nullptr,
            10
        );

        if (interval < 1000 || interval > 3600000) {
            m_server.send(
                400,
                "text/plain",
                "Telemetry interval must be 1000-3600000 ms!"
            );
            return;
        }

        config.telemetryIntervalMs = interval;
    }

    if (
        config.wifiSSID.isEmpty() ||
        config.mqttHost.isEmpty() ||
        config.siteId.isEmpty() ||
        config.edgeId.isEmpty()
    ) {
        m_server.send(
            400,
            "text/plain",
            "Required fields cannot be empty!"
        );
        return;
    }

    if (!m_configManager->save(config)) {
        m_server.send(
            500,
            "text/plain",
            "Failed to save configuration!"
        );
        return;
    }

    m_server.send(
        200,
        "text/html; charset=utf-8",
        "<!DOCTYPE html>"
        "<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Saved</title></head>"
        "<body style='font-family:Arial;text-align:center;padding:40px'>"
        "<h2>Configuration saved successfully!</h2>"
        "<p>The device is restarting...</p>"
        "</body></html>"
    );

    Serial.println("[PORTAL] Configuration saved. Restarting...");

    delay(1000);

    stopPortal();

    delay(500);

    ESP.restart();
}

String ConfigPortal::getNodeId() const {
    const uint64_t mac = ESP.getEfuseMac();

    char nodeId[20];

    snprintf(
        nodeId,
        sizeof(nodeId),
        "%llu",
        static_cast<unsigned long long>(mac)
    );

    return String(nodeId);
}


String ConfigPortal::buildHtml() const {
    const DeviceConfig& config = m_configManager->get();

    auto escapeHtml = [](const String& value) {
        String result;

        for (size_t i = 0; i < value.length(); i++) {
            switch (value[i]) {
                case '&': result += "&amp;"; break;
                case '<': result += "&lt;"; break;
                case '>': result += "&gt;"; break;
                case '"': result += "&quot;"; break;
                case '\'': result += "&#39;"; break;
                default: result += value[i]; break;
            }
        }

        return result;
    };

    String html = R"rawliteral(
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <title>AIoT Device Configuration</title>

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            padding: 20px;
            background: #f1f5f9;
            font-family: Arial, sans-serif;
            color: #1e293b;
        }

        .container {
            max-width: 650px;
            margin: auto;
        }

        .header {
            text-align: center;
            margin-bottom: 25px;
        }

        .header h1 {
            margin-bottom: 8px;
            color: #0f172a;
        }

        .header p {
            color: #64748b;
        }

        .card {
            background: white;
            padding: 22px;
            margin-bottom: 18px;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }

        .card h2 {
            font-size: 18px;
            margin-top: 0;
            margin-bottom: 18px;
            color: #2563eb;
        }

        label {
            display: block;
            font-size: 14px;
            font-weight: bold;
            margin-bottom: 7px;
        }

        input {
            width: 100%;
            padding: 12px;
            margin-bottom: 16px;
            border: 1px solid #cbd5e1;
            border-radius: 7px;
            font-size: 15px;
        }

        input:focus {
            outline: none;
            border-color: #2563eb;
        }

        input[readonly] {
            background: #e2e8f0;
            color: #475569;
        }

        button {
            width: 100%;
            padding: 15px;
            border: none;
            border-radius: 8px;
            background: #2563eb;
            color: white;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
        }

        button:active {
            background: #1d4ed8;
        }

        .note {
            font-size: 12px;
            color: #64748b;
            margin-top: -10px;
            margin-bottom: 16px;
        }
    </style>
</head>

<body>
<div class="container">

    <div class="header">
        <h1>AIoT Device Setup</h1>
        <p>Configure your device connection and system settings</p>
    </div>

    <form action="/save" method="POST">

        <div class="card">
            <h2>Device Information</h2>

            <label>Node ID</label>
            <input type="text" value=")rawliteral";

    html += escapeHtml(getNodeId());

    html += R"rawliteral(" readonly>

            <label>Site ID</label>
            <input type="text" name="site_id" value=")rawliteral";

    html += escapeHtml(config.siteId);

    html += R"rawliteral(" required>

            <label>Zone ID</label>
            <input type="text" name="zone_id" value=")rawliteral";

    html += escapeHtml(config.zoneId);

    html += R"rawliteral(" required>

            <label>Edge ID</label>
            <input type="text" name="edge_id" value=")rawliteral";

    html += escapeHtml(config.edgeId);

    html += R"rawliteral(" required>
        </div>

        <div class="card">
            <h2>Wi-Fi Configuration</h2>

            <label>Wi-Fi SSID</label>
            <input type="text" name="wifi_ssid" value=")rawliteral";

    html += escapeHtml(config.wifiSSID);

    html += R"rawliteral(" required>

            <label>Wi-Fi Password</label>
            <input type="password" name="wifi_password" value=")rawliteral";

    html += escapeHtml(config.wifiPassword);

    html += R"rawliteral(">
        </div>

        <div class="card">
            <h2>MQTT Configuration</h2>

            <label>MQTT Broker</label>
            <input type="text" name="mqtt_host" value=")rawliteral";

    html += escapeHtml(config.mqttHost);

    html += R"rawliteral(" required>

            <label>MQTT Port</label>
            <input type="number" name="mqtt_port" min="1" max="65535" value=")rawliteral";

    html += String(config.mqttPort);

    html += R"rawliteral(" required>

            <label>MQTT Username</label>
            <input type="text" name="mqtt_username" value=")rawliteral";

    html += escapeHtml(config.mqttUsername);

    html += R"rawliteral(">

            <label>MQTT Password</label>
            <input type="password" name="mqtt_password" value=")rawliteral";

    html += escapeHtml(config.mqttPassword);

    html += R"rawliteral(">
        </div>

        <div class="card">
            <h2>System Configuration</h2>

            <label>Telemetry Interval (ms)</label>
            <input
                type="number"
                name="telemetry_interval"
                min="1000"
                max="3600000"
                value=")rawliteral";

    html += String(config.telemetryIntervalMs);

    html += R"rawliteral("
                required
            >

            <p class="note">
                Allowed range: 1000 - 3600000 milliseconds
            </p>
        </div>

        <button type="submit">
            Save Configuration
        </button>

    </form>
</div>
</body>
</html>
)rawliteral";

    return html;
}
