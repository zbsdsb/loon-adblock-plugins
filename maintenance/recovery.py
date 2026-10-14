"""事故后唯一允许的真机核验入口：只读，不导入/启动 Frida，不启动应用。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from .transport import Transport


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "private/incidents/frida-restarts-20261014"
    output.mkdir(parents=True, exist_ok=True)
    events = []
    command = ("date '+%Y-%m-%d %H:%M:%S'; uptime; ps -p 1 -o lstart=; "
               "ps -A -o pid,comm | grep frida || true; "
               "curl -fsS --max-time 3 http://127.0.0.1:8769/status; echo; "
               "find /var/mobile/Library/Logs/CrashReporter -maxdepth 1 -type f "
               "-name 'panic-full-2026-10-14*' -print")
    try:
        stdout = Transport(ledger=events.append).execute(command, timeout=8)
        print(stdout)
        result = 0
    except Exception as exc:
        print(type(exc).__name__, file=sys.stderr)
        result = 2
    filename = "recovery-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
    (output / filename).write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    sys.exit(main())
