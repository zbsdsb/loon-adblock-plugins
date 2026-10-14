import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request


def credentials(root):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        return token, chat
    secret = Path(root) / "private/telegram.dpapi.json"
    if not secret.exists() or os.name != "nt":
        return None
    # DPAPI 只能由创建文件的 Windows 用户解密。输出只进入内存，不记录命令结果。
    # pwsh 父进程的 PSModulePath 可能指向不兼容的新版模块，显式使用当前 powershell 的安全模块。
    command = "$ErrorActionPreference='Stop';Import-Module ($PSHOME+'/Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1') -ErrorAction Stop;"
    command += "$s=Get-Content -Raw -LiteralPath '" + str(secret).replace("'", "''") + "'|ConvertFrom-Json;"
    command += "$r=@{};foreach($k in @('token','chat_id')){$p=[Runtime.InteropServices.Marshal]::SecureStringToBSTR((ConvertTo-SecureString $s.$k));try{$r[$k]=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($p)}finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p)}};$r|ConvertTo-Json -Compress"
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                              capture_output=True, timeout=15, encoding="utf-8")
        if proc.returncode:
            return None
        data = json.loads(proc.stdout)
        token, chat = data.get("token"), data.get("chat_id")
        return (token, chat) if isinstance(token, str) and token and isinstance(chat, str) and chat else None
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        # 损坏密文、解密失败、超时都不能误报配置成功或打印解密输出。
        return None


def send(root, message):
    auth = credentials(root)
    if not auth:
        return {"delivered": False, "reason": "telegram_credentials_missing"}
    token, chat = auth
    body = json.dumps({"chat_id": chat, "text": message, "disable_web_page_preview": True}).encode()
    request = urllib.request.Request("https://api.telegram.org/bot" + token + "/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
        return {"delivered": data.get("ok") is True, "message_id": data.get("result", {}).get("message_id")}
    except urllib.error.HTTPError as exc:
        return {"delivered": False, "reason": "telegram_http_error", "status": exc.code}
    except (OSError, ValueError):
        return {"delivered": False, "reason": "telegram_transport_error"}
