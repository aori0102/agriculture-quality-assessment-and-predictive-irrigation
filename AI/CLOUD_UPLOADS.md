# Cloud uploads and node pump events

The existing file layout is retained: AI/ hosts app.py and the processing handlers;
Cloud Edge/ hosts CloudClient, Blynk transports and the combined launcher.

Run the combined application in a Python environment containing both AI and Cloud
dependencies: from `Cloud Edge/`, run `python -m run_edge`.
The launcher registers its node-bound CloudClients with AI/cloud_reporter.py.
Running `AI/app.py` alone receives local messages but has no registered Cloud clients,
so no HTTP uploads occur.

Data flow:
- Accepted and recorded telemetry -> send_telemetry (V0, V1, V6).
- Irrigation forecast without future irrigation -> send_ai_results (V5).
- Saved image inference result -> send_ai_results with classification label counts (V2).
- Valid pump_started/pump_stopped event -> report_pump_status (V4 True/False).

Uploads use one background worker and a bounded 256-item queue. Only bound nodes
upload. Full queues drop the new update and log; failed HTTP calls log the exception
class without retry/offline storage. Requests are sent separately, without batching
across processing cycles. The worker is drained/stopped before Cloud clients close.

MQTT subscribes to [mqtt] event_filter using event_qos from AI/config.cfg.
As with telemetry/image topics, node_id is the next-to-last topic segment.
Pump events use node_id, sent_at, event and valid_until. Both timestamps must be
explicitly timezone-aware, and sent_at <= receive time < valid_until. Payload node_id
must match the topic. Retained, malformed, unknown, duplicate and out-of-order events
are ignored. Ordering is tracked per node for the lifetime of the process.
Event names supported: pump_started, pump_stopped. These confirmations do not change
irrigation authorization or issue new pump commands.

Tests: from AI/, run `python -B test_cloud_reporter.py`. The tests mock network/model
services and cover worker dispatch, failures, event validation, MQTT subscription/
callback routing, accepted telemetry and launcher lifecycle. Live Blynk, camera and
ESP32 execution have not been verified by these tests.
