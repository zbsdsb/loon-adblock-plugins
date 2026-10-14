from contextlib import contextmanager
from pathlib import Path
import base64
import hashlib
import json
import plistlib
import re
import shlex
import time
from concurrent.futures import ThreadPoolExecutor

from .transport import DeviceError, Transport

APPS = [("netease", "com.netease.cloudmusic"), ("jd", "com.360buy.jdmobile"), ("taobao", "com.taobao.taobao4iphone")]
LOON_GROUP = "/var/mobile/Containers/Shared/AppGroup/58636B54-1A26-4BC3-A2C3-3C1080DCB894"
SERVER_DIR = "/var/mobile/loon-maintenance"


def observer_source():
    # frida-tools 携带与当前 Frida 兼容的 ObjC bridge，避免安装/升级全局依赖。
    import frida_tools
    from frida_tools.repl import REPLApplication
    raw = (Path(frida_tools.__file__).parent / "repl_agent.js").read_text(encoding="utf-8")
    bundle = REPLApplication._relocate_bundle(raw, "/maintenance/bridge")
    body = (Path(__file__).parent / "observer.js").read_text(encoding="utf-8")
    user = "Script.evaluate('/maintenance/observer.js', " + json.dumps(body) + ");"
    return "📦\n" + bundle[2:] + "\n✄\n" + str(len(user.encode())) + " /maintenance/user.js\n✄\n" + user


class Device:
    def __init__(self, transport):
        self.ssh = transport
        self.frida = None
        self.group = None

    @contextmanager
    def observe(self):
        policy_path = Path(__file__).parent / "device-policy.json"
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DeviceError("缺少有效的设备观察安全策略；禁止启动 Frida") from exc
        if policy.get("frida_enabled") is not True or policy.get("compatibility_verified") is not True:
            raise DeviceError("设备观察策略已隔离 Frida；未经兼容性验收不得启动")
        quarantine = Path(__file__).resolve().parents[1] / "private/FRIDA_DISABLED.json"
        if quarantine.exists():
            raise DeviceError("Frida 已因设备重启事故隔离；禁止自动重试，必须先完成独立兼容性与恢复审查")
        import frida
        # 如果设备上已有 Frida 服务，不能把它误当成自己的进程关闭。
        existing = [line.split()[0] for line in self.ssh.execute("ps -A -o pid,comm").splitlines()
                    if line.strip().endswith("/frida-server")]
        owned = None
        if not existing:
            result = self.ssh.execute(f"mkdir -p {SERVER_DIR}; chmod 700 {SERVER_DIR}; "
                f"nohup /var/jb/usr/sbin/frida-server -l 127.0.0.1:27042 >{SERVER_DIR}/frida.log 2>&1 </dev/null & pid=$!; disown; echo $pid")
            owned = result.strip()
            if not owned.isdecimal():
                raise DeviceError("未获得 Frida 服务 PID")
        try:
            with self.ssh.forward() as endpoint:
                self.frida = frida.get_device_manager().add_remote_device(endpoint)
                self.frida.enumerate_processes()
                yield self
        finally:
            self.frida = None
            if owned:
                # 清理错误必须留证，但不能覆盖最初的采集错误。
                try:
                    self.ssh.execute(f"kill {owned}", timeout=10)
                except DeviceError:
                    if self.ssh.ledger:
                        self.ssh.ledger({"cleanup_required": True, "owned_frida_pid": owned})

    @contextmanager
    def script(self, target):
        session = self.frida.attach(target)
        script = session.create_script(observer_source())
        messages = []
        script.on("message", lambda msg, data: messages.append(msg) if msg.get("type") == "error" else None)
        try:
            script.load()
            yield script.exports_sync
        finally:
            session.detach()

    def locked(self):
        proc = next((p for p in self.frida.enumerate_processes() if p.name == "SpringBoard"), None)
        if not proc:
            raise DeviceError("SpringBoard 不可见")
        with self.script(proc.pid) as api:
            return api.lockstate()["locked"]

    def charge(self):
        return json.loads(self.ssh.execute("curl -fsS --max-time 5 http://127.0.0.1:8769/status"))

    def records(self):
        if not self.group:
            matches = self.ssh.execute("find /var/mobile/Containers/Shared/AppGroup -maxdepth 3 -name loon_record.db").splitlines()
            if len(matches) != 1:
                raise DeviceError("无法唯一定位 Loon AppGroup；停止而非复用失效 UUID")
            self.group = str(Path(matches[0]).parent.parent).replace("\\", "/")
        db = shlex.quote(self.group + "/record/loon_record.db")
        sql = "SELECT id,scriptTag,isException,isFinished,startTime,endTime,scriptPath,pluginName FROM scriptRecords ORDER BY id DESC LIMIT 100;"
        return json.loads(self.ssh.execute(f"sqlite3 -readonly -json {db} {shlex.quote(sql)}", sensitive=True).strip() or "[]")

    def metadata(self):
        apps = self.frida.enumerate_applications(scope="full")
        wanted = {bundle for _, bundle in APPS} | {"com.ruikq.decar"}
        return [{"bundle_id": a.identifier, "name": a.name, "pid": a.pid,
                 "version": a.parameters.get("version"), "build": a.parameters.get("build"),
                 "path": a.parameters.get("path")} for a in apps if a.identifier in wanted]

    def capture(self, bundle, destination, duration=15, interval=.25):
        if self.locked():
            raise DeviceError("设备已锁屏；不解锁、不采集")
        application = next((a for a in self.frida.enumerate_applications() if a.identifier == bundle), None)
        if application and application.pid:
            self.frida.kill(application.pid)
        before = self.records()
        watermark = max((int(r["id"]) for r in before), default=0)
        started = time.time()
        # spawn gating 在入口执行前安装同一观察器；观察器不得改变广告逻辑。
        pid = self.frida.spawn(bundle)
        destination.mkdir(parents=True, exist_ok=True)
        frames, errors = [], []
        resumed = None
        try:
            with self.script(pid) as api:
                resumed = time.time()
                self.frida.resume(pid)
                deadline = time.monotonic() + duration
                # 不等待 SSH 启动调用返回才开始截图，否则会漏掉最初一秒的开屏广告。
                with ThreadPoolExecutor(max_workers=1) as pool:
                    launch = pool.submit(self.ssh.execute, "su mobile -c " + shlex.quote("uiopen --bundleid " + shlex.quote(bundle)))
                    while time.monotonic() < deadline:
                        tick = time.monotonic()
                        try:
                            result = api.snapshot()
                            data = base64.b64decode(result.pop("jpeg"))
                            if not data.startswith(b"\xff\xd8"):
                                raise DeviceError("截图不是 JPEG")
                            frame = destination / f"frame-{len(frames):03d}.jpg"
                            frame.write_bytes(data)
                            result.update(file=frame.name, sha256=hashlib.sha256(data).hexdigest(), timestamp=time.time())
                            frames.append(result)
                        except Exception as exc:
                            errors.append(type(exc).__name__ + ": " + str(exc)[:180])
                        time.sleep(max(0, interval - (time.monotonic() - tick)))
                    launch.result()
        finally:
            try:
                self.frida.kill(pid)
            except Exception:
                pass
        after = [r for r in self.records() if int(r["id"]) > watermark]
        result = {"bundle_id": bundle, "started": started, "resumed": resumed, "finished": time.time(), "frames": frames,
                  "errors": errors, "script_records": after, "watermark": watermark}
        (destination / "capture.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if not frames:
            raise DeviceError("未采集到任何有效开屏画面")
        return result
