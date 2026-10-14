"""只读复用已授权服务的通知凭据；明文仅经过 SSH 和本机进程内存。"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .transport import Transport


def read_remote_credentials(transport, container="douyin-auto-fire"):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", container):
        raise ValueError("invalid_container")
    # 不 inspect 全量环境，不读取 Cookie，不写入或重启远端抖音服务。
    code = ("import os,json; print(json.dumps({'token':os.environ.get('TG_BOT_TOKEN'),"
            "'chat_id':os.environ.get('TG_CHAT_ID')}))")
    import shlex
    raw = transport.execute("docker exec " + shlex.quote(container) + " python -c " + shlex.quote(code),
                            timeout=15, sensitive=True)
    data = json.loads(raw)
    token, chat = data.get("token"), data.get("chat_id")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9]+:[a-zA-Z0-9_-]+", token):
        raise ValueError("remote_token_missing_or_invalid")
    if not isinstance(chat, str) or not re.fullmatch(r"-?[0-9]+", chat):
        raise ValueError("remote_chat_missing_or_invalid")
    return {"token": token, "chat_id": chat}


def save_dpapi(root, data):
    if os.name != "nt":
        raise RuntimeError("windows_dpapi_required")
    target = Path(root) / "private/telegram.dpapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError("credentials_already_exist")
    # 凭据通过 stdin 传入，而非命令行参数/环境变量/临时明文文件。
    command = ("Import-Module ($PSHOME+'/Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1') -ErrorAction Stop;"
               "$ErrorActionPreference='Stop';$s=[Console]::In.ReadToEnd()|ConvertFrom-Json;"
               "$r=@{};foreach($k in @('token','chat_id')){"
               "$r[$k]=ConvertFrom-SecureString (ConvertTo-SecureString $s.$k -AsPlainText -Force)};"
               "$r|ConvertTo-Json -Compress")
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                          input=json.dumps(data), capture_output=True, encoding="utf-8", timeout=15)
    if proc.returncode:
        # PowerShell 错误可能包含输入；不转发其 stdout/stderr。
        raise RuntimeError("dpapi_encrypt_failed")
    encrypted = json.loads(proc.stdout)
    if set(encrypted) != {"token", "chat_id"} or any(
            not isinstance(v, str) or v in data.values() for v in encrypted.values()):
        raise RuntimeError("invalid_dpapi_output")
    with target.open("x", encoding="utf-8") as handle:
        json.dump(encrypted, handle)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alias", default="dhcc-zbs")
    parser.add_argument("--container", default="douyin-auto-fire")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        data = read_remote_credentials(Transport(alias=args.alias), args.container)
        target = save_dpapi(root, data)
        print(json.dumps({"imported": True, "storage": "windows_current_user_dpapi",
                          "source": args.alias + "/" + args.container, "path": str(target)}))
        return 0
    except Exception as exc:
        # 不输出远端正文、异常内容或 traceback。
        print(json.dumps({"imported": False, "reason": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
