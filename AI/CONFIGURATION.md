# Shared AI configuration

All application settings are in `AI/config.cfg`. Component-local config.cfg files
have been removed. Paths in the shared file are relative to `AI/`, regardless of
which directory launches the application.

| Section | Purpose |
| --- | --- |
| edge | Edge identity |
| mqtt | Broker, topic filters, QoS and retain options |
| pump | Pump duration, TTL and acknowledgement settings |
| nodes | Default node profile and node history settings |
| retrain | Retraining schedule mode |
| irrigation | Irrigation duration candidates |
| irrigation_sampling | Sampling delay |
| irrigation_model | Current irrigation model path |
| vision_model | Detection and classification weights |
| vision_output | Annotated-image output directory |
| capture | Capture times, time offset and polling interval |

For example, `IrrigationPrediction/models/model.joblib` and
`ComputerVision/runs/detect/.../best.pt` resolve from AI/. Dataset, model and output
folders stay in their existing locations. Applying a model updates only the
irrigation_model/model_path value in the shared config using atomic replacement;
other sections and settings remain present.

Verification includes model activation and real bootstrap training in a temporary
folder, checking that activation preserves vision settings. MQTT and Blynk
credentials remain configured as before (Cloud credentials in repository-root .env).
