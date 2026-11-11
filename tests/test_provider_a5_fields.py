"""A5：删除死字段（image_edit_route、/api/config 的 has_api_key/has_ms_key）；
image_request_mode 在用户明确选择过时以用户为准，不再被自动识别覆盖。"""
import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import main
from provider_schema import ApiProviderPayload

ROOT = Path(__file__).resolve().parents[1]
AGNES = "https://apihub.agnes-ai.com/v1"


def provider(**changes):
    item = {"id": "agnes-test", "name": "Agnes", "base_url": AGNES, "protocol": "openai",
            "image_request_mode": "openai", "image_models": ["agnes-image-1"]}
    item.update(changes)
    return main.normalize_provider(item)


class DeadFieldsTests(unittest.TestCase):
    def test_image_edit_route_is_gone_everywhere(self):
        self.assertNotIn("image_edit_route", ApiProviderPayload.model_fields)
        self.assertNotIn("image_edit_route", provider(image_edit_route="chat"))
        for rel in ("main.py", "provider_schema.py", "static/js/api-settings.js", "static/api-settings.html"):
            source = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("image_edit_route", source, rel)
            self.assertNotIn("imageEditRoute", source, rel)

    def test_legacy_payload_with_image_edit_route_is_still_accepted(self):
        # 旧页面保存时还会带上这个字段：主程序的 Pydantic 模型忽略多余字段
        parsed = ApiProviderPayload(**{"id": "x", "image_edit_route": "chat"}).model_dump()
        self.assertNotIn("image_edit_route", parsed)
        source = (ROOT / "instance_provider_settings.py").read_text(encoding="utf-8")
        self.assertIn("if k!='image_edit_route'", source)  # 实例模式的严格校验前先丢掉

    def test_config_has_no_key_presence_flags(self):
        client = TestClient(main.app, base_url="http://127.0.0.1")
        config = client.get("/api/config").json()
        self.assertNotIn("has_api_key", config)
        self.assertNotIn("has_ms_key", config)


class ImageRequestModeTests(unittest.TestCase):
    def test_detection_still_applies_when_user_never_chose(self):
        item = provider()
        self.assertFalse(item["image_request_mode_manual"])
        self.assertEqual(item["image_request_mode"], "openai-json")
        self.assertEqual(main.effective_image_request_mode(item, "agnes-image-1"), "openai-json")

    def test_explicit_user_choice_wins_over_detection(self):
        item = provider(image_request_mode="openai", image_request_mode_manual=True)
        self.assertTrue(item["image_request_mode_manual"])
        self.assertEqual(item["image_request_mode"], "openai")
        self.assertEqual(main.effective_image_request_mode(item, "agnes-image-1"), "openai")
        # 保存后再读回、再保存，选择保持不变
        again = main.normalize_provider(dict(item))
        self.assertEqual((again["image_request_mode"], again["image_request_mode_manual"]), ("openai", True))

    def test_effective_mode_detects_per_model_only_without_explicit_choice(self):
        base = {"id": "p", "base_url": "https://example.test/v1", "image_request_mode": "openai"}
        self.assertEqual(main.effective_image_request_mode(base, "agnes-image-1"), "openai-json")
        self.assertEqual(main.effective_image_request_mode(dict(base, image_request_mode_manual=True), "agnes-image-1"), "openai")

    def test_fixed_platforms_are_never_manual(self):
        kie = main.normalize_provider({"id": "kie", "image_request_mode": "openai-json", "image_request_mode_manual": True})
        self.assertEqual((kie["image_request_mode"], kie["image_request_mode_manual"]), ("openai", False))


class SettingsPageWiringTests(unittest.TestCase):
    source = (ROOT / "static/js/api-settings.js").read_text(encoding="utf-8")

    def test_only_a_trusted_user_change_marks_the_choice_manual(self):
        listener = re.search(r"imageRequestModeInput\.addEventListener\('change', event => \{.*?\n    \}\);", self.source, re.S)
        self.assertIsNotNone(listener)
        self.assertIn("if(event.isTrusted){", listener.group(0))
        self.assertIn("item.image_request_mode_manual = true;", listener.group(0))

    def test_detection_and_tudou_preset_do_not_override_a_manual_choice(self):
        body = re.search(r"function applyDetectedImageRequestMode\(mode\)\{.*?\n\}", self.source, re.S).group(0)
        self.assertLess(body.index("item.image_request_mode_manual === true"), body.index("const detected"))
        self.assertIn("if(isTudouHost && imageRequestModeInput && item.image_request_mode_manual !== true){", self.source)

    def test_save_sends_the_flag(self):
        self.assertIn("image_request_mode_manual:item.image_request_mode_manual === true,", self.source)


if __name__ == "__main__":
    unittest.main()
