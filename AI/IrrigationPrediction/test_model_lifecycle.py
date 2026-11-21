import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from operation_log_manager import OperationLogManager
import json

AI = Path(os.environ.get('EDGE_AI_PATH', str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(AI))
SOURCE = Path(os.environ.get('MODEL_RETRAINER_SOURCE', str(AI / 'IrrigationPrediction/model_retrainer.py')))
spec = importlib.util.spec_from_file_location('IrrigationPrediction.model_retrainer', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "IrrigationPrediction"
        self.root.mkdir()
        self.models = self.root / 'models'
        self.models.mkdir()
        (self.root.parent / 'config.cfg').write_text('[irrigation_model]\nmodel_path = IrrigationPrediction/models/irrigation_0.joblib\n[vision_model]\ndetection_path = ComputerVision/detector.pt\n')
        self.log = OperationLogManager()
        self.log.log_file_path = self.root.parent / 'operation_log.json'
        self.log.set_model_path('IrrigationPrediction/models/irrigation_0.joblib')

    def files(self):
        for i in range(6):
            path = self.models / f'irrigation_{i}.joblib'
            path.write_bytes(b'old')
            os.utime(path, (100+i, 100+i))

    def test_retention_protects_active_and_pending(self):
        self.files()
        unrelated = self.models / 'notes.txt'
        unrelated.touch()
        m.prune_models(self.root, self.models / 'irrigation_1.joblib', log=self.log)
        self.assertEqual({p.name for p in self.models.glob('irrigation_*.joblib')},
                         {'irrigation_0.joblib', 'irrigation_1.joblib', 'irrigation_5.joblib'})
        self.assertTrue(unrelated.exists())

    def test_valid_startup_does_not_train(self):
        model = object()
        with patch.object(m, 'load_validated_model', return_value=model), patch.object(m.subprocess, 'run') as run:
            self.assertIs(m.load_or_train_initial_model(self.root, self.log), model)
        run.assert_not_called()

    def test_search_uses_original_model_and_skips_corrupt_newer_file(self):
        original = self.models / 'original.joblib'
        original.touch()
        corrupt = self.models / 'newer.joblib'
        corrupt.touch()
        os.utime(original, (100, 100))
        os.utime(corrupt, (200, 200))
        model = object()
        def load(path):
            if path == original:
                return model
            raise ValueError('invalid model')
        with patch.object(m, 'load_validated_model', side_effect=load), patch.object(m.subprocess, 'run') as run:
            self.assertIs(m.load_or_train_initial_model(self.root, self.log), model)
        run.assert_not_called()
        self.assertEqual(self.log.get_model_status()['model_path'], 'IrrigationPrediction/models/original.joblib')

    def test_legacy_log_without_active_path_keeps_other_fields(self):
        data = self.log.load_log()
        del data['model_status']['model_path']
        data['model_status']['last_retrain_period'] = 'keep-period'
        data['node_profile']['one'] = {'node_id': 'one', 'custom': 12}
        self.log.log_file_path.write_text(json.dumps(data))
        self.assertEqual(self.log.get_model_status()['model_path'], '')
        self.log.set_model_path('IrrigationPrediction/models/selected.joblib')
        restored = self.log.load_log()
        self.assertEqual(restored['model_status']['last_retrain_period'], 'keep-period')
        self.assertEqual(restored['node_profile']['one']['custom'], 12)
        self.log.clear_model_status()
        self.assertTrue(self.log.get_model_status()['model_path'].endswith('selected.joblib'))

    def test_startup_uses_log_over_legacy_config_and_never_rewrites_config(self):
        self.log.set_model_path('IrrigationPrediction/models/selected.joblib')
        before = (self.root.parent / 'config.cfg').read_bytes()
        with patch.object(m, 'load_validated_model', return_value=object()) as load:
            m.load_or_train_initial_model(self.root, self.log)
        self.assertEqual(load.call_args.args[0], self.models / 'selected.joblib')
        self.assertEqual(before, (self.root.parent / 'config.cfg').read_bytes())

    def test_legacy_config_migrates_after_validation(self):
        data = self.log.load_log()
        data['model_status']['model_path'] = ''
        self.log.save_log(data)
        with patch.object(m, 'load_validated_model', return_value=object()):
            m.load_or_train_initial_model(self.root, self.log)
        self.assertEqual(self.log.get_model_status()['model_path'],
                         'IrrigationPrediction/models/irrigation_0.joblib')

    def test_legacy_paths_resolve_inside_models(self):
        self.assertEqual(m.resolve_model_path(self.root, 'retrained_models/old.joblib'),
                         self.models / 'old.joblib')
        self.assertEqual(m.resolve_model_path(self.root, 'original.joblib'),
                         self.models / 'original.joblib')

    def test_training_failure_keeps_config_and_stops(self):
        before = (self.root.parent / 'config.cfg').read_bytes()
        with patch.object(m.subprocess, 'run', side_effect=RuntimeError('training failed')):
            with self.assertRaisesRegex(RuntimeError, 'setup stopped'):
                m.load_or_train_initial_model(self.root, self.log)
        self.assertEqual(before, (self.root.parent / 'config.cfg').read_bytes())
        self.assertEqual(list(self.models.iterdir()), [])
        self.assertEqual(self.log.get_model_status()['model_path'], 'IrrigationPrediction/models/irrigation_0.joblib')

    def test_invalid_prediction_rejected(self):
        model = Mock()
        model.feature_names_in_ = [m.fc.SOIL_MOISTURE, m.fc.WEATHER_TEMPERATURE,
            m.fc.WEATHER_HUMIDITY, m.fc.IRRIGATION_PAST_10M,
            m.fc.IRRIGATION_DURATION_MINUTES, m.fc.SOIL_CHANGE_10M]
        model.predict.return_value = [float('nan')]
        with patch.object(m.joblib, 'load', return_value=model):
            with self.assertRaises(ValueError):
                m.load_validated_model(self.root / 'bad.joblib')

    def test_activation_updates_model_and_prunes(self):
        self.files()
        retrainer = object.__new__(m.ModelRetrainer)
        retrainer.directory = self.root
        retrainer.irrigation = Mock()
        retrainer.log = self.log
        self.log.log_model_training('test')
        self.log.log_model_ready('models/irrigation_5.joblib')
        model = object()
        with patch.object(m, 'load_validated_model', return_value=model):
            retrainer._activate()
        self.assertIs(retrainer.irrigation.model, model)
        self.assertEqual(len(list(self.models.glob('irrigation_*.joblib'))), 3)
        self.assertEqual(self.log.get_model_status()['model_path'], 'IrrigationPrediction/models/irrigation_5.joblib')
        self.assertIn('detection_path = ComputerVision/detector.pt', (self.root.parent / 'config.cfg').read_text())

    def test_real_bootstrap_from_base_datasets_and_reload(self):
        for name in ['random_forest_train.py', 'field_constants.py']:
            source = SOURCE.parent / name
            if not source.exists():
                source = AI / 'IrrigationPrediction' / name
            shutil.copyfile(source, self.root / name)
        shutil.copytree(AI / 'IrrigationPrediction/datasets', self.root / 'datasets')
        (self.models / 'irrigation_0.joblib').write_bytes(b'corrupt')
        model = m.load_or_train_initial_model(self.root, self.log)
        self.assertTrue(hasattr(model, 'predict'))
        self.assertFalse(self.log.get_model_status()['new_model_ready'])
        self.assertTrue(self.log.get_model_status()['model_path'])
        with patch.object(m.subprocess, 'run') as run:
            m.load_or_train_initial_model(self.root, self.log)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

