"""判定与发布门：下载成功、进程退出 0、无广告投放都不能替代真机证据。"""
import hashlib
import json
from pathlib import Path

APPS = ("netease", "jd", "taobao")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evaluate_trial(trial):
    if not all(trial.get(k) is True for k in ("capture_verified", "early_window_covered", "review_complete", "home_loaded")):
        return "unable_to_test"
    if trial.get("crashed") is not False or trial.get("persistent_blank") is not False:
        return "unable_to_test"
    if trial.get("ad_visible") is True:
        return "ad_reproduced"
    if trial.get("ad_visible") is not False:
        return "unable_to_test"
    if trial.get("rule_hit") is True and trial.get("loaded_version_verified") is True:
        return "verified_effective"
    return "no_ad_observed"


def evaluate_app(trials):
    if len(trials) != 3:
        return {"result": "unable_to_test", "repair": False}
    states = [evaluate_trial(t) for t in trials]
    if states.count("ad_reproduced") >= 2:
        return {"result": "ad_reproduced", "repair": True}
    if "unable_to_test" in states:
        result = "unable_to_test"
    elif "ad_reproduced" in states:
        result = "ad_fluctuation"
    elif all(s == "verified_effective" for s in states):
        result = "verified_effective"
    else:
        result = "no_ad_observed"
    return {"result": result, "repair": False}


def release_reasons(evidence, current_tree):
    reasons = []
    if evidence.get("tree_sha256") != current_tree:
        reasons.append("candidate_tree_changed")
    for key in ("unit_tests_passed", "har_regression_passed", "rollback_verified", "config_restored", "runtime_version_verified"):
        if evidence.get(key) is not True:
            reasons.append(key)
    if evidence.get("source") != "physical_device":
        reasons.append("not_physical_device")
    if evidence.get("candidate_attempt", 99) not in (1, 2):
        reasons.append("attempt_limit")
    for app in APPS:
        trials = evidence.get("apps", {}).get(app, [])
        state = evaluate_app(trials)["result"]
        if state not in ("verified_effective", "no_ad_observed"):
            reasons.append(app + ":" + state)
        if app == evidence.get("changed_app") and state != "verified_effective":
            reasons.append(app + ":no_proven_candidate_effect")
    if evidence.get("changed_app") not in APPS:
        reasons.append("unknown_changed_app")
    return reasons


def verify_capture(directory):
    directory = Path(directory)
    data = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
    frames = data.get("frames", [])
    native = data.get("backend") == "native-instruments"
    if native and (data.get("source") != "physical_device" or data.get("errors") or
                   data.get("helper_exit_status") != 0 or data.get("helper_finished") is not True or
                   data.get("own_helper_closed") is not True or data.get("probe_only") is not False):
        raise ValueError("native_capture_incomplete")
    if len(frames) < 30:
        raise ValueError("insufficient_frames")
    for frame in frames:
        name = frame["file"]
        if Path(name).name != name or not name.endswith(".jpg"):
            raise ValueError("unsafe_frame_path")
        if sha256(directory / name) != frame["sha256"]:
            raise ValueError("frame_hash_mismatch")
        if native:
            if not (directory / name).read_bytes().startswith(b"\xff\xd8"):
                raise ValueError("native_frame_not_jpeg")
            if "applicationState" in frame:
                raise ValueError("native_foreground_state_must_not_be_fabricated")
        elif frame.get("applicationState") != 0:
            raise ValueError("application_not_foreground")
    # 启动时刻与画面间空窗超一秒，或者帧间空窗超一秒，不能证明没有短暂开屏广告。
    started = data.get("resumed", data["started"])
    if frames[0]["timestamp"] - started > 1:
        raise ValueError("early_window_missing")
    if frames[-1]["timestamp"] - started < 14:
        raise ValueError("capture_too_short")
    if any(b["timestamp"] - a["timestamp"] > 1 for a, b in zip(frames, frames[1:])):
        raise ValueError("frame_gap")
    if any(b["timestamp"] <= a["timestamp"] for a, b in zip(frames, frames[1:])):
        raise ValueError("frame_time_not_monotonic")
    if native:
        # 完整保留系统启动过渡画面，不给原生截图伪填 UIKit 的 applicationState。
        # 另存的逐帧看图记录必须与本轮 manifest 和每张图片逐一绑定。
        if frames[-1]["timestamp"] - data.get("launch_completed", float("inf")) < 14:
            raise ValueError("native_post_launch_window_too_short")
        review_file = directory / "native-ui-review.json"
        if not review_file.is_file():
            raise ValueError("native_ui_review_required")
        review = json.loads(review_file.read_text(encoding="utf-8"))
        if review.get("source") != "visual_screen_review" or review.get("manifest_sha256") != sha256(directory / "capture.json"):
            raise ValueError("native_ui_review_not_bound")
        annotations = review.get("frames", [])
        if len(annotations) != len(frames):
            raise ValueError("native_ui_review_incomplete")
        target_seen = False
        for frame, annotation in zip(frames, annotations):
            if annotation.get("file") != frame["file"] or annotation.get("sha256") != frame["sha256"]:
                raise ValueError("native_ui_review_frame_mismatch")
            state = annotation.get("state")
            if state == "target_app":
                target_seen = True
            elif state != "launch_transition" or target_seen or frame["timestamp"] - started > 2:
                raise ValueError("native_target_not_foreground")
        if not target_seen:
            raise ValueError("native_target_not_foreground")
    return data
