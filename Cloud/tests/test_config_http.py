import os
from pathlib import Path
import tempfile
import traceback
import unittest
from unittest.mock import Mock, patch

import requests

from Cloud.config import CloudConfig
from Cloud.blynk_http import BlynkHTTP
from Cloud.errors import CloudError


class ConfigTests(unittest.TestCase):
    def test_defaults_and_secret_representation(self):
        config = CloudConfig(server="sgp1.blynk.cloud", auth_token="test-secret")
        self.assertEqual((config.http_timeout, config.command_lifetime), (10, 5))
        self.assertEqual((config.mqtt_port, config.mqtt_keepalive), (8883, 45))
        self.assertNotIn("test-secret", repr(config))

    def test_env_precedence_without_mutating_environment_or_using_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("BLYNK_SERVER=sgp1.blynk.cloud\nBLYNK_AUTH_TOKEN=file-secret\n")
            with patch("Cloud.config.ENV_FILE", env_file), \
                    patch.dict(os.environ, {"BLYNK_AUTH_TOKEN": "env-secret"}, clear=True), \
                    patch("pathlib.Path.cwd", side_effect=AssertionError("cwd dependency")):
                config = CloudConfig.from_env()
                self.assertEqual(config.server, "sgp1.blynk.cloud")
                self.assertEqual(config.auth_token, "env-secret")
                self.assertNotIn("BLYNK_SERVER", os.environ)

    def test_missing_configuration(self):
        with patch("Cloud.config.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                CloudConfig.from_env()

    def test_invalid_configuration(self):
        for changes in (
            {"server": "https://sgp1.blynk.cloud"}, {"server": "blynk.cloud"},
            {"server": "sgp1.blynk.cloud/path"}, {"server": "example.com"},
            {"server": "sgp1.blynk.cloud.evil.test"}, {"server": None},
            {"auth_token": " "}, {"auth_token": None}, {"auth_token": "a\nb"},
            {"http_timeout": 0}, {"http_timeout": float("nan")},
            {"command_lifetime": -1}, {"command_lifetime": float("inf")},
            {"command_lifetime": 1e100},
            {"command_lifetime": True}, {"mqtt_port": 1883}, {"mqtt_keepalive": 0},
        ):
            with self.subTest(changes=changes):
                kwargs = {"server": "sgp1.blynk.cloud", "auth_token": "test-secret", **changes}
                with self.assertRaises(ValueError):
                    CloudConfig(**kwargs)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(spec=requests.Session)
        self.response = requests.Response()
        self.response.status_code = 200
        self.response._content = b""
        self.response._content_consumed = True
        self.session.get.return_value = self.response
        patcher = patch("Cloud.blynk_http.requests.Session", return_value=self.session)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.http = BlynkHTTP(CloudConfig("sgp1.blynk.cloud", "test-secret"))
        self.addCleanup(self.http.close)

    def test_batch_mapping_rounding_and_verified_transport(self):
        self.http.send_snapshot(
            soil_index_pct=42.5, temperature_c=28, air_humidity_pct=65.1,
            plant_status="Healthy", predicted_moisture_pct=40.5, pump_is_on=False,
        )
        self.session.get.assert_called_once_with(
            "https://sgp1.blynk.cloud/external/api/batch/update",
            params={"token": "test-secret", "v0": 43, "v1": 28.0,
                    "v6": 65, "v2": "Healthy", "v5": 41, "v4": 0},
            timeout=10, verify=True, allow_redirects=False,
        )
        self.assertNotIn("v3", self.session.get.call_args.kwargs["params"])
        self.assertIs(type(self.session.get.call_args.kwargs["params"]["v1"]), float)
        self.response.close()

    def test_omitted_values_and_empty_update(self):
        self.http.send_snapshot()
        self.session.get.assert_not_called()
        self.http.send_snapshot(soil_index_pct=0, air_humidity_pct=100)
        self.assertEqual(self.session.get.call_args.kwargs["params"],
                         {"token": "test-secret", "v0": 0, "v6": 100})

    def test_value_just_below_half_is_not_rounded_up(self):
        self.http.send_snapshot(soil_index_pct=0.49999999999999994)
        self.assertEqual(self.session.get.call_args.kwargs["params"]["v0"], 0)

    def test_timeout_override_is_used(self):
        http = BlynkHTTP(CloudConfig("sgp1.blynk.cloud", "test-secret", http_timeout=2.5))
        self.addCleanup(http.close)
        http.send_snapshot(temperature_c=28)
        self.assertEqual(self.session.get.call_args.kwargs["timeout"], 2.5)

    def test_validation_happens_before_any_request(self):
        cases = [(field, value) for field in (
            "soil_index_pct", "temperature_c", "air_humidity_pct", "predicted_moisture_pct"
        ) for value in (True, "42", float("nan"), float("inf"), -float("inf"))]
        cases += [(field, value) for field in (
            "soil_index_pct", "air_humidity_pct", "predicted_moisture_pct"
        ) for value in (-0.1, 100.1)]
        cases += [("plant_status", 1), ("pump_is_on", 1), ("pump_is_on", "0")]
        for field, value in cases:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.http.send_snapshot(**{field: value})
        self.session.get.assert_not_called()

    def test_http_errors_and_redirects_are_sanitized(self):
        for status in (301, 308, 400, 401, 429, 500):
            with self.subTest(status=status):
                self.response.status_code = status
                self.response.url = "https://sgp1.blynk.cloud/?token=test-secret"
                with self.assertRaises(CloudError) as caught:
                    self.http.send_snapshot(temperature_c=28)
                self.assertEqual(caught.exception.status_code, status)
                self.assertNotIn("test-secret", str(caught.exception))

    def test_network_exceptions_do_not_leak_traceback_secrets(self):
        for error in (requests.Timeout, requests.ConnectionError, requests.RequestException):
            with self.subTest(error=error):
                self.session.get.side_effect = error("URL contains test-secret")
                try:
                    self.http.send_snapshot(temperature_c=28)
                except CloudError as exc:
                    self.assertNotIn("test-secret", "".join(traceback.format_exception(exc)))
                else:
                    self.fail("Expected CloudError")

    def test_close_is_idempotent_and_rejects_further_uploads(self):
        self.http.close()
        self.http.close()
        self.session.close.assert_called_once()
        with self.assertRaises(CloudError):
            self.http.send_snapshot()


if __name__ == "__main__":
    unittest.main()
