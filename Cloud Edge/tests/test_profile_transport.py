import json
import unittest
from unittest.mock import Mock, patch

import requests

from cloud_edge.blynk_http import BlynkHTTP
from cloud_edge.config import CloudConfig, load_device_bindings
from cloud_edge.errors import CloudError
from tests.test_profiles import draft


class ProfileHTTPTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(spec=requests.Session)
        self.response = requests.Response()
        self.response.status_code = 200
        self.response._content = json.dumps(draft()).encode()
        self.response._content_consumed = True
        self.session.get.return_value = self.response
        patcher = patch("cloud_edge.blynk_http.requests.Session", return_value=self.session)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.http = BlynkHTTP(CloudConfig("sgp1.blynk.cloud", "test-secret"))
        self.addCleanup(self.http.close)

    def test_read_fetches_all_draft_fields_in_one_verified_request(self):
        self.assertEqual(self.http.read_profile_snapshot(), draft())
        call = self.session.get.call_args
        self.assertEqual(call.args[0], "https://sgp1.blynk.cloud/external/api/get")
        self.assertEqual(set(call.kwargs["params"]), set(draft()) | {"token"})
        self.assertTrue(call.kwargs["verify"])
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertEqual(call.kwargs["timeout"], 10)

    def test_unset_description_reads_as_allowed_empty_text(self):
        self.response._content = json.dumps(draft(v8=None)).encode()
        self.assertEqual(self.http.read_profile_snapshot()["v8"], "")

    def test_response_errors_are_sanitized(self):
        for body in (b"test-secret", b"[]", b'{"error":"test-secret"}'):
            self.response._content = body
            with self.assertRaises(CloudError) as raised:
                self.http.read_profile_snapshot()
            self.assertNotIn("test-secret", str(raised.exception))
        self.session.get.side_effect = requests.Timeout("test-secret")
        with self.assertRaises(CloudError) as raised:
            self.http.read_profile_snapshot()
        self.assertEqual(raised.exception.category, "timeout")

    def test_profile_writes_cannot_write_pump_pins_or_trigger_apply(self):
        self.http.write_profile({"v7": "node-1", "v14": 0, "v15": "APPLIED rev 1"})
        self.assertNotIn("v3", self.session.get.call_args.kwargs["params"])
        for values in ({"v3": 1}, {"v4": 1}, {"v14": 1}, {"v16": 2},
                       {"v10": float("nan")}, {"v11": True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.http.write_profile(values)


class BindingTests(unittest.TestCase):
    def values(self):
        return {
            "BLYNK_DEVICES_JSON": json.dumps([
                dict(node_id="one", server="sgp1.blynk.cloud", auth_token_env="TOKEN_ONE"),
                dict(node_id="two", server="sgp1.blynk.cloud", auth_token_env="TOKEN_TWO"),
            ]),
            "TOKEN_ONE": "secret-one", "TOKEN_TWO": "secret-two",
        }

    def test_loads_multiple_devices_without_exposing_tokens(self):
        bindings = load_device_bindings(self.values())
        self.assertEqual([binding.node_id for binding in bindings], ["one", "two"])
        self.assertEqual(bindings[1].config.auth_token, "secret-two")
        self.assertNotIn("secret-one", repr(bindings))

    def test_rejects_duplicate_tokens_nodes_and_invalid_configuration(self):
        values = self.values()
        entries = json.loads(values["BLYNK_DEVICES_JSON"])
        duplicates = [entries[0], entries[0]]
        for changes in (
            {"TOKEN_TWO": "secret-one"}, {"TOKEN_ONE": ""},
            {"BLYNK_DEVICES_JSON": json.dumps(duplicates)},
            {"BLYNK_DEVICES_JSON": "invalid secret-one"},
            {"BLYNK_DEVICES_JSON": "{}"}, {"BLYNK_DEVICES_JSON": "[]"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError) as raised:
                load_device_bindings(values | changes)
            self.assertNotIn("secret-one", str(raised.exception))
