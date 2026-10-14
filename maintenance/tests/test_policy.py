from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from maintenance.policy import evaluate_trial, evaluate_app, release_reasons, verify_capture
from maintenance.notify import send
from maintenance.transport import Transport, DeviceError
from maintenance.device import Device


def trial(**changes):
    result = dict(capture_verified=True, early_window_covered=True, review_complete=True,
                  home_loaded=True, crashed=False, persistent_blank=False, ad_visible=False,
                  rule_hit=True, loaded_version_verified=True)
    result.update(changes)
    return result


class PolicyTests(unittest.TestCase):
    def test_effective_requires_runtime(self):
        self.assertEqual(evaluate_trial(trial()), "verified_effective")
        self.assertEqual(evaluate_trial(trial(loaded_version_verified=False)), "no_ad_observed")

    def test_no_ad_is_not_script_success(self):
        self.assertEqual(evaluate_trial(trial(rule_hit=False)), "no_ad_observed")

    def test_locked_offline_and_unreviewed_fail_closed(self):
        for field in ("capture_verified", "early_window_covered", "review_complete", "home_loaded"):
            with self.subTest(field=field):
                self.assertEqual(evaluate_trial(trial(**{field: False})), "unable_to_test")

    def test_white_screen_and_crash_not_effective(self):
        self.assertEqual(evaluate_trial(trial(crashed=True)), "unable_to_test")
        self.assertEqual(evaluate_trial(trial(persistent_blank=True)), "unable_to_test")

    def test_two_of_three_reproduced_trigger_repair(self):
        r = evaluate_app([trial(ad_visible=True), trial(ad_visible=True), trial()])
        self.assertTrue(r["repair"])

    def test_one_reproduction_only_fluctuation(self):
        r = evaluate_app([trial(ad_visible=True), trial(), trial()])
        self.assertEqual(r["result"], "ad_fluctuation")
        self.assertFalse(r["repair"])

    def test_incomplete_trials_cannot_pass(self):
        self.assertEqual(evaluate_app([trial()])["result"], "unable_to_test")

    def test_timeout_does_not_trigger_repair(self):
        r = evaluate_app([trial(capture_verified=False)] * 3)
        self.assertFalse(r["repair"])

    def evidence(self):
        return dict(tree_sha256="hash", unit_tests_passed=True, har_regression_passed=True,
                    rollback_verified=True, config_restored=True, runtime_version_verified=True,
                    source="physical_device", candidate_attempt=1, changed_app="netease",
                    apps={app: [trial(), trial(), trial()] for app in ("netease", "jd", "taobao")})

    def test_release_all_gates_required(self):
        self.assertEqual(release_reasons(self.evidence(), "hash"), [])
        for gate in ("unit_tests_passed", "har_regression_passed", "rollback_verified", "config_restored", "runtime_version_verified"):
            with self.subTest(gate=gate):
                evidence = self.evidence(); evidence[gate] = False
                self.assertIn(gate, release_reasons(evidence, "hash"))

    def test_candidate_change_invalidates_release(self):
        self.assertIn("candidate_tree_changed", release_reasons(self.evidence(), "different"))

    def test_static_check_not_physical(self):
        evidence = self.evidence(); evidence["source"] = "static"
        self.assertIn("not_physical_device", release_reasons(evidence, "hash"))

    def test_attempts_are_capped(self):
        evidence = self.evidence(); evidence["candidate_attempt"] = 3
        self.assertIn("attempt_limit", release_reasons(evidence, "hash"))

    def test_clean_baseline_cannot_prove_candidate(self):
        evidence = self.evidence(); evidence["apps"]["netease"] = [trial(rule_hit=False)] * 3
        self.assertIn("netease:no_proven_candidate_effect", release_reasons(evidence, "hash"))

    def test_other_app_regression_blocks_release(self):
        evidence = self.evidence(); evidence["apps"]["jd"][0]["crashed"] = True
        self.assertIn("jd:unable_to_test", release_reasons(evidence, "hash"))

    def test_missing_telegram_not_delivered(self):
        with patch("maintenance.notify.credentials", return_value=None):
            self.assertEqual(send(Path("."), "test"), {"delivered": False, "reason": "telegram_credentials_missing"})

    def test_telegram_error_does_not_leak_token(self):
        with patch("maintenance.notify.credentials", return_value=("secret-value", "123")), \
             patch("urllib.request.urlopen", side_effect=OSError("secret-value")):
            self.assertNotIn("secret-value", json.dumps(send(Path("."), "test")))

    def test_wrapper_timeout_not_success(self):
        with patch("subprocess.run", side_effect=__import__("subprocess").TimeoutExpired("ssh", 1)):
            with self.assertRaises(DeviceError):
                Transport().execute("echo test")

    def test_nonzero_remote_exit_is_error(self):
        fake = __import__("subprocess").CompletedProcess([], 1, json.dumps({"success": False, "exit_code": 7}), "")
        with patch("subprocess.run", return_value=fake):
            with self.assertRaises(DeviceError):
                Transport().execute("false")

    def test_incident_quarantine_blocks_before_remote_action(self):
        transport = __import__('unittest.mock', fromlist=['Mock']).Mock()
        with patch('pathlib.Path.exists', return_value=True):
            with self.assertRaisesRegex(DeviceError, '隔离'):
                with Device(transport).observe():
                    self.fail('quarantined observer executed')
        transport.execute.assert_not_called()

    def test_frame_hash_and_early_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder); payload = b"\xff\xd8fixture"; (p / "frame.jpg").write_bytes(payload)
            frames = [{"file": "frame.jpg", "sha256": hashlib.sha256(payload).hexdigest(), "timestamp": 100 + n * .5,
                       "applicationState": 0} for n in range(30)]
            capture = {"frames": frames, "started": 100}
            (p / "capture.json").write_text(json.dumps(capture))
            self.assertEqual(len(verify_capture(p)["frames"]), 30)
            capture["started"] = 98; (p / "capture.json").write_text(json.dumps(capture))
            with self.assertRaisesRegex(ValueError, "early_window_missing"):
                verify_capture(p)
            capture["started"] = 100; (p / "frame.jpg").write_bytes(b"tampered")
            (p / "capture.json").write_text(json.dumps(capture))
            with self.assertRaisesRegex(ValueError, "frame_hash_mismatch"):
                verify_capture(p)


if __name__ == "__main__":
    unittest.main()
