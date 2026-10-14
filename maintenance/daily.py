"""本地证据驱动的维护入口；人工/Agent 看图审核后才允许判定及发布。"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

from .device import APPS, Device
from .notify import credentials, send
from .policy import evaluate_app, release_reasons, sha256, verify_capture
from .transport import DeviceError, Transport

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "private"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def command(argv, cwd=ROOT, timeout=60):
    p = subprocess.run(argv, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return {"command": argv, "stdout": p.stdout, "stderr": p.stderr, "exit_status": p.returncode}


def source_hash():
    import hashlib
    result = command(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    if result["exit_status"]:
        raise ValueError("git_inventory_failed")
    h = hashlib.sha256()
    for name in sorted(result["stdout"].split("\0")):
        if not name:
            continue
        if name.startswith("private/") or name.lower().endswith((".har", ".jpg", ".png", ".p12", ".pem", ".env")):
            raise ValueError("private_file_in_public_tree")
        path = ROOT / name
        if path.is_file():
            h.update(name.encode() + b"\0" + path.read_bytes())
    return h.hexdigest()


@contextmanager
def run_lock():
    PRIVATE.mkdir(exist_ok=True)
    with (PRIVATE / "daily.lock").open("a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            if stream.read(1) == b"":
                stream.write(b"0"); stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise DeviceError("another_run_active") from exc
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def check_sources():
    registry = json.loads((ROOT / "maintenance/plugins.json").read_text(encoding="utf-8"))
    import hashlib
    outcomes = {}
    for app, spec in registry.items():
        try:
            with urllib.request.urlopen(spec["url"], timeout=10) as response:
                data = response.read(2_000_001)
            if len(data) > 2_000_000 or b"[" not in data or b"#!name=" not in data:
                raise ValueError("invalid_plugin_payload")
            outcomes[app] = {"downloaded": True, "sha256": hashlib.sha256(data).hexdigest(),
                             "ad_effect_verified": False}
        except (OSError, ValueError):
            outcomes[app] = {"downloaded": False, "ad_effect_verified": False}
    return outcomes


def preflight(destination):
    events = []
    d = Device(Transport(ledger=events.append))
    result = {"timestamp": time.time(), "telegram_configured": credentials(ROOT) is not None,
              "source_checks": check_sources(), "hardware_ready": False, "candidate_loading_verified": False}
    try:
        result["charge"] = d.charge()
        with d.observe():
            result["locked"] = d.locked()
            result["apps"] = d.metadata()
            result["loon_running"] = any(a["bundle_id"] == "com.ruikq.decar" and a["pid"] for a in result["apps"])
            if result["locked"]:
                raise DeviceError("device_locked")
            if not result["loon_running"]:
                raise DeviceError("loon_not_running")
            result["script_record_count"] = len(d.records())
            result["hardware_ready"] = True
    except Exception as exc:
        result["blocker"] = type(exc).__name__ + ": " + str(exc)[:150]
    finally:
        write_json(destination / "commands.json", events)
        write_json(destination / "preflight.json", result)
    return result


def capture(destination):
    events, report = [], {"source": "physical_device", "started": time.time(), "apps": {}}
    d = Device(Transport(ledger=events.append))
    try:
        report["charge"] = d.charge()
        report["tree_sha256"] = source_hash()
        with d.observe():
            if d.locked():
                raise DeviceError("device_locked")
            report["metadata"] = d.metadata()
            if not any(a["bundle_id"] == "com.ruikq.decar" and a["pid"] for a in report["metadata"]):
                raise DeviceError("loon_not_running")
            for app, bundle in APPS:
                report["apps"][app] = []
                for number in range(1, 4):
                    trial_dir = destination / app / str(number)
                    trial = {"directory": str(trial_dir.relative_to(destination)), "review_complete": False}
                    try:
                        d.capture(bundle, trial_dir)
                        verify_capture(trial_dir)
                        trial["capture_verified"] = True
                        trial["manifest_sha256"] = sha256(trial_dir / "capture.json")
                    except Exception as exc:
                        trial["capture_verified"] = False
                        trial["blocker"] = type(exc).__name__ + ": " + str(exc)[:150]
                    report["apps"][app].append(trial)
                    write_json(destination / "capture-summary.json", report)
    except Exception as exc:
        report["blocker"] = type(exc).__name__ + ": " + str(exc)[:150]
    finally:
        report["finished"] = time.time()
        write_json(destination / "commands.json", events)
        write_json(destination / "capture-summary.json", report)
    return report


def finalize(destination, review):
    captured = json.loads((destination / "capture-summary.json").read_text(encoding="utf-8"))
    outcome = {"source": captured["source"], "tree_sha256": captured.get("tree_sha256"), "apps": {}, "finished": time.time()}
    for app, _ in APPS:
        trials = []
        for raw in captured.get("apps", {}).get(app, []):
            entry = dict(raw)
            matching = next((r for r in review.get("apps", {}).get(app, []) if r.get("directory") == raw["directory"]), {})
            folder = destination / raw["directory"]
            native_manifest = folder / "capture.json"
            native = (native_manifest.is_file() and
                      json.loads(native_manifest.read_text(encoding="utf-8")).get("backend") == "native-instruments")
            if raw.get("capture_verified") or (native and matching):
                folder = destination / raw["directory"]
                evidence = verify_capture(folder)
                digest = sha256(folder / "capture.json")
                if digest != matching.get("manifest_sha256"):
                    raise ValueError("review_not_bound_to_capture")
                entry.update(matching)
                # 审核记录不能覆盖底层采集验证；原生路径必须先完成逐帧前台证明。
                entry["capture_verified"] = True
                # 单靠标签或审核声明不证明版本；还需要本地保存的运行版本证据。
                runtime = folder / "runtime-version.json"
                entry["loaded_version_verified"] = False
                if runtime.exists():
                    proof = json.loads(runtime.read_text(encoding="utf-8"))
                    entry["loaded_version_verified"] = (proof.get("source") == "loon_execution_evidence" and
                        proof.get("capture_manifest_sha256") == digest and proof.get("tree_sha256") == captured.get("tree_sha256"))
                entry["rule_hit"] = matching.get("rule_hit") is True and bool(evidence.get("script_records"))
            trials.append(entry)
        outcome["apps"][app] = {"trials": trials, **evaluate_app(trials)}
    outcome["blocker"] = captured.get("blocker")
    write_json(destination / "result.json", outcome)
    return outcome


def notification(report):
    names = {"netease": "网易云", "jd": "京东", "taobao": "淘宝"}
    states = {"verified_effective": "已验证有效", "ad_reproduced": "广告复现", "no_ad_observed": "未观察到广告投放",
              "unable_to_test": "无法完成测试", "ad_fluctuation": "广告偶发，待复核"}
    lines = ["SE2 / Loon 每日测试"]
    for app in names:
        state = report.get("apps", {}).get(app, {}).get("result", "unable_to_test")
        lines.append(names[app] + "：" + states[state])
    if report.get("blocker"):
        lines.append("有执行阻塞，未发布修复；请查看本地证据。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preflight", "capture", "review", "notify", "test-notify", "release-check", "hash"))
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    PRIVATE.mkdir(exist_ok=True)
    destination = (args.run_dir or PRIVATE / "runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")).resolve()
    if not destination.is_relative_to(PRIVATE.resolve()):
        parser.error("run-dir must remain inside ignored private/")
    if args.action == "hash":
        print(source_hash()); return 0
    if args.action == "test-notify":
        result = send(ROOT, "SE2 / Loon 通知链路验收测试：仅验证送达，不代表去广告已通过。")
        write_json(PRIVATE / "telegram-test.json", result); print(json.dumps(result)); return 0 if result["delivered"] else 2
    if args.action == "release-check":
        if not args.evidence:
            parser.error("release-check requires --evidence")
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        reasons = release_reasons(evidence, source_hash())
        print(json.dumps({"allowed": not reasons, "reasons": reasons}, ensure_ascii=False)); return 2 if reasons else 0
    if args.action in ("preflight", "capture"):
        with run_lock():
            destination.mkdir(parents=True, exist_ok=True)
            if args.action == "preflight":
                report = preflight(destination)
                print(json.dumps(report, ensure_ascii=False)); return 0 if report["hardware_ready"] else 2
            # 在独立子进程限时执行，RPC 阻塞不能突破 30 分钟预算。
            if os.environ.get("LOON_CAPTURE_CHILD") != "1":
                env = dict(os.environ, LOON_CAPTURE_CHILD="1")
                # 父进程已持有锁；子进程不重复争抢同一锁。
                proc = subprocess.Popen([sys.executable, "-m", "maintenance.daily", "capture", "--run-dir", str(destination)],
                                        cwd=ROOT, env=env)
                try:
                    return proc.wait(timeout=1740)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
                    write_json(destination / "capture-summary.json", {"source": "physical_device", "blocker": "capture_timeout_cleanup_unconfirmed", "apps": {}})
                    return 2
            report = capture(destination)
            print(json.dumps({"run_dir": str(destination), "blocker": report.get("blocker"), "review_required": True}, ensure_ascii=False))
            return 2 if report.get("blocker") else 0
    if args.action == "review":
        if not args.review:
            parser.error("review requires --review")
        report = finalize(destination, json.loads(args.review.read_text(encoding="utf-8")))
        print(notification(report)); return 2 if any(a["result"] == "unable_to_test" for a in report["apps"].values()) else 0
    report_path = destination / "result.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {"blocker": "no_reviewed_result"}
    result = send(ROOT, notification(report))
    write_json(destination / "notification.json", result)
    print(json.dumps(result)); return 0 if result["delivered"] else 2


if __name__ == "__main__":
    # 只有父进程持锁；子进程同样执行真实采集，不返回模拟成功。
    if os.environ.get("LOON_CAPTURE_CHILD") == "1":
        from contextlib import nullcontext
        run_lock = nullcontext
    sys.exit(main())
