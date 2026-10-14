"""复用 SSH Skill 的认证/主机密钥策略，不另建凭据或裸 SSH 连接。"""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import time


class DeviceError(RuntimeError):
    pass


class Transport:
    def __init__(self, alias="zbs-se2", skill=None, ledger=None):
        self.alias = alias
        self.skill = Path(skill or os.environ.get("SSH_SKILL_ROOT", str(Path.home() / ".agents/skills/ssh-skill")))
        self.ledger = ledger

    def execute(self, command, timeout=20, sensitive=False):
        argv = [sys.executable, str(self.skill / "scripts/ssh_execute.py"), self.alias, command,
                "--timeout", str(timeout)]
        try:
            proc = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout + 10)
            result = json.loads(proc.stdout or proc.stderr)
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            raise DeviceError(type(exc).__name__) from exc
        if self.ledger:
            self.ledger({"command": "[private read]" if sensitive else command, "exit_status": result.get("exit_code"),
                         "stdout": "[private data withheld]" if sensitive else result.get("stdout", ""),
                         "stderr": "[private data withheld]" if sensitive else result.get("stderr", ""),
                         "wrapper_exit": proc.returncode})
        if not result.get("success"):
            raise DeviceError("SSH command failed: exit=" + str(result.get("exit_code")))
        return result.get("stdout", "")

    @contextmanager
    def forward(self, remote_port=27042):
        # 使用 skill 自带客户端生成认证参数；不改 ~/.ssh/config，不暴露设备监听端口。
        sys.path.insert(0, str(self.skill / "scripts/lib"))
        from config_v3 import SSHConfigLoaderV3
        client = SSHConfigLoaderV3().from_alias(self.alias)
        if not hasattr(client, "_build_ssh_base_args"):
            raise DeviceError("当前 SSH Skill 客户端不支持原生密钥端口转发")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        args = client._build_ssh_base_args() + ["-o", "ForwardAgent=no", "-o", "ExitOnForwardFailure=yes",
                 "-N", "-L", f"127.0.0.1:{port}:127.0.0.1:{remote_port}", client.destination]
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            for _ in range(50):
                if proc.poll() is not None:
                    raise DeviceError("SSH Skill 端口转发退出")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        pass
                except OSError:
                    time.sleep(.1)
                    continue
                # 探测连接先关闭，不能在 yield 期间保留一个空闲 Frida 握手连接。
                yield f"127.0.0.1:{port}"
                return
            raise DeviceError("SSH Skill 端口转发启动超时")
        finally:
            proc.terminate()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
