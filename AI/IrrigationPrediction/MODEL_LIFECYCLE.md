# Irrigation model lifecycle

- `models/` is the shared store for original and retrained Random Forest models (`*.joblib`). Manual training writes there by default; an explicit `--output` remains available for experiments.
- Startup validates the configured model inside this store first, then searches other models newest-first by modification time, skipping invalid files. A discovered model is recorded atomically in `config.cfg`. Legacy config/log paths resolve by filename inside `models/`.
- If no usable model exists, startup synchronously trains from `datasets/train_data.csv` and `datasets/test_data.csv`. MQTT setup resumes only after training, validation and atomic config update succeed. Training has a one-hour timeout; failure stops startup.
- After successful retraining and activation, cleanup keeps up to three `models/*.joblib` files, including original models. The active model and candidate awaiting activation are protected and count toward the limit. Other file types and symlinks are ignored. Deletion failures are logged.
- Startup recovery training clears stale pending retrain status after saving the recovered model.
- Run tests from `AI`: `python -B IrrigationPrediction/test_model_lifecycle.py`. Tests include real training from copies of the base datasets in a temporary directory; they do not connect to MQTT or alter deployed models/config.
