"""构建本机原生截图工具和 private 配置，不安装全局依赖、不打印设备身份。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .native_capture import ROOT, digest, save, source_digest


def main():
    tool = ROOT / "private/tools/go-ios-v1.3.2"
    manifest = json.loads((tool / "manifest.json").read_text(encoding="utf-8"))
    ios = Path(manifest["executable"])
    if digest(ios) != manifest["executable_sha256"]:
        raise RuntimeError("go-ios hash mismatch")
    go = shutil.which("go")
    if not go:
        raise RuntimeError("Go missing; use the approved local tool manager before building")
    helper = ROOT / "private/tools/native-capture.exe"
    env = dict(os.environ, GOMODCACHE=str(ROOT / "private/tools/go-cache/mod"),
               GOCACHE=str(ROOT / "private/tools/go-cache/build"))
    runs = []
    def run(argv, cwd=ROOT):
        p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=120)
        runs.append({"command":argv, "stdout":p.stdout.decode("utf-8",errors="replace"),
                     "stderr":p.stderr.decode("utf-8",errors="replace"), "exit_status":p.returncode})
        if p.returncode:
            raise RuntimeError("native build/check failed; see private build ledger")
        return p.stdout
    ledger = ROOT / "private/runs/native-build" / (str(time.time_ns()) + ".json")
    try:
        folder = ROOT / "maintenance/native-capture"
        run([go, "test", "./..."], folder)
        run([go, "build", "-o", str(helper), "."], folder)
        devices = json.loads(run([str(ios), "list"]))["deviceList"]
        if len(devices) != 1:
            raise RuntimeError("Expected exactly one USB device; refusing ambiguous identity")
        private_env = dict(env, GO_IOS_UDID=devices[0])
        p = subprocess.run([str(ios), "info"], env=private_env, capture_output=True, timeout=20)
        if p.returncode or json.loads(p.stdout).get("ProductType") != "iPhone12,8":
            raise RuntimeError("Expected SE2 identity not confirmed")
        config = {"udid":devices[0], "ios":str(ios), "ios_sha256":digest(ios),
                  "helper":str(helper), "helper_sha256":digest(helper), "helper_source_sha256":source_digest()}
        path = ROOT / "private/native-device.json"
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("udid") != config["udid"]:
                raise RuntimeError("Existing target differs; refusing to rebind")
            shutil.copyfile(path, path.with_name("native-device-backup-" + str(time.time_ns()) + ".json"))
        save(path, config)
    finally:
        save(ledger, runs)
    print(json.dumps({"built":True, "tool_hash_verified":True, "target_identity_verified":True,
                      "config":str(path), "build_ledger":str(ledger)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
