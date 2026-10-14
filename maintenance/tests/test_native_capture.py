"""原生观察器失败闭合与逐帧证明；这里的图片/时间是合成测试，不是真机验收。"""
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from maintenance.native_capture import approve_preflight, load_config, NativeDevice
from maintenance.policy import sha256, verify_capture
from maintenance.transport import DeviceError
from maintenance.daily import check_sources


class NativeTests(unittest.TestCase):
    def fixture(self, folder):
        p = Path(folder)
        frames = []
        for n in range(61):
            file = p / ("frame-%03d.jpg" % n)
            file.write_bytes(b"\xff\xd8synthetic_fixture_" + str(n).encode())
            frames.append({"file": file.name, "sha256": sha256(file), "timestamp": 100 + .25 * n})
        capture = {"backend":"native-instruments", "source":"physical_device", "started":100,
            "launch_completed":100.5, "frames":frames, "errors":[], "helper_exit_status":0,
            "helper_finished":True, "own_helper_closed":True, "probe_only":False}
        (p / "capture.json").write_text(json.dumps(capture), encoding="utf-8")
        review = {"source":"visual_screen_review", "manifest_sha256":sha256(p / "capture.json"),
                  "frames":[dict(file=f["file"],sha256=f["sha256"],state="target_app") for f in frames]}
        (p / "native-ui-review.json").write_text(json.dumps(review), encoding="utf-8")
        return p, capture, review

    def test_missing_config_fails_before_device_use(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(DeviceError, "config_missing"):
                load_config(Path(folder))

    def test_bound_native_review_passes_without_fake_uikit_state(self):
        with tempfile.TemporaryDirectory() as folder:
            p, _, _ = self.fixture(folder)
            self.assertEqual(len(verify_capture(p)["frames"]), 61)

    def test_missing_native_review_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            p, _, _ = self.fixture(folder)
            (p / "native-ui-review.json").unlink()
            with self.assertRaisesRegex(ValueError, "native_ui_review_required"):
                verify_capture(p)

    def test_wrong_manifest_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            p, _, review = self.fixture(folder)
            review["manifest_sha256"] = "wrong"
            (p / "native-ui-review.json").write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError, "not_bound"):
                verify_capture(p)

    def test_lock_or_other_app_is_not_foreground(self):
        for state in ["locked", "other_app", "unknown", "black_screen"]:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as folder:
                p, _, review = self.fixture(folder)
                review["frames"][10]["state"] = state
                (p / "native-ui-review.json").write_text(json.dumps(review))
                with self.assertRaisesRegex(ValueError, "not_foreground"):
                    verify_capture(p)

    def test_transition_allowed_only_before_target_and_within_two_seconds(self):
        with tempfile.TemporaryDirectory() as folder:
            p, _, review = self.fixture(folder)
            review["frames"][0]["state"] = "launch_transition"
            (p / "native-ui-review.json").write_text(json.dumps(review))
            verify_capture(p)
            review["frames"][20]["state"] = "launch_transition"
            (p / "native-ui-review.json").write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError, "not_foreground"):
                verify_capture(p)

    def test_fabricated_application_state_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p, capture, _ = self.fixture(folder)
            capture["frames"][0]["applicationState"] = 0
            (p / "capture.json").write_text(json.dumps(capture))
            with self.assertRaisesRegex(ValueError, "fabricated"):
                verify_capture(p)

    def test_native_error_timeout_missing_frames_and_incomplete_reviews_fail(self):
        for field, value in [("errors",["timeout"]),("helper_exit_status",2),("helper_finished",False),("own_helper_closed",False),("probe_only",True)]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                p, capture, _ = self.fixture(folder)
                capture[field] = value
                (p / "capture.json").write_text(json.dumps(capture))
                with self.assertRaisesRegex(ValueError, "native_capture_incomplete"):
                    verify_capture(p)
        with tempfile.TemporaryDirectory() as folder:
            p, _, review = self.fixture(folder)
            review["frames"].pop()
            (p / "native-ui-review.json").write_text(json.dumps(review))
            with self.assertRaisesRegex(ValueError, "incomplete"):
                verify_capture(p)

    def test_unlock_review_is_bound_and_expires(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p / "preflight.jpg").write_bytes(b"fixture")
            event = {"file":"preflight.jpg", "sha256":sha256(p / "preflight.jpg"), "timestamp":time.time()}
            self.assertFalse(approve_preflight(p,event))
            proof = {"source":"visual_screen_review", "unlocked":True, "preflight_sha256":event["sha256"]}
            (p / "unlock-review.json").write_text(json.dumps(proof))
            self.assertTrue(approve_preflight(p,event))
            event["timestamp"] -= 60
            with self.assertRaisesRegex(DeviceError, "stale"):
                approve_preflight(p,event)

    def test_locked_review_never_authorizes_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p / "preflight.jpg").write_bytes(b"fixture")
            event = {"file":"preflight.jpg", "sha256":sha256(p / "preflight.jpg"), "timestamp":time.time()}
            (p / "unlock-review.json").write_text(json.dumps({"source":"visual_screen_review","unlocked":False,"preflight_sha256":event["sha256"]}))
            with self.assertRaisesRegex(DeviceError, "invalid"):
                approve_preflight(p,event)

    def test_cold_stop_matches_only_exact_app_executable(self):
        device = NativeDevice.__new__(NativeDevice)
        device.installed = {"bundle":{"executable":"TargetApp"}}
        device.ssh = Mock()
        device.ssh.execute.side_effect = ["321 /path/TargetApp\n322 /path/OtherTargetApp\n", "", "322 /path/OtherTargetApp\n"]
        self.assertEqual(device.cold_stop("bundle"),[321])
        self.assertEqual(device.ssh.execute.call_args_list[1].args,("kill -9 321",))

    def test_black_screen_before_review_never_stops_or_launches_apps(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            device = NativeDevice.__new__(NativeDevice)
            device.root = root
            device.config = {"helper":"fixture", "udid":"fixture"}
            device.port = 12345
            device.ssh = Mock()
            device.cold_stop = Mock()
            device.records = Mock()
            proc = Mock()
            proc.stdin = io.BytesIO()
            proc.stdout = io.BytesIO(b'{"event":"error","reason":"native_capture_failed"}\n')
            proc.poll.return_value = 2
            def spawn(*a, **kw):
                kw["stderr"].write(b"screen_unavailable_black_frame")
                kw["stderr"].flush()
                return proc
            with patch("maintenance.native_capture.subprocess.Popen",side_effect=spawn):
                with self.assertRaisesRegex(DeviceError,"screen_unavailable"):
                    device.capture("bundle",root / "private/trial")
            device.ssh.execute.assert_not_called()
            device.cold_stop.assert_not_called()
            device.records.assert_not_called()
            capture = json.loads((root / "private/trial/capture.json").read_text())
            self.assertTrue(capture["own_helper_closed"])
            self.assertEqual(capture["frames"],[])
            self.assertNotIn("resumed",capture)

    def test_capture_timeout_terminates_owned_helper_without_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            device = NativeDevice.__new__(NativeDevice)
            device.root = root
            device.config = {"helper":"fixture", "udid":"fixture"}
            device.port = 12345
            device.ssh = Mock()
            proc = Mock()
            proc.stdin = io.BytesIO()
            proc.stdout = io.BytesIO(b"")
            proc.poll.side_effect = [None,0]
            with patch("maintenance.native_capture.subprocess.Popen",return_value=proc), \
                 patch("maintenance.native_capture.time.monotonic",side_effect=[0,100]):
                with self.assertRaisesRegex(DeviceError,"native_capture_timeout"):
                    device.capture("bundle",root / "private/trial")
            proc.terminate.assert_called_once()
            device.ssh.execute.assert_not_called()

    def test_script_source_offline_does_not_report_success(self):
        with patch("maintenance.daily.urllib.request.urlopen",side_effect=OSError("offline")):
            result = check_sources()
        self.assertEqual(set(result),{"netease","jd","taobao"})
        self.assertTrue(all(v["downloaded"] is False and v["ad_effect_verified"] is False for v in result.values()))


if __name__ == "__main__":
    unittest.main()
