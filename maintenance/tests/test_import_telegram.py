import json
import unittest
from unittest.mock import Mock

from maintenance.import_telegram import read_remote_credentials, save_dpapi
from maintenance.notify import credentials
from maintenance.transport import Transport
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch


class CredentialImportTests(unittest.TestCase):
    def test_read_only_and_sensitive(self):
        transport = Mock()
        transport.execute.return_value = json.dumps({"token": "123:fixture_only", "chat_id": "-456"})
        self.assertEqual(read_remote_credentials(transport)["chat_id"], "-456")
        args, kwargs = transport.execute.call_args
        self.assertTrue(args[0].startswith("docker exec douyin-auto-fire python -c "))
        self.assertNotIn("123:fixture_only", args[0])
        self.assertTrue(kwargs["sensitive"])

    def test_no_container_shell_injection(self):
        transport = Mock()
        with self.assertRaises(ValueError):
            read_remote_credentials(transport, "x; reboot")
        transport.execute.assert_not_called()

    def test_missing_or_invalid_credentials(self):
        for data in ({}, {"token": "bad", "chat_id": "42"},
                     {"token": "123:fixture", "chat_id": "not-a-chat"}):
            with self.subTest(data=data):
                transport = Mock()
                transport.execute.return_value = json.dumps(data)
                with self.assertRaises(ValueError):
                    read_remote_credentials(transport)

    def test_sensitive_transport_redacts_both_streams(self):
        events = []
        result = {"success": True, "exit_code": 0, "stdout": "fixture_secret", "stderr": "fixture_secret"}
        proc = subprocess.CompletedProcess([], 0, json.dumps(result), "")
        with patch("subprocess.run", return_value=proc):
            self.assertEqual(Transport(ledger=events.append).execute("private read", sensitive=True), "fixture_secret")
        self.assertNotIn("fixture_secret", json.dumps(events))

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI integration')
    def test_dpapi_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            data = {"token": "123:fixture_only", "chat_id": "-456"}
            target = save_dpapi(directory, data)
            self.assertNotIn(data['token'], target.read_text(encoding='utf-8'))
            before = target.read_bytes()
            # 保留 Windows 的 SystemRoot/用户上下文，避免破坏 DPAPI 子进程运行环境。
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
                self.assertEqual(credentials(directory), (data['token'], data['chat_id']))
            with self.assertRaises(FileExistsError):
                save_dpapi(directory, data)
            self.assertEqual(before, target.read_bytes())
