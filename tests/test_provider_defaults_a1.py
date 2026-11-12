"""A1 收尾：页面不再发送 comfly、后端没有 comfly 别名、请求默认值是“未指定”；设置页保存时保留 primary。"""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main

ROOT = Path(__file__).resolve().parents[1]


def without_comments(source):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(line for line in source.splitlines() if not line.strip().startswith("//"))


class PagesDoNotSendComflyTests(unittest.TestCase):
    def test_online_and_chat_pages_have_no_comfly_literal(self):
        for page in ("online.html", "gpt-chat.html"):
            with self.subTest(page=page):
                code = without_comments((ROOT / "static" / page).read_text(encoding="utf-8"))
                self.assertNotIn("'comfly'", code)
                self.assertNotIn('"comfly"', code)

    def test_pages_refuse_to_send_without_a_provider(self):
        online = (ROOT / "static/online.html").read_text(encoding="utf-8")
        chat = (ROOT / "static/gpt-chat.html").read_text(encoding="utf-8")
        self.assertIn("if(!provider){ alert(tr('online.noImageProvider')); return; }", online)
        self.assertIn("alert(tr(mode === 'image' ? 'chat.noImageProvider' : 'chat.noChatProvider'));", chat)
        studio = (ROOT / "static/js/i18n/studio.js").read_text(encoding="utf-8")
        for key in ("online.noImageProvider", "chat.noImageProvider", "chat.noChatProvider"):
            self.assertIn(f'"{key}"', studio)


class BackendDefaultsTests(unittest.TestCase):
    def test_request_models_default_to_unspecified_provider(self):
        offenders = []
        for name in dir(main):
            model = getattr(main, name)
            fields = getattr(model, "model_fields", None)
            if not isinstance(fields, dict) or not isinstance(model, type):
                continue
            for field_name in ("provider_id", "provider"):
                field = fields.get(field_name)
                if field is not None and field.default == "comfly":
                    offenders.append(f"{name}.{field_name}")
        self.assertEqual(offenders, [])

    def test_no_comfly_fallbacks_in_resolution_code(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertNotIn('provider_id="comfly"', source)
        self.assertNotIn('or "comfly")', source)
        self.assertNotIn('else "comfly"', source)

    def test_provider_literally_named_comfly_still_resolves(self):
        providers = [{"id": "comfly", "name": "Comfly", "enabled": True, "image_models": ["m"], "chat_models": []},
                     {"id": "kie", "name": "Kie", "enabled": True, "image_models": ["k"], "chat_models": []}]
        with patch.object(main, "load_api_providers", return_value=providers):
            self.assertEqual(main.get_api_provider("comfly")["id"], "comfly")


class SettingsPrimaryRoundTripTests(unittest.TestCase):
    """设置页保存时保留 primary（审计时每次保存都写成 false；合并 fork/main 的 b088ffc 已修复，这里防止回归）。"""

    def test_settings_page_sends_primary_from_item(self):
        source = (ROOT / "static/js/api-settings.js").read_text(encoding="utf-8")
        self.assertIn("primary:item.primary === true,", source)
        self.assertNotRegex(source, r"\n\s+primary:false,\n\s+image_models:item\.image_models")
        self.assertIn("providerPrimaryInput", (ROOT / "static/api-settings.html").read_text(encoding="utf-8"))

    def test_put_then_get_keeps_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            providers_file = str(Path(tmp) / "api_providers.json")
            with patch.object(main, "API_PROVIDERS_FILE", providers_file), \
                 patch.object(main, "update_env_values"), patch.object(main, "reload_env_globals"):
                client = TestClient(main.app, base_url="http://127.0.0.1")  # 默认 Host testserver 会被 A7 的 Host 校验拒绝
                payload = [
                    {"id": "custom-a", "name": "A", "base_url": "http://127.0.0.1:9/v1", "protocol": "openai", "image_models": ["m"], "primary": False},
                    {"id": "custom-b", "name": "B", "base_url": "http://127.0.0.1:9/v1", "protocol": "openai", "image_models": ["m"], "primary": True},
                ]
                self.assertEqual(client.put("/api/providers", json=payload).status_code, 200)
                listed = {p["id"]: p.get("primary") for p in client.get("/api/providers").json()["providers"]}
                self.assertTrue(listed["custom-b"])
                self.assertFalse(listed["custom-a"])
                self.assertEqual(main.get_primary_provider_id(), "custom-b")
                stored = json.loads(Path(providers_file).read_text(encoding="utf-8"))
                stored = stored if isinstance(stored, list) else stored.get("providers", stored)
                self.assertEqual([p["id"] for p in stored if p.get("primary")], ["custom-b"])


if __name__ == "__main__":
    unittest.main()
