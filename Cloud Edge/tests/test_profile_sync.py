from dataclasses import replace
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import Mock, patch

from cloud_edge.errors import CloudError
from cloud_edge.profiles import ActiveProfile, ProfileApplyResult, illustrative_preset, validate_snapshot
from cloud_edge.profile_sync import ProfileSync
from cloud_edge.datastreams import BlynkTopics
from tests.test_profiles import draft


def wait_until(predicate, timeout=2):
    deadline = monotonic() + timeout
    while not predicate():
        if monotonic() >= deadline:
            raise AssertionError("Timed out waiting for worker")
        sleep(0.005)


class FakeBackend:
    def __init__(self):
        self.active = {}
        self.calls = []

    def get_active(self, node_id):
        return self.active.get(node_id)

    def apply_profile(self, profile):
        self.calls.append(profile)
        old = self.get_active(profile.node_id)
        if old is None or old.profile != profile:
            self.active[profile.node_id] = ActiveProfile(
                profile, 1 if old is None else old.revision + 1,
                "Automatic irrigation blocked: VWC calibration required",
            )
        return ProfileApplyResult("applied", self.active[profile.node_id])


class ProfileSyncTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.http = Mock()
        self.http.read_profile_snapshot.return_value = draft()
        self.sync = self.make_sync("node-1", self.http)

    def make_sync(self, node_id, http):
        sync = ProfileSync(http, self.backend, node_id=node_id, retry_interval=0.02)
        sync.start()
        self.addCleanup(sync.close)
        sync.connected()
        wait_until(lambda: sync.ready)
        return sync

    def apply(self):
        self.sync.receive(BlynkTopics.APPLY_PROFILE, "1")

    def test_unconfigured_restore_then_apply_and_idempotent_repeat(self):
        self.assertIn("UNCONFIGURED", self.sync.last_status)
        self.apply()
        wait_until(lambda: "APPLIED" in self.sync.last_status)
        self.assertEqual(self.backend.active["node-1"].revision, 1)
        self.assertIn("VWC", self.sync.last_status)
        self.apply()
        wait_until(lambda: len(self.backend.calls) == 2)
        self.assertEqual(self.backend.active["node-1"].revision, 1)
        for call in self.http.write_profile.call_args_list:
            self.assertNotIn("v3", call.args[0])
            self.assertNotIn("v4", call.args[0])

    def test_rejection_preserves_previous_state(self):
        self.backend.apply_profile(validate_snapshot(draft(), node_id="node-1"))
        old = self.backend.active["node-1"]
        self.http.read_profile_snapshot.return_value = draft(v11=1)
        self.apply()
        wait_until(lambda: "REJECTED" in self.sync.last_status)
        self.assertIs(self.backend.active["node-1"], old)
        self.assertEqual(len(self.backend.calls), 1)

    def test_presets_fill_drafts_without_applying_and_custom_keeps_values(self):
        self.http.read_profile_snapshot.return_value = draft(v16=1)
        self.sync.receive(BlynkTopics.PROFILE_PRESET, "1")
        wait_until(lambda: "Illustrative" in self.sync.last_status)
        writes = [call.args[0] for call in self.http.write_profile.call_args_list]
        self.assertTrue(any(values.get("v10") == 0.1 for values in writes))
        self.assertFalse(self.backend.calls)
        self.http.write_profile.reset_mock()
        self.sync.receive(BlynkTopics.PROFILE_PRESET, "0")
        wait_until(lambda: "Custom" in self.sync.last_status)
        self.assertTrue(all("v10" not in call.args[0] for call in self.http.write_profile.call_args_list))

    def test_disconnect_during_snapshot_prevents_application_and_restores(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)

        def slow_read():
            entered.set()
            release.wait(2)
            return draft()

        self.http.read_profile_snapshot.side_effect = slow_read
        self.apply()
        self.assertTrue(entered.wait(1))
        self.sync.disconnected()
        self.sync.connected()
        release.set()
        wait_until(lambda: self.sync.ready)
        self.assertFalse(self.backend.calls)

    def test_slow_read_does_not_block_receipt_and_coalesces_duplicate_apply(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)

        def slow_read():
            entered.set()
            release.wait(2)
            return draft()

        self.http.read_profile_snapshot.side_effect = slow_read
        self.apply()
        self.assertTrue(entered.wait(1))
        for _ in range(20):
            self.apply()
        self.sync.receive(BlynkTopics.APPLY_PROFILE, "0")
        release.set()
        wait_until(lambda: "APPLIED" in self.sync.last_status)
        self.assertEqual(len(self.backend.calls), 1)

    def test_status_retry_does_not_reapply_or_rollback(self):
        failed = Event()

        def write(values):
            if values.get("v15", "").startswith("APPLIED") and not failed.is_set():
                failed.set()
                raise CloudError("Unavailable", category="network")

        self.http.write_profile.side_effect = write
        self.apply()
        self.assertTrue(failed.wait(1))
        wait_until(lambda: self.sync.last_error is None and "APPLIED" in self.sync.last_status)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(self.backend.active["node-1"].revision, 1)

    def test_backend_failure_and_network_failure_are_distinct_from_rejection(self):
        self.backend.apply_profile = Mock(side_effect=OSError("private-secret"))
        self.apply()
        wait_until(lambda: "SYNC_FAILED" in self.sync.last_status)
        self.assertNotIn("private-secret", self.sync.last_status)
        self.assertFalse(self.backend.active)

    def test_two_nodes_are_isolated_and_wrong_identity_is_rejected(self):
        second_http = Mock()
        second_http.read_profile_snapshot.return_value = draft("node-2", v9="Other plant")
        second = self.make_sync("node-2", second_http)
        second.receive(BlynkTopics.APPLY_PROFILE, "1")
        self.apply()
        wait_until(lambda: len(self.backend.active) == 2)
        self.assertEqual(self.backend.active["node-1"].profile.plant_name, "Lettuce")
        self.assertEqual(self.backend.active["node-2"].profile.plant_name, "Other plant")
        self.http.read_profile_snapshot.return_value = draft("node-2")
        self.apply()
        wait_until(lambda: "REJECTED" in self.sync.last_status)
        self.assertEqual(len(self.backend.calls), 2)

    def test_reconnect_restores_active_revision_and_never_replays_apply(self):
        profile = validate_snapshot(draft(), node_id="node-1")
        self.backend.apply_profile(profile)
        self.sync.disconnected()
        self.apply()
        self.sync.connected()
        wait_until(lambda: self.sync.ready)
        values = self.http.write_profile.call_args.args[0]
        self.assertEqual(values["v10"], profile.permanent_wilting_point)
        self.assertEqual(values["v14"], 0)
        self.assertIn("discarded", values["v15"])
        self.assertEqual(len(self.backend.calls), 1)

    def test_restored_selector_echo_does_not_overwrite_custom_plant_name(self):
        profile = replace(illustrative_preset("node-1"), plant_name="My lettuce")
        self.backend.apply_profile(profile)
        self.sync.disconnected()
        self.sync.connected()
        wait_until(lambda: self.sync.ready)
        self.http.read_profile_snapshot.return_value = draft(v16=1)
        self.http.read_profile_snapshot.reset_mock()
        self.sync.receive(BlynkTopics.PROFILE_PRESET, "1")  # Echo of restore.
        self.sync.receive(BlynkTopics.PROFILE_PRESET, "0")
        wait_until(lambda: "DRAFT: Custom" in self.sync.last_status)
        self.http.read_profile_snapshot.assert_not_called()

    def test_apply_expiring_during_read_never_reaches_backend(self):
        with patch("cloud_edge.profile_sync.monotonic", return_value=100) as clock:
            def read():
                clock.return_value = 131
                return draft()
            self.http.read_profile_snapshot.side_effect = read
            self.apply()
            wait_until(lambda: "expired" in self.sync.last_status)
        self.assertFalse(self.backend.calls)

    def test_restore_failure_keeps_events_disabled_until_retry_succeeds(self):
        self.sync.disconnected()
        self.http.write_profile.side_effect = CloudError("Unavailable", category="network")
        self.sync.connected()
        wait_until(lambda: self.sync.last_error is not None)
        self.assertFalse(self.sync.ready)
        self.apply()
        self.http.write_profile.side_effect = None
        wait_until(lambda: self.sync.ready)
        self.assertFalse(self.backend.calls)

    def test_backend_rejection_and_persistence_failure_keep_old_revision(self):
        old = ActiveProfile(validate_snapshot(draft(), node_id="node-1"), 3)
        self.backend.active["node-1"] = old
        for outcome in ("rejected", "sync_failed"):
            self.backend.apply_profile = Mock(return_value=ProfileApplyResult(outcome, old, "Not applied"))
            self.apply()
            wait_until(lambda: self.sync.last_status.startswith(outcome.upper()))
            self.assertIs(self.backend.active["node-1"], old)
            self.assertIn("rev=3", self.sync.last_status)

    def test_wrong_backend_identity_is_never_reported_as_success(self):
        other = ActiveProfile(validate_snapshot(draft("node-2"), node_id="node-2"), 1)
        self.backend.apply_profile = Mock(return_value=ProfileApplyResult("applied", other))
        self.apply()
        wait_until(lambda: "SYNC_FAILED" in self.sync.last_status)
        self.assertNotIn("APPLIED", self.sync.last_status)

    def test_disconnect_during_backend_commit_reconciles_committed_state(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        original = self.backend.apply_profile

        def commit(profile):
            entered.set()
            release.wait(2)
            return original(profile)

        self.backend.apply_profile = commit
        self.apply()
        self.assertTrue(entered.wait(1))
        self.sync.disconnected()
        self.sync.connected()
        release.set()
        wait_until(lambda: self.sync.ready)
        self.assertIn("ACTIVE:", self.sync.last_status)
        self.assertIn("rev=1", self.sync.last_status)
        self.assertEqual(len(self.backend.calls), 1)

    def test_failed_preset_read_can_be_retried_with_same_selection(self):
        self.http.read_profile_snapshot.side_effect = CloudError("Timed out", category="timeout")
        self.sync.receive(BlynkTopics.PROFILE_PRESET, "1")
        wait_until(lambda: "SYNC_FAILED" in self.sync.last_status)
        self.http.read_profile_snapshot.side_effect = None
        self.http.read_profile_snapshot.return_value = draft(v16=1)
        self.sync.receive(BlynkTopics.PROFILE_PRESET, "1")
        wait_until(lambda: "DRAFT: Illustrative" in self.sync.last_status)
        self.assertFalse(self.backend.calls)
