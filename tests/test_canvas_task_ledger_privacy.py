"""任务账本（data/canvas_tasks.sqlite3）落盘时：去掉 result.prompt；error、message 里已知的密钥值替换成 [redacted]。
旧版本已经写进账本的行，启动恢复时按同样规则重写。"""
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from test_canvas_task_persistence_c9 import LocalLedgerTestCase

ENV_KEY = "sk-audit-env-0123456789abcdef"
FILE_KEY = "file-only-secret-9876543210"
ALL_ENV_NAMES = {"AUDIT_PROVIDER_API_KEY": ENV_KEY, "AUDIT_SHORT_TOKEN": "abc1234", "AUDIT_BASE_URL": "https://upstream.example/v1"}


class LedgerPrivacyTests(LocalLedgerTestCase):
    def setUp(self):
        super().setUp()
        env_file = Path(self.temp.name) / ".env"
        env_file.write_text(f"# comment\nAUDIT_WALLET_SECRET='{FILE_KEY}'\nAUDIT_REGION=cn-beijing-0001\n", encoding="utf-8")
        for patcher in (patch.dict(os.environ, ALL_ENV_NAMES), patch.object(main, "API_ENV_FILE", str(env_file))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_prompt_and_raw_are_not_written_but_the_result_is(self):
        self.put("t-ok", status="succeeded", result={"prompt": "完整提示词 secret plan", "raw": {"x": 1},
                                                      "images": ["/assets/output/a.png"], "params": {"size": "1024x1024"}})
        row = self.rows()["t-ok"]
        self.assertEqual(row["result"], {"images": ["/assets/output/a.png"], "params": {"size": "1024x1024"}})
        self.assertNotIn("完整提示词", json.dumps(row, ensure_ascii=False))
        # 内存里的任务（返回给页面的）不受影响
        self.assertEqual(main.CANVAS_TASKS["t-ok"]["result"]["prompt"], "完整提示词 secret plan")

    def test_known_secret_values_are_redacted_in_error_and_message(self):
        error = f"401 from https://upstream.example/v1/x?apiKey={ENV_KEY}; wallet={FILE_KEY}; short=abc1234"
        self.put("t-err", status="failed", error=error, message={"detail": [f"Bearer {ENV_KEY}"]})
        row = self.rows()["t-err"]
        text = json.dumps(row, ensure_ascii=False)
        self.assertNotIn(ENV_KEY, text)
        self.assertNotIn(FILE_KEY, text)
        self.assertEqual(row["error"], "401 from https://upstream.example/v1/x?apiKey=[redacted]; wallet=[redacted]; short=abc1234")
        self.assertEqual(row["message"], {"detail": ["Bearer [redacted]"]})
        # 名字不像密钥的值（地址、地区）和太短的值不替换
        self.assertIn("https://upstream.example/v1", row["error"])
        self.assertIn("abc1234", row["error"])

    def test_restart_rewrites_rows_from_older_versions(self):
        old = {"id": "t-old", "type": "online-image", "status": "succeeded", "created_at": time.time(), "updated_at": time.time(),
               "result": {"prompt": "旧提示词", "images": ["/assets/output/b.png"]}, "error": f"echo {ENV_KEY}", "provider_id": "kie"}
        with main.canvas_task_db() as db:
            db.execute("INSERT INTO jobs (id, status, data, updated) VALUES (?, ?, ?, ?)",
                       ("t-old", "succeeded", json.dumps(old, ensure_ascii=False), old["updated_at"]))
        self.assertEqual(self.restart(), 1)
        row = self.rows()["t-old"]
        self.assertEqual(row["result"], {"images": ["/assets/output/b.png"]})
        self.assertEqual(row["error"], "echo [redacted]")
        self.assertEqual(main.CANVAS_TASKS["t-old"]["status"], "succeeded")

    def test_secret_collection_rules(self):
        values = main.known_secret_values()
        self.assertIn(ENV_KEY, values)
        self.assertIn(FILE_KEY, values)
        self.assertNotIn("abc1234", values)
        self.assertNotIn("https://upstream.example/v1", values)
        self.assertNotIn("cn-beijing-0001", values)
        self.assertEqual(values, sorted(values, key=len, reverse=True))  # 长的先替换，避免部分重叠


if __name__ == "__main__":
    unittest.main()
