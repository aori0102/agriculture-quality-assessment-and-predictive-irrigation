
#include "ConfigPortal.h"

#include <Arduino.h>
#include <WiFi.h>
#include <esp_system.h>
#include "PumpPwmValidation.h"

// =====================================================
// ACCESS POINT CONFIGURATION
// =====================================================

// Empty password means the configuration AP is open.
constexpr char AP_PASSWORD[] = "";
constexpr uint16_t ConfigPortal::DNS_PORT;

// =====================================================
// HTML ESCAPE
// =====================================================

static String escapeHtml(const String& value) {
    String result;

    result.reserve(value.length());

    for (size_t i = 0; i < value.length(); i++) {
        switch (value[i]) {
            case '&':
                result += "&amp;";
                break;

            case '<':
                result += "&lt;";
                break;

            case '>':
                result += "&gt;";
                break;

            case '"':
                result += "&quot;";
                break;

            case '\'':
                result += "&#39;";
                break;

            default:
                result += value[i];
                break;
        }
    }

    return result;
}

// =====================================================
// BEGIN
// =====================================================

void ConfigPortal::begin(DeviceConfigManager* configManager) {

    m_configManager = configManager;

    Serial.println("[PORTAL] Configuration portal initialized!");
}

// =====================================================
// LOOP
// =====================================================

void ConfigPortal::loop() {

    if (!m_isActive) {
        return;
    }

    m_dnsServer.processNextRequest();

    m_server.handleClient();
}

// =====================================================
// IS ACTIVE
// =====================================================

bool ConfigPortal::isActive() const {

    return m_isActive;
}

// =====================================================
// OPEN PORTAL
// =====================================================

void ConfigPortal::open() {

    if (m_isActive) {
        Serial.println("[PORTAL] Portal is already active!");
        return;
    }

    if (m_configManager == nullptr) {
        Serial.println("[PORTAL] Config manager is not initialized!");
        return;
    }

    startPortal();
}

// =====================================================
// START PORTAL
// =====================================================

void ConfigPortal::startPortal() {

    if (m_isActive) {
        return;
    }

    Serial.println();
    Serial.println("========== START CONFIGURATION PORTAL ==========");

    // ---------------------------------------------
    // 1. Configure WiFi mode.
    // ---------------------------------------------

    WiFi.mode(WIFI_AP_STA);

    // ---------------------------------------------
    // 2. Generate AP SSID.
    // ---------------------------------------------

    String nodeId = getNodeId();

    String suffix = nodeId;

    if (suffix.length() > 4) {
        suffix = suffix.substring(suffix.length() - 4);
    }

    String apSSID = "ESP32-Config-" + suffix;

    Serial.print("[PORTAL] AP SSID: ");
    Serial.println(apSSID);

    // ---------------------------------------------
    // 3. Start Access Point.
    // ---------------------------------------------

    bool apStarted;

    if (strlen(AP_PASSWORD) == 0) {
        apStarted = WiFi.softAP(apSSID.c_str());
    }
    else {
        apStarted = WiFi.softAP(
            apSSID.c_str(),
            AP_PASSWORD
        );
    }

    if (!apStarted) {
        Serial.println("[PORTAL] Failed to start Access Point!");

        WiFi.mode(WIFI_STA);

        return;
    }

    // ---------------------------------------------
    // 4. Get AP IP address.
    // ---------------------------------------------

    IPAddress apIP = WiFi.softAPIP();

    Serial.print("[PORTAL] AP IP: ");
    Serial.println(apIP);

    // ---------------------------------------------
    // 5. Start captive DNS.
    // ---------------------------------------------

    if (!m_dnsServer.start(
        DNS_PORT,
        "*",
        apIP
    )) {
        Serial.println("[PORTAL] Failed to start DNS server!");

        WiFi.softAPdisconnect(true);
        WiFi.mode(WIFI_STA);

        return;
    }

    Serial.println("[PORTAL] DNS server started!");

    // ---------------------------------------------
    // 6. Configure HTTP routes.
    // ---------------------------------------------

    configureRoutes();

    // ---------------------------------------------
    // 7. Start HTTP server.
    // ---------------------------------------------

    m_server.begin();

    m_isActive = true;

    Serial.println("[PORTAL] HTTP server started!");

    Serial.println();
    Serial.println("[PORTAL] Configuration portal is ready!");
    Serial.print("[PORTAL] Connect to WiFi: ");
    Serial.println(apSSID);

    Serial.print("[PORTAL] Open browser: http://");
    Serial.println(apIP);

    Serial.println("================================================");
}

// =====================================================
// STOP PORTAL
// =====================================================

void ConfigPortal::close() {
    stopPortal();
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

    Serial.println("[PORTAL] Configuration portal stopped!");
}

