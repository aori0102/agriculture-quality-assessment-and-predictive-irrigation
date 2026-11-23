#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <functional>
#include <cmath>
#include <ctime>
#include "PumpController.h"

// Called only from the application loop, never from the MQTT task.
class NodeFallback {
    PumpController& pump;
    Preferences storage;
    JsonDocument state;
    String edge, node, boot, session;
    uint32_t lastHeartbeat = 0, lastSample = 0, lastStop = 0, hourStart = 0;
    uint32_t heartbeatSequence = 0, lowSamples = 0, startedAt = 0, lastAudit = 0;
    bool healthy = false, sampleValid = false, haveHeartbeat = false, previousRunning = false;
    bool heartbeatLossLogged = false;
    String runId, source;
    uint32_t requestedDuration = 0;
    float moisture = 0;
    uint32_t serial = 0;
    String debugId, debugLastId, debugRunId;
    uint32_t debugDelay = 0, debugSince = 0, debugStoppedAt = 0, debugActual = 0, debugLastAck = 0;
    bool debugStopped = false;

    void debugReply(const char* event) {
        if (!publish || debugId.isEmpty()) return;
        JsonDocument reply;
        reply["event"] = event; reply["edge_id"] = edge; reply["node_id"] = node;
        reply["session_id"] = session; reply["simulation_id"] = debugId;
        reply["pump_running"] = pump.isRunning(); reply["source"] = "local_fallback";
        reply["run_id"] = debugRunId; reply["actual_duration_ms"] = debugActual;
        String value; serializeJson(reply, value); publish(value);
    }

