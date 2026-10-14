"""不依赖 Frida/WDA 的 USB 截图采集；画面判断由看图 Agent 留下逐帧证据。"""
import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from .device import APPS, Device
from .transport import DeviceError, Transport

ROOT = Path(__file__).resolve().parents[1]
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_digest(root=ROOT):
    folder = root / "maintenance/native-capture"
    value = hashlib.sha256()
    for p in sorted(folder.iterdir()):
        if p.suffix == ".go" or p.name in ("go.mod", "go.sum"):
            value.update(p.name.encode() + b"\0" + p.read_bytes())
    return value.hexdigest()


def load_config(root=ROOT):
    try:
        config = json.loads((root / "private/native-device.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeviceError("native_tool_config_missing_or_invalid") from exc
    if not isinstance(config.get("udid"), str) or not config["udid"]:
        raise DeviceError("native_device_identity_missing")
    for key in ("ios", "helper"):
        p = Path(config.get(key, ""))
        if not p.is_file() or not p.resolve().is_relative_to((root / "private/tools").resolve()):
            raise DeviceError("native_tool_outside_private_tools")
        if digest(p) != config.get(key + "_sha256"):
            raise DeviceError("native_tool_hash_mismatch")
    if source_digest(root) != config.get("helper_source_sha256"):
        raise DeviceError("native_helper_rebuild_required")
    return config


def approve_preflight(directory, event, max_age=45):
    """不凭缓存或退出码断言解锁；需要 Agent 实际看图并绑定本轮截图。"""
    path = Path(directory) / "unlock-review.json"
    if not path.exists():
        return False
    proof = json.loads(path.read_text(encoding="utf-8"))
    if (proof.get("source") != "visual_screen_review" or proof.get("unlocked") is not True or
            proof.get("preflight_sha256") != event["sha256"] or
            digest(Path(directory) / event["file"]) != event["sha256"]):
        raise DeviceError("native_unlock_review_invalid")
    age = time.time() - event["timestamp"]
    if age < -2 or age > max_age:
        raise DeviceError("native_unlock_review_stale")
    return True


class NativeDevice:
    def __init__(self, transport, root=ROOT):
        self.root = root
        self.ssh = transport
        self.config = load_config(root)
        # 仅复用没有 Frida 副作用的本机充电状态和 SQLite 只读方法。
        self.readonly = Device(transport)
        self.port = None
        self.installed = None
        self.ledger = []

    def command(self, args, timeout=30):
        env = dict(os.environ, GO_IOS_UDID=self.config["udid"])
        p = subprocess.run([self.config["ios"]] + args, capture_output=True, timeout=timeout,
                           env=env, creationflags=FLAGS)
        self.ledger.append({"command": [self.config["ios"]] + args, "exit_status": p.returncode,
                            "stdout": p.stdout.decode("utf-8", errors="replace"),
                            "stderr": p.stderr.decode("utf-8", errors="replace")})
        if p.returncode:
            raise DeviceError("native_command_failed")
        return json.loads(p.stdout)

    @contextmanager
    def observe(self, directory):
        directory = Path(directory).resolve()
        if not directory.is_relative_to((self.root / "private").resolve()):
            raise DeviceError("native_output_must_be_private")
        directory.mkdir(parents=True, exist_ok=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        pair = self.root / "private/tools/go-ios-pair-records"
        pair.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, GO_IOS_UDID=self.config["udid"])
        proc = None
        with (directory / "tunnel-private.log").open("wb") as log:
            try:
                proc = subprocess.Popen([self.config["ios"], "tunnel", "start", "--userspace",
                    "--pair-record-path=" + str(pair), "--tunnel-info-port=" + str(self.port)],
                    env=env, stdout=log, stderr=log, creationflags=FLAGS)
                deadline = time.monotonic() + 20
                ready = False
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise DeviceError("native_tunnel_exited")
                    try:
                        listing = self.command(["tunnel", "ls", "--tunnel-info-port=" + str(self.port)], timeout=3)
                        ready = any(t.get("Udid", t.get("udid")) == self.config["udid"] for t in listing)
                    except (DeviceError, OSError, ValueError, subprocess.TimeoutExpired):
                        ready = False
                    if ready:
                        break
                    time.sleep(.3)
                if not ready:
                    raise DeviceError("native_tunnel_timeout")
                yield self
            finally:
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                save(directory / "native-commands-private.json", self.ledger)
                self.port = None

    def charge(self):
        return self.readonly.charge()

    def records(self):
        return self.readonly.records()

    def metadata(self):
        entries = self.command(["apps"])
        processes = self.ssh.execute("ps -A -o pid=,comm=", sensitive=True)
        process_map = {}
        for line in processes.splitlines():
            fields = line.strip().split(None, 1)
            if len(fields) == 2 and fields[0].isdigit():
                process_map.setdefault(fields[1].rsplit("/", 1)[-1], []).append(int(fields[0]))
        wanted = {b for _, b in APPS} | {"com.ruikq.decar"}
        self.installed = {}
        for a in entries:
            bundle = a.get("CFBundleIdentifier")
            if bundle not in wanted:
                continue
            exe = a.get("CFBundleExecutable", "")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", exe):
                raise DeviceError("unsafe_app_executable")
            self.installed[bundle] = {"bundle_id": bundle, "executable": exe,
                "version": a.get("CFBundleShortVersionString"), "build": a.get("CFBundleVersion"),
                "pid": next(iter(process_map.get(exe, [])), None)}
        if set(self.installed) != wanted:
            raise DeviceError("required_apps_missing")
        return list(self.installed.values())

    def cold_stop(self, bundle):
        if self.installed is None:
            self.metadata()
        exe = self.installed[bundle]["executable"]
        def pids():
            result = []
            for line in self.ssh.execute("ps -A -o pid=,comm=", sensitive=True).splitlines():
                fields = line.strip().split(None, 1)
                if len(fields) == 2 and fields[0].isdigit() and fields[1].rsplit("/", 1)[-1] == exe:
                    result.append(int(fields[0]))
            return result
        found = pids()
        if len(found) > 1:
            raise DeviceError("target_process_not_unique")
        if found:
            if found[0] <= 1:
                raise DeviceError("invalid_target_pid")
            self.ssh.execute("kill -9 " + str(found[0]))
        if pids():
            raise DeviceError("target_not_cold")
        return found

    def capture(self, bundle, directory, probe=False, review_timeout=45):
        if self.port is None:
            raise DeviceError("native_tunnel_not_open")
        directory = Path(directory).resolve()
        if not directory.is_relative_to((self.root / "private").resolve()):
            raise DeviceError("native_output_must_be_private")
        directory.mkdir(parents=True, exist_ok=False)
        manifest = {"backend": "native-instruments", "source": "physical_device", "bundle_id": bundle,
                    "started": time.time(), "frames": [], "errors": [], "script_records": [],
                    "foreground_evidence": "requires_bound_visual_review", "probe_only": probe}
        pending = queue.Queue()
        proc = None
        stream_error = directory / "helper-stderr-private.log"
        try:
            with stream_error.open("wb") as err:
                proc = subprocess.Popen([self.config["helper"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=err, creationflags=FLAGS)
                config = {"udid": self.config["udid"], "directory": str(directory), "tunnel_port": self.port,
                          "duration": 3 if probe else 20, "interval": .25, "review_gate": not probe}
                proc.stdin.write((json.dumps(config) + "\n").encode())
                proc.stdin.flush()
                def read():
                    try:
                        for line in proc.stdout:
                            pending.put(json.loads(line))
                    except (OSError, ValueError):
                        pending.put({"event": "error", "reason": "native_stream_invalid"})
                    finally:
                        pending.put({"event": "eof"})
                reader = threading.Thread(target=read, daemon=True)
                reader.start()
                deadline = time.monotonic() + 90
                with ThreadPoolExecutor(max_workers=1) as pool:
                    launch = None
                    while time.monotonic() < deadline:
                        try:
                            event = pending.get(timeout=1)
                        except queue.Empty:
                            if proc.poll() is not None:
                                break
                            continue
                        kind = event.get("event")
                        if kind == "preflight":
                            save(directory / "preflight.json", event)
                            print(json.dumps({"event": "awaiting_unlock_visual_review", "directory": str(directory),
                                "image": str(directory / event["file"]), "sha256": event["sha256"]}), flush=True)
                            wait_until = time.monotonic() + review_timeout
                            while not approve_preflight(directory, event):
                                if time.monotonic() >= wait_until:
                                    raise DeviceError("native_unlock_visual_review_required")
                                time.sleep(.2)
                            # 只有确认当前画面已解锁之后，才允许终止目标进程和启动 App。
                            manifest["preflight_sha256"] = event["sha256"]
                            manifest["stopped_pids"] = self.cold_stop(bundle)
                            before = self.records()
                            manifest["watermark"] = max((int(r["id"]) for r in before), default=0)
                            proc.stdin.write(b'{"action":"capture"}\n')
                            proc.stdin.flush()
                        elif kind == "frame":
                            event.pop("event", None)
                            manifest["frames"].append(event)
                            if not probe and launch is None:
                                manifest["resumed"] = time.time()
                                manifest["started"] = manifest["resumed"]
                                def start():
                                    self.ssh.execute("uiopen --bundleid " + shlex.quote(bundle))
                                    return time.time()
                                launch = pool.submit(start)
                        elif kind == "finished":
                            manifest["helper_finished"] = True
                            break
                        elif kind in ("error", "eof"):
                            if kind == "error":
                                raise DeviceError(event.get("reason", "native_capture_failed"))
                            break
                    else:
                        raise DeviceError("native_capture_timeout")
                    if launch is not None:
                        manifest["launch_completed"] = launch.result(timeout=5)
                proc.wait(timeout=5)
                manifest["helper_exit_status"] = proc.returncode
                if proc.returncode or not manifest.get("helper_finished") or not manifest["frames"]:
                    raise DeviceError("native_capture_incomplete")
                if not probe:
                    manifest["script_records"] = [r for r in self.records() if int(r["id"]) > manifest["watermark"]]
        except Exception as exc:
            detail = "screen_unavailable_black_frame" if stream_error.exists() and "screen_unavailable_black_frame" in stream_error.read_text(encoding="utf-8", errors="replace") else type(exc).__name__ + ": " + str(exc)[:180]
            manifest["errors"].append(detail)
            raise DeviceError(detail) from exc
        finally:
            if proc is not None:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()
            manifest["finished"] = time.time()
            manifest["own_helper_closed"] = proc is None or proc.poll() is not None
            save(directory / "capture.json", manifest)
        return manifest


def main():
    from .daily import run_lock, source_hash
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("probe", "capture"))
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--app", choices=("all", "netease", "jd", "taobao"), default="all")
    args = parser.parse_args()
    destination = args.run_dir.resolve()
    if not destination.is_relative_to((ROOT / "private").resolve()):
        parser.error("run-dir must remain inside ignored private/")
    destination.mkdir(parents=True, exist_ok=False)
    events = []
    report = {"source": "physical_device", "backend": "native-instruments", "apps": {}, "started": time.time(), "tree_sha256": source_hash()}
    try:
        with run_lock():
            device = NativeDevice(Transport(ledger=events.append))
            report["charge"] = device.charge()
            with device.observe(destination):
                if args.action == "probe":
                    device.capture(None, destination / "probe", probe=True)
                    report["screen_capture_available"] = True
                    report["unlock_state"] = "requires_visual_review"
                else:
                    report["metadata"] = device.metadata()
                    if not any(a["bundle_id"] == "com.ruikq.decar" and a["pid"] for a in report["metadata"]):
                        raise DeviceError("loon_not_running")
                    for app, bundle in APPS:
                        if args.app not in ("all", app):
                            continue
                        report["apps"][app] = []
                        for number in range(1, 4):
                            folder = destination / app / str(number)
                            trial = {"directory": str(folder.relative_to(destination)), "capture_verified": False, "review_complete": False}
                            try:
                                device.capture(bundle, folder)
                                trial["manifest_sha256"] = digest(folder / "capture.json")
                                trial["blocker"] = "native_frame_visual_review_required"
                            except DeviceError as exc:
                                trial["blocker"] = str(exc)
                                report["apps"][app].append(trial)
                                raise
                            report["apps"][app].append(trial)
                            save(destination / "capture-summary.json", report)
    except Exception as exc:
        report["blocker"] = type(exc).__name__ + ": " + str(exc)[:180]
    finally:
        report["finished"] = time.time()
        save(destination / "commands-private.json", events)
        save(destination / "capture-summary.json", report)
    print(json.dumps({"directory": str(destination), "blocker": report.get("blocker"), "review_required": True}))
    return 2 if report.get("blocker") else 0


if __name__ == "__main__":
    sys.exit(main())