// =====================================================
// CONFIGURE HTTP ROUTES
// =====================================================

void ConfigPortal::configureRoutes() {

    // ---------------------------------------------
    // Main configuration page.
    // ---------------------------------------------

    m_server.on(
        "/",
        HTTP_GET,
        [this]() {
            handleRoot();
        }
    );

    // ---------------------------------------------
    // Save configuration.
    // ---------------------------------------------

    m_server.on(
        "/save",
        HTTP_POST,
        [this]() {
            handleSave();
        }
    );

    // ---------------------------------------------
    // Common captive portal detection paths.
    // ---------------------------------------------

    m_server.on(
        "/generate_204",
        HTTP_GET,
        [this]() {
            handleRoot();
        }
    );

    m_server.on(
        "/hotspot-detect.html",
        HTTP_GET,
        [this]() {
            handleRoot();
        }
    );

    m_server.on(
        "/connecttest.txt",
        HTTP_GET,
        [this]() {
            handleRoot();
        }
    );

    m_server.on(
        "/ncsi.txt",
        HTTP_GET,
        [this]() {
            handleRoot();
        }
    );

    // ---------------------------------------------
    // Unknown routes.
    // ---------------------------------------------

    m_server.onNotFound(
        [this]() {
            handleNotFound();
        }
    );
}

// =====================================================
// HANDLE ROOT
// =====================================================

void ConfigPortal::handleRoot() {

    String html = buildHtml();

    m_server.send(
        200,
        "text/html",
        html
    );
}

// =====================================================
// HANDLE SAVE
// =====================================================

void ConfigPortal::handleSave() {

    Serial.println();
    Serial.println("[PORTAL] Received configuration update!");

    if (m_configManager == nullptr) {

        m_server.send(
            500,
            "text/plain",
            "Configuration manager is unavailable."
        );

        return;
    }

    // ---------------------------------------------
    // 1. Get current configuration.
    // ---------------------------------------------

    DeviceConfig updatedConfig = m_configManager->get();

    // ---------------------------------------------
    // 2. Read submitted form values.
    // ---------------------------------------------

    String wifiSSID = m_server.arg("wifiSSID");
    String wifiPassword = m_server.arg("wifiPassword");

    String edgeId = m_server.arg("edgeId");

    String mqttHost = m_server.arg("mqttHost");
    String mqttPort = m_server.arg("mqttPort");

    String mqttUsername = m_server.arg("mqttUsername");
    String mqttPassword = m_server.arg("mqttPassword");

    String telemetryInterval = m_server.arg("telemetryIntervalMs");
    String pumpPwm = m_server.arg("pumpPwmDuty");
    pumpPwm.trim();

    // ---------------------------------------------
    // 3. Validate required fields.
    // ---------------------------------------------

    wifiSSID.trim();
    edgeId.trim();
    mqttHost.trim();
    mqttUsername.trim();

    if (
        wifiSSID.isEmpty() ||
        edgeId.isEmpty() ||
        mqttHost.isEmpty() ||
        mqttPort.isEmpty() ||
        telemetryInterval.isEmpty()
    ) {

        m_server.send(
            400,
            "text/html",
            "<h2>Invalid configuration</h2>"
            "<p>Please fill in all required fields.</p>"
            "<a href='/'>Go back</a>"
        );

        return;
    }

    // ---------------------------------------------
    // 4. Validate MQTT port.
    // ---------------------------------------------

    long parsedPort = mqttPort.toInt();

    if (parsedPort < 1 || parsedPort > 65535) {

        m_server.send(
            400,
            "text/html",
            "<h2>Invalid MQTT port</h2>"
            "<a href='/'>Go back</a>"
        );

        return;
    }

    // ---------------------------------------------
    // 5. Validate telemetry interval.
    // ---------------------------------------------

    long parsedInterval = telemetryInterval.toInt();

    if (parsedInterval <= 0) {

        m_server.send(
            400,
            "text/html",
            "<h2>Invalid telemetry interval</h2>"
            "<a href='/'>Go back</a>"
        );

        return;
    }

    // ---------------------------------------------
    // 6. Update configuration.
    // ---------------------------------------------

    if (!validPumpPwm(pumpPwm.c_str())) {
        m_server.send(400, "text/plain", "Pump PWM must be an integer from 0 to 255.");
        return;
    }
    updatedConfig.pumpPwmDuty = static_cast<uint8_t>(pumpPwm.toInt());
    updatedConfig.wifiSSID = wifiSSID;

    // Empty password means keep the existing password.
    if (!wifiPassword.isEmpty()) {
        updatedConfig.wifiPassword = wifiPassword;
    }

    updatedConfig.edgeId = edgeId;

    updatedConfig.mqttHost = mqttHost;

    updatedConfig.mqttPort = static_cast<uint16_t>(parsedPort);

    updatedConfig.mqttUsername = mqttUsername;

    // Empty password means keep the existing password.
    if (!mqttPassword.isEmpty()) {
        updatedConfig.mqttPassword = mqttPassword;
    }

    updatedConfig.telemetryIntervalMs =
        static_cast<uint32_t>(parsedInterval);

    // ---------------------------------------------
    // 7. Save configuration to NVS.
    // ---------------------------------------------

    Serial.println("[PORTAL] Saving configuration...");

    bool saved = m_configManager->save(updatedConfig);

    if (!saved) {

        Serial.println("[PORTAL] Failed to save configuration!");

        m_server.send(
            500,
            "text/html",
            "<h2>Failed to save configuration!</h2>"
            "<p>Please try again.</p>"
            "<a href='/'>Go back</a>"
        );

        return;
    }

    Serial.println("[PORTAL] Configuration saved successfully!");

    // ---------------------------------------------
    // 8. Send success response.
    // ---------------------------------------------

    String response = R"rawliteral(
        <!DOCTYPE html>
        <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Configuration Saved</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background: #f3f4f6;
                    text-align: center;
                    padding: 40px 15px;
                }

                .container {
                    max-width: 420px;
                    margin: auto;
                    padding: 30px;
                    background: white;
                    border-radius: 12px;
                    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
                }

                h2 {
                    color: #16a34a;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h2>Configuration Saved!</h2>
                <p>Your device configuration has been saved successfully.</p>
                <p>ESP32 is restarting...</p>
            </div>
        </body>
        </html>
    )rawliteral";

    m_server.send(
        200,
        "text/html",
        response
    );

    Serial.println("[PORTAL] Restarting ESP32...");

    delay(1500);

    Serial.flush();

    ESP.restart();
}