    bool save() {
        String value;
        serializeJson(state, value);
        healthy = storage.putBytes("state", value.c_str(), value.length()) == value.length();
        return healthy;
    }
    uint32_t setting(const char* name) const { return state["config"][name].as<uint32_t>(); }
    bool hasConfig() const { return state["config"].is<JsonObjectConst>(); }
    bool elapsed(uint32_t since, uint32_t duration) const { return uint32_t(millis() - since) >= duration; }
    void audit(const char* phase, uint32_t actual = 0) {
        JsonObject record = state["journal"].as<JsonArray>().add<JsonObject>();
        record["event"] = "fallback_audit";
        record["event_id"] = boot + "-" + String(++serial);
        record["edge_id"] = edge;
        record["node_id"] = node;
        record["run_id"] = runId;
        record["source"] = source;
        record["phase"] = phase;
        record["config_version"] = state["config"]["config_version"];
        record["soil_index_pct"] = moisture;
        record["requested_duration_ms"] = requestedDuration;
        record["actual_duration_ms"] = actual;
        record["boot_id"] = boot;
        record["uptime_ms"] = millis();
        time_t now = time(nullptr);
        if (now >= 1700000000) record["utc_epoch_seconds"] = int64_t(now);
        else record["utc_epoch_seconds"] = nullptr;
    }
public:
    std::function<bool(const String&)> publish;
    explicit NodeFallback(PumpController& value): pump(value) {}
    void begin(const String& edgeId, const String& nodeId) {
        edge = edgeId; node = nodeId;
        boot = String(esp_random(), HEX) + String(esp_random(), HEX);
        if (!storage.begin("node_fallback", false)) return;
        size_t length = storage.getBytesLength("state");
        if (length > 32768) return;
        if (length) {
            char* value = static_cast<char*>(malloc(length));
            if (!value) return;
            bool valid = storage.getBytes("state", value, length) == length && !deserializeJson(state, value, length);
            free(value);
            if (!valid) return;
        }
        if (!state["journal"].is<JsonArray>()) state["journal"].to<JsonArray>();
        // Never reuse another Edge/node identity's persisted actuation policy.
        if (hasConfig() && (state["config"]["edge_id"].as<String>() != edge || state["config"]["node_id"].as<String>() != node)) return;
        if (state["running"].as<bool>()) {
            runId = state["run_id"].as<String>(); source = "interrupted_run";
            audit("interrupted_unknown_duration");
            state["running"] = false;
        }
        // Restart preserves consumed budget; a new full hour must elapse.
        hourStart = lastStop = lastHeartbeat = millis();
        save();
    }
    void receive(const String& topic, const String& payload) {
        if (!healthy) return;
        JsonDocument d;
        if (deserializeJson(d, payload) || d["edge_id"].as<String>() != edge || d["node_id"].as<String>() != node) return;
        if (topic.endsWith("/debug")) {
            if (session.isEmpty() || d["session_id"].as<String>() != session) return;
            String id = d["simulation_id"].as<String>();
            if (id.length() != 32) return;
            if (d["action"] == "cancel") {
                if (id == debugId) { debugLastId = debugId; debugId = ""; debugStopped = false; }
                return; // Cancels debug tracking, never interrupts the pump timer.
            }
            if (d["action"] != "arm" || !d["expires_at_epoch"].is<int64_t>() ||
                    !d["after_pump_delay_ms"].is<uint32_t>()) return;
            int64_t now = int64_t(time(nullptr)), expires = d["expires_at_epoch"];
            if (now < 1700000000 || expires <= now || expires - now > 15 ||
                    d["after_pump_delay_ms"].as<uint32_t>() > 120000 || id == debugLastId) return;
            if (!debugId.isEmpty()) {
                if (id == debugId) debugReply("debug_simulation_armed");
                return;
            }
            debugId = id;
            if (!hasConfig() || !state["config"]["fallback_enabled"].as<bool>() ||
                    state["stop_locked"].as<bool>() || state["dry_used"].as<bool>() || pump.isRunning()) {
                debugReply("debug_simulation_rejected"); debugLastId = debugId; debugId = ""; return;
            }
            debugDelay = d["after_pump_delay_ms"]; debugSince = millis();
            debugStopped = false; debugRunId = ""; debugActual = 0;
            debugReply("debug_simulation_armed");
            return;
        }
        if (topic.endsWith("/fallback_ack")) {
            JsonArray entries = state["journal"].as<JsonArray>();
            if (entries.size() && entries[0]["event_id"].as<String>() == d["event_id"].as<String>()) {
                entries.remove(0); save();
            }
            return;
        }
        if (topic.endsWith("/heartbeat")) {
            if (session.isEmpty() || d["session_id"].as<String>() != session || !d["sequence"].is<uint32_t>()) return;
            uint32_t seq = d["sequence"];
            if (seq <= heartbeatSequence) return;
            heartbeatSequence = seq; lastHeartbeat = millis(); haveHeartbeat = true;
            heartbeatLossLogged = false;
            lowSamples = 0;
            return;
        }
        if (!topic.endsWith("/config")) return;
        auto reject = [&]() {
            JsonDocument reply; reply["event"] = "config_rejected";
            reply["edge_id"] = edge; reply["node_id"] = node;
            reply["config_version"] = d["config_version"]; reply["reason"] = "invalid_or_busy";
            String encoded; serializeJson(reply, encoded); if (publish) publish(encoded);
        };
        if (pump.isRunning()) { reject(); return; }
        const char* integers[] = {"schema_version", "config_version", "duration_ms", "cooldown_ms", "required_low_samples",
            "max_sample_age_ms", "edge_timeout_ms", "max_runs_per_hour", "max_total_duration_ms_per_hour", "unlock_generation"};
        for (auto key: integers) if (!d[key].is<uint32_t>()) { reject(); return; }
        const char* numbers[] = {"pwp_pct", "madp_pct", "fc_pct", "emergency_threshold_pct", "rearm_threshold_pct"};
        for (auto key: numbers) if (!d[key].is<float>() || !std::isfinite(d[key].as<float>())) { reject(); return; }
        float pwp = d["pwp_pct"], emergency = d["emergency_threshold_pct"], rearm = d["rearm_threshold_pct"], fc = d["fc_pct"], madp = d["madp_pct"];
        if (!(0 <= pwp && pwp < emergency && emergency < rearm && rearm <= fc && fc <= 100 && emergency < madp && madp < fc)) { reject(); return; }
        if (d["schema_version"].as<uint32_t>() != 1 || !d["fallback_enabled"].is<bool>() ||
            d["config_version"].as<uint32_t>() < state["config"]["config_version"].as<uint32_t>() ||
            d["duration_ms"].as<uint32_t>() < 1 || d["duration_ms"].as<uint32_t>() > 60000 ||
            d["cooldown_ms"].as<uint32_t>() < 1000 || d["cooldown_ms"].as<uint32_t>() > 3600000 ||
            d["required_low_samples"].as<uint32_t>() < 1 || d["required_low_samples"].as<uint32_t>() > 100 ||
            d["max_sample_age_ms"].as<uint32_t>() < 1000 || d["max_sample_age_ms"].as<uint32_t>() > 300000 ||
            d["edge_timeout_ms"].as<uint32_t>() < 15000 || d["edge_timeout_ms"].as<uint32_t>() > 300000 ||
            d["max_runs_per_hour"].as<uint32_t>() < 1 || d["max_runs_per_hour"].as<uint32_t>() > 32 ||
            d["max_total_duration_ms_per_hour"].as<uint32_t>() < 1 || d["max_total_duration_ms_per_hour"].as<uint32_t>() > 1920000 ||
            d["session_id"].as<String>().length() != 32 || d["sync_nonce"].as<String>().length() != 32) { reject(); return; }
        String incomingSession = d["session_id"], nonce = d["sync_nonce"];
        d.remove("session_id"); d.remove("sync_nonce");
        if (hasConfig() && d["config_version"] == state["config"]["config_version"] && d.as<JsonVariantConst>() != state["config"].as<JsonVariantConst>()) { reject(); return; }
        bool changed = !hasConfig() || d["config_version"] != state["config"]["config_version"];
        if (changed) {
            uint32_t unlock = state["config"]["unlock_generation"];
            state["config"].set(d);
            if (d["unlock_generation"].as<uint32_t>() > unlock) state["stop_locked"] = false;
            if (!save()) return;
            lowSamples = 0;
        }
        if (session != incomingSession) {
            debugLastId = debugId; debugId = ""; debugStopped = false;
            session = incomingSession; heartbeatSequence = 0; haveHeartbeat = false;
            lastHeartbeat = millis();
            heartbeatLossLogged = false;
        }
        JsonDocument ack;
        ack["event"] = "config_applied"; ack["edge_id"] = edge; ack["node_id"] = node;
        ack["session_id"] = session; ack["sync_nonce"] = nonce;
        ack["config_version"] = state["config"]["config_version"];
        ack["journal_pending"] = state["journal"].size(); ack["pump_running"] = pump.isRunning();
        String encoded; serializeJson(ack, encoded); if (publish) publish(encoded);
    }
    void sample(float value, bool valid) {
        if (hasConfig() && elapsed(lastSample, setting("max_sample_age_ms"))) lowSamples = 0;
        sampleValid = valid && std::isfinite(value) && value >= 0 && value <= 100;
        if (!sampleValid) { lowSamples = 0; return; }
        moisture = value; lastSample = millis();
        if (!hasConfig()) return;
        if (value >= state["config"]["rearm_threshold_pct"].as<float>()) {
            if (state["dry_used"].as<bool>()) { state["dry_used"] = false; save(); }
            lowSamples = 0;
        } else if (value < state["config"]["emergency_threshold_pct"].as<float>()) {
            if (lowSamples < 100) ++lowSamples;
        } else lowSamples = 0;
    }
    const char* lastStartError = "none";
    String lastStartDetail;
    uint32_t lastStartRetryMs = 0;
    bool start(uint32_t duration, bool fallback = false) {
        lastStartError = "none"; lastStartDetail = ""; lastStartRetryMs = 0;
        auto reject = [&](const char* reason, const String& detail, uint32_t retryMs = 0) {
            lastStartError = reason; lastStartDetail = detail; lastStartRetryMs = retryMs;

            return false;
        };
        if (!healthy) return reject("storage_unavailable", "Persistent safety state is unavailable");
        if (pump.isRunning()) return reject("pump_already_running", "Pump is ON; existing run was not extended");
        if (hasConfig()) {
            uint32_t sinceStop = uint32_t(millis() - lastStop);
            if (sinceStop < setting("cooldown_ms"))
                return reject("cooldown", "Wait " + String(setting("cooldown_ms") - sinceStop) + " ms after last stop", setting("cooldown_ms") - sinceStop);
            // Run-count limit retired. Keep legacy wire/NVS fields compatible;
            // cooldown and cumulative duration budget still apply.
            uint32_t total = state["total_ms"];
            if (total + duration > setting("max_total_duration_ms_per_hour"))
                return reject("hourly_duration_limit", "Used " + String(total) + " ms; requested " + String(duration) + " ms; limit " + String(setting("max_total_duration_ms_per_hour")) + " ms");
        }
        if (duration == 0 || duration > 60000) return reject("invalid_duration", "duration_ms must be 1..60000");
        if (state["journal"].size() >= 30) return reject("audit_journal_full", "30 audit entries pending; wait for Edge reconciliation");
        runId = boot + "-run-" + String(++serial); source = fallback ? "local_fallback" : "edge";
        requestedDuration = duration;
        state["running"] = true; state["run_id"] = runId;
        state["runs"] = state["runs"].as<uint32_t>() + 1;
        state["total_ms"] = state["total_ms"].as<uint32_t>() + duration;
        if (fallback) state["dry_used"] = true;
        audit("start_reserved");
        if (!save()) return reject("state_persist_failed", "Could not reserve safety state before motor power");
        hourStart = startedAt = millis(); previousRunning = true;
        if (!pump.start(duration)) { previousRunning = false; state["running"] = false; audit("start_failed"); save(); return reject(pump.lastStartError, "Pump controller refused start; see node controller log"); }
        if (fallback) Serial.printf("[PUMP LOCAL] run=%s duration_ms=%lu\n", runId.c_str(), (unsigned long)duration);
        if (fallback && !debugId.isEmpty()) debugRunId = runId;
        return true;
    }
    void stop() { state["stop_locked"] = true; save(); pump.stop(); }
    void tick() {
        // Detect loss before evaluating storage health, Auto, moisture or pump limits.
        if (hasConfig() && !heartbeatLossLogged && elapsed(lastHeartbeat, setting("edge_timeout_ms"))) {
            heartbeatLossLogged = true;
            Serial.printf("[FALLBACK] HEARTBEAT LOST node=%s | no Edge heartbeat for %lu ms\n",
                node.c_str(), static_cast<unsigned long>(uint32_t(millis() - lastHeartbeat)));
        }
        if (!healthy) { pump.stop(); return; }
        if (previousRunning && !pump.isRunning()) {
            previousRunning = false; lastStop = millis();
            state["running"] = false; audit("stopped", millis() - startedAt); save();
            if (!debugId.isEmpty() && debugRunId == runId && source == "local_fallback") {
                debugStopped = true; debugStoppedAt = millis(); debugActual = millis() - startedAt;
                debugLastAck = millis() - 2000;
            }
        }
        if (!debugId.isEmpty()) {
            if (elapsed(debugSince, 120000)) {
                debugLastId = debugId; debugId = ""; debugStopped = false;
            } else if (debugStopped && !pump.isRunning() && elapsed(debugStoppedAt, debugDelay) && elapsed(debugLastAck, 2000)) {
                debugLastAck = millis(); debugReply("debug_simulation_completed");
            }
        }
        if (elapsed(hourStart, 3600000)) {
            hourStart = millis(); state["runs"] = 0; state["total_ms"] = 0; save();
        }
        if (publish && state["journal"].size() && elapsed(lastAudit, 2000)) {
            lastAudit = millis(); String record; serializeJson(state["journal"][0], record); publish(record);
        }
        if (!hasConfig() || !state["config"]["fallback_enabled"].as<bool>() || state["stop_locked"].as<bool>() ||
            state["dry_used"].as<bool>() || !sampleValid || elapsed(lastSample, setting("max_sample_age_ms")) ||
            lowSamples < setting("required_low_samples") || !elapsed(lastHeartbeat, setting("edge_timeout_ms"))) return;
        // Boot starts a fresh timeout, never treats the zero millis epoch as an expired heartbeat.
        start(setting("duration_ms"), true);
    }
};
