"""读取 Loon 配置/记录；不启动 App、不注入、不修改配置，不输出订阅凭据。"""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import plistlib
import shlex
import sys

from .transport import Transport


def read_bytes(ssh, path):
    return base64.b64decode(ssh.execute("base64 " + shlex.quote(path), sensitive=True), validate=False)


def config_summary(name, data):
    text = data.decode("utf-8-sig", errors="replace")
    section, plugins, mitm = "", [], {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line.lower()
        elif section == "[plugin]" and line and not line.startswith(("#", ";")):
            url = line.split(",", 1)[0].strip()
            label = PurePosixPath(url.split("?", 1)[0]).name
            # 只输出已知插件名，未知私有 URL 仅计数，不泄露查询参数。
            if any(k in label.lower() for k in ("netease", "jd_", "taobao")):
                plugins.append({"name": label, "enabled_in_file": "enabled=true" in line.replace(" ", "").lower()})
        elif section == "[mitm]" and "=" in line:
            key, value = (v.strip().lower() for v in line.split("=", 1))
            if key in ("enable", "skip-server-cert-verify"):
                mitm[key] = value == "true"
            elif key in ("ca-p12", "ca-passphrase"):
                mitm[key + "_present"] = bool(value)
    return {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "plugins": plugins,
            "mitm_config_fields": mitm, "active_config_verified": False, "certificate_trust_verified": False}


def audit(ssh):
    dbs = ssh.execute("find /var/mobile/Containers/Shared/AppGroup -maxdepth 3 -name loon_record.db -print").splitlines()
    if len(dbs) != 1:
        raise ValueError("loon_group_not_unique")
    group = str(PurePosixPath(dbs[0]).parent.parent)
    configs = ssh.execute("find " + shlex.quote(group + "/config") + " -maxdepth 1 -type f -print", sensitive=True).splitlines()
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "read_only": True, "configs": []}
    for path in configs:
        if path.endswith((".conf", ".lcf")):
            report["configs"].append(config_summary(PurePosixPath(path).name, read_bytes(ssh, path)))
    prefs = plistlib.loads(read_bytes(ssh, group + "/Library/Preferences/group.com.ruikq.decar.plist"))
    report["preference_keys"] = sorted(prefs)
    names = {c["name"] for c in report["configs"]}
    # 配置名称偏好只是线索，不能当成隧道实时运行版本的证明。
    report["selected_config_hints"] = [{"key": k, "name": PurePosixPath(v).name}
                                       for k, v in prefs.items() if isinstance(v, str) and PurePosixPath(v).name in names]
    query = "SELECT pluginName, COUNT(*) AS count, SUM(isException) AS exceptions, MAX(startTime) AS latest FROM scriptRecords GROUP BY pluginName;"
    rows = json.loads(ssh.execute("sqlite3 -readonly -json " + shlex.quote(dbs[0]) + " " + shlex.quote(query), sensitive=True) or "[]")
    report["script_record_groups"] = [{"plugin_name_hash": hashlib.sha256(str(r.get("pluginName")).encode()).hexdigest(),
                                       "count": r["count"], "exceptions": r["exceptions"], "latest": r["latest"]} for r in rows]
    processes = ssh.execute("ps -A -o pid,comm", sensitive=True)
    report["tunnel_process_present"] = "LoonTunnelProvider" in processes
    report["frida_process_present"] = any("frida-server" in line for line in processes.splitlines())
    report["runtime_version_verified"] = False
    report["ad_effect_verified"] = False
    return report


def main():
    root = Path(__file__).resolve().parents[1]
    target = root / "private/runs/safe-loon-audit/audit.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = audit(Transport())
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        # 偏好 key 可能含私人名称；工具输出仅摘要，完整结果仅留 private。
        print(json.dumps({"read_only": True, "config_count": len(report["configs"]),
                          "selected_config_hints": report["selected_config_hints"],
                          "tunnel_process_present": report["tunnel_process_present"],
                          "frida_process_present": report["frida_process_present"],
                          "script_record_groups": len(report["script_record_groups"]),
                          "runtime_version_verified": False, "path": str(target)}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"read_only": True, "completed": False, "reason": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