// =====================================================
// HANDLE NOT FOUND
// =====================================================

void ConfigPortal::handleNotFound() {

    IPAddress apIP = WiFi.softAPIP();

    String redirectURL = "http://" + apIP.toString() + "/";

    m_server.sendHeader(
        "Location",
        redirectURL,
        true
    );

    m_server.send(
        302,
        "text/plain",
        ""
    );
}

// =====================================================
// BUILD HTML
// =====================================================

String ConfigPortal::buildHtml() const {

    if (m_configManager == nullptr) {
        return "<h2>Configuration manager is unavailable.</h2>";
    }

    const DeviceConfig& config = m_configManager->get();

    String html;

    html.reserve(6500);

    // ---------------------------------------------
    // HTML header.
    // ---------------------------------------------

    html += R"rawliteral(
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">

            <title>ESP32 Configuration</title>

            <style>
                * {
                    box-sizing: border-box;
                }

                body {
                    font-family: Arial, sans-serif;
                    background: #f3f4f6;
                    margin: 0;
                    padding: 20px;
                    color: #1f2937;
                }

                .container {
                    max-width: 520px;
                    margin: 20px auto;
                    background: white;
                    padding: 25px;
                    border-radius: 12px;
                    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
                }

                h1 {
                    text-align: center;
                    font-size: 24px;
                    margin-bottom: 8px;
                }

                .subtitle {
                    text-align: center;
                    color: #6b7280;
                    margin-bottom: 25px;
                    font-size: 14px;
                }

                h3 {
                    margin-top: 25px;
                    padding-bottom: 8px;
                    border-bottom: 1px solid #e5e7eb;
                }

                label {
                    display: block;
                    margin-top: 15px;
                    margin-bottom: 6px;
                    font-weight: bold;
                    font-size: 14px;
                }

                input {
                    width: 100%;
                    padding: 11px;
                    border: 1px solid #d1d5db;
                    border-radius: 7px;
                    font-size: 15px;
                }

                input:focus {
                    outline: none;
                    border-color: #2563eb;
                }

                .hint {
                    color: #6b7280;
                    font-size: 12px;
                    margin-top: 5px;
                }

                button {
                    width: 100%;
                    margin-top: 28px;
                    padding: 13px;
                    border: none;
                    border-radius: 7px;
                    background: #2563eb;
                    color: white;
                    font-size: 16px;
                    font-weight: bold;
                    cursor: pointer;
                }

                button:hover {
                    background: #1d4ed8;
                }

                .footer {
                    text-align: center;
                    margin-top: 20px;
                    color: #9ca3af;
                    font-size: 12px;
                }
            </style>
        </head>

        <body>
            <div class="container">

                <h1>ESP32 Configuration</h1>

                <p class="subtitle">
                    Configure your IoT device
                </p>

                <form action="/save" method="POST">

                    <h3>WiFi Configuration</h3>
    )rawliteral";

    // ---------------------------------------------
    // WiFi SSID.
    // ---------------------------------------------

    html += R"rawliteral(
                    <label for="wifiSSID">WiFi SSID</label>
                    <input
                        type="text"
                        id="wifiSSID"
                        name="wifiSSID"
                        required
                        maxlength="32"
                        value="
    )rawliteral";

    html += escapeHtml(config.wifiSSID);

    html += R"rawliteral(
                    ">
    )rawliteral";

    // ---------------------------------------------
    // WiFi password.
    // ---------------------------------------------

    html += R"rawliteral(
                    <label for="wifiPassword">WiFi Password</label>
                    <input
                        type="password"
                        id="wifiPassword"
                        name="wifiPassword"
                        placeholder="Leave blank to keep current password"
                        autocomplete="new-password"
                    >

                    <p class="hint">
                        Leave blank if you do not want to change the password.
                    </p>

                    <h3>Device Configuration</h3>
    )rawliteral";

    // ---------------------------------------------
    // Edge ID.
    // ---------------------------------------------

    html += R"rawliteral(
                    <label for="edgeId">Edge ID</label>
                    <input
                        type="text"
                        id="edgeId"
                        name="edgeId"
                        required
                        value="
    )rawliteral";

    html += escapeHtml(config.edgeId);

    html += R"rawliteral(
                    ">

                    <h3>MQTT Configuration</h3>
    )rawliteral";

    // ---------------------------------------------
    // MQTT host.
    // ---------------------------------------------

    html += R"rawliteral(
                    <label for="mqttHost">MQTT Host</label>
                    <input
                        type="text"
                        id="mqttHost"
                        name="mqttHost"
                        required
                        value="
    )rawliteral";

    html += escapeHtml(config.mqttHost);

    html += R"rawliteral(
                    ">
    )rawliteral";

    // ---------------------------------------------
    // MQTT port.
    // ---------------------------------------------

    html += R"rawliteral(
                    <label for="mqttPort">MQTT Port</label>
                    <input
                        type="number"
                        id="mqttPort"
                        name="mqttPort"
                        min="1"
                        max="65535"
                        required
                        value="
    )rawliteral";

    html += String(config.mqttPort);

    html += R"rawliteral(
                    ">
    )rawliteral";

    // ---------------------------------------------
    // MQTT username.
    // ---------------------------------------------

    html += R"rawliteral(
                    <label for="mqttUsername">MQTT Username</label>
                    <input
                        type="text"
                        id="mqttUsername"
                        name="mqttUsername"
                        value="
    )rawliteral";

    html += escapeHtml(config.mqttUsername);

    html += R"rawliteral(
                    ">
    )rawliteral";

    // ---------------------------------------------
    // MQTT password.
    // ---------------------------------------------

    html += R"rawliteral(
                    <label for="mqttPassword">MQTT Password</label>
                    <input
                        type="password"
                        id="mqttPassword"
                        name="mqttPassword"
                        placeholder="Leave blank to keep current password"
                        autocomplete="new-password"
                    >

                    <p class="hint">
                        Leave blank if you do not want to change the password.
                    </p>

                    <h3>Telemetry Configuration</h3>
    )rawliteral";

    // ---------------------------------------------
    // Telemetry interval.
    // ---------------------------------------------

    html += R"rawliteral(
                    <label for="telemetryIntervalMs">
                        Telemetry Interval (milliseconds)
                    </label>

                    <input
                        type="number"
                        id="telemetryIntervalMs"
                        name="telemetryIntervalMs"
                        min="1"
                        required
                        value="
    )rawliteral";

    html += String(config.telemetryIntervalMs);

    html += R"rawliteral(
                    ">

                    <h3>Pump Configuration</h3>
                    <label for="pumpPwmDuty">Pump PWM (0-255): <output id="pumpPwmValue" for="pumpPwmDuty">)rawliteral";
    html += String(config.pumpPwmDuty);
    html += R"rawliteral(</output></label>
                    <input type="range" id="pumpPwmDuty" name="pumpPwmDuty"
                           min="0" max="255" step="1" value=")rawliteral";
    html += String(config.pumpPwmDuty);
    html += R"rawliteral(" oninput="document.getElementById('pumpPwmValue').textContent=this.value">
                    <p class="hint">Default: 200 / 255. 0 disables pump starts. Save to apply after restart.</p>

                    <button type="submit">
                        Save Configuration
                    </button>

                </form>

                <div class="footer">
                    ESP32 IoT Configuration Portal
                </div>

            </div>
        </body>
        </html>
    )rawliteral";

    return html;
}

// =====================================================
// GET NODE ID
// =====================================================

String ConfigPortal::getNodeId() const {

    String macAddress = WiFi.macAddress();

    macAddress.replace(":", "");

    return macAddress;
}
