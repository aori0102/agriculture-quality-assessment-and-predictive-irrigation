# Blynk telemetry and Events

Updated 2026-11-22. Run the unified `Cloud Edge/run_edge.py` launcher using the
project virtual environment. Running `AI/app.py` alone does not configure Blynk.
Restart the existing Edge process to load these changes; do not start a second
process using the same device tokens.

## Measurements and forecasts

- Soil moisture uses V0. Every validated sensor sample is forwarded even when
  its 10-minute training row has already been sealed. The sealed row stays frozen.
- Predicted moisture uses V5 and means the forecast in 10 minutes **without
  additional irrigation**, not the observed soil value or a post-irrigation forecast.
- Turning Auto OFF no longer suppresses a valid forecast. It still prevents an
  automatic pump start. A monitoring-only decision seals the window with zero
  planned irrigation, consistent with the existing per-window decision model.
- Forecasts still require consecutive 10-minute telemetry windows and a known
  previous irrigation duration. Fallback configuration/audit readiness and the
  post-offline-irrigation quarantine still apply. No synthetic forecast is uploaded.
- Successful HTTPS uploads log `UPLOAD ... | Blynk accepted`; failures include
  node, method, sanitized error category and HTTP status. Missing bindings log
  `UPLOAD SKIPPED` once until that node is bound.

Direct read-only checks on 2026-11-22 found V0/V5 values of 87/86 and 57/56 on
the two bound devices. These are existing values, not proof of fresh sensor
traffic or of the updated code running. Both template streams had raw and
aggregated history disabled. A time-series comparison requires enabling history
for V0 and V5 in Blynk and binding both to the chart; historical data before
enabling storage cannot be reconstructed by this change.

## Timeline and alerts

Five Event definitions were created on template TMPL6FGqaSpR3:

| Code | Trigger | Notification |
|---|---|---|
| pump_started | A node-confirmed transition to ON, including initial observed ON | Timeline |
| pump_stopped | A node-confirmed transition from ON to OFF | Timeline |
| pump_ack_timeout | Pending command acknowledgement times out | Timeline and owner push |
| soil_moisture_low | Fresh soil reading falls below the active MADP | Timeline and owner push |
| soil_moisture_critical | Fresh soil reading reaches or falls below PWP | Timeline and owner push |

The warning/critical definitions enable push to the Device Owner metafield
(ID 2), notification management, and a 15-minute notification limit. Delivery
also depends on device ownership, Blynk user settings and OS permissions.
No test push notification or physical pump command was sent during verification.

The Edge sends `/external/api/logEvent` asynchronously. Repeated five-second
pump heartbeats do not become Timeline entries. Initial OFF is a baseline.
Soil warnings occur once per dry episode, escalate once to critical, and rearm
when moisture reaches MADP. Only sensor samples aged 0–120 seconds can trigger
soil alerts; profile thresholds stored as fractions are converted to percentages.

All codes share an Edge budget of 90 queued events per node per rolling 24 hours
and a minimum 60 seconds between repetitions of the same code. This state is
in memory and resets on Edge restart; Blynk's own account/device quotas still
apply. Queue/HTTP failures can lose Events; there is no durable replay. Pump
confirmation heartbeats remain available on V4 independently of Timeline limits.
No ambiguous HTTP request is automatically retried, to avoid duplicate Events.

## Verification

Offline tests cover V0/V5 mappings, Event endpoint/TLS/input validation, confirmed
pump transitions, no heartbeat spam, soil escalation/rearming, shared rate limits,
telemetry forwarding after row sealing, and forecasts with Auto OFF while pump
start remains blocked. Actual fresh hardware-to-Blynk delivery still needs the
updated Edge process and live node telemetry.

## Chart configuration repaired (2026-11-22)

Changed V0 and V5 History from Last Value to 1 Minute AVG in Blynk.Console, enabled chart legend, and saved/applied the template to all three devices. Read-back confirmed saveAggregatedHistoryValues=true on both streams; RAW remains disabled. The online device dashboard rendered the actual soil-moisture line in Live mode. Existing latest values do not backfill historical points: V5 needs a fresh Edge forecast before its line can appear, and 1h history accumulates only after this change.

