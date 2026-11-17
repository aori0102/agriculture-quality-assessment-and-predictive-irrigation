from dataclasses import FrozenInstanceError, replace
import unittest

from cloud_edge.profiles import PlantProfile, illustrative_preset, validate_snapshot


def draft(node_id="node-1", **changes):
    values = dict(v7=node_id, v8="Window", v9="Lettuce", v10=0.10,
                  v11=0.30, v12=0.22, v13=0.41, v16=0)
    return values | changes


class ProfileTests(unittest.TestCase):
    def test_fractional_profile_and_derived_madp(self):
        profile = validate_snapshot(draft(), node_id="node-1")
        self.assertAlmostEqual(profile.madp, 0.184)
        self.assertEqual(profile.mode, "custom")
        with self.assertRaises(FrozenInstanceError):
            profile.field_capacity = 0.5

    def test_rejects_missing_fields_invalid_types_and_boundaries(self):
        for pin in draft():
            values = draft()
            del values[pin]
            with self.subTest(missing=pin), self.assertRaises(ValueError):
                validate_snapshot(values, node_id="node-1")
        for changes in (
            dict(v7="node-2"), dict(v9=" "), dict(v8=None),
            dict(v10=True), dict(v11="0.3"), dict(v12=float("nan")),
            dict(v13=float("inf")), dict(v11=0), dict(v11=1),
            dict(v10=-0.1), dict(v10=0.22), dict(v12=0.41),
            dict(v13=1.01), dict(v16=True), dict(v16=99),
            dict(v10=[0.1]), dict(v10=10 ** 400),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_snapshot(draft(**changes), node_id="node-1")

    def test_preset_requires_matching_thresholds_but_allows_metadata_edits(self):
        profile = validate_snapshot(draft(v16=1, v9="My lettuce"), node_id="node-1")
        self.assertEqual(profile.mode, "preset")
        self.assertEqual(profile.preset_key, "illustrative_lettuce")
        with self.assertRaisesRegex(ValueError, "Custom"):
            validate_snapshot(draft(v16=1, v12=0.25), node_id="node-1")
        self.assertEqual(validate_snapshot(draft(v12=0.25), node_id="node-1").mode, "custom")

    def test_direct_construction_is_also_validated(self):
        profile = illustrative_preset("node-1", "Window")
        self.assertIsInstance(profile, PlantProfile)
        self.assertIn("Illustrative", profile.plant_name)
        with self.assertRaises(ValueError):
            replace(profile, management_allowed_depletion=0)
        with self.assertRaises(ValueError):
            replace(profile, mode="custom", preset_key="illustrative_lettuce")


if __name__ == "__main__":
    unittest.main()
