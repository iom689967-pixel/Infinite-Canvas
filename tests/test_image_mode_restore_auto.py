"""A5 后续：设置页"恢复自动识别"按钮。只在手动选过图片接口、且下拉框可用时显示；
点击后清除手动标记，并按与后端相同的规则（Agnes → OpenAI JSON，土豆 → 土豆异步）重新识别，没有匹配规则时保持当前选择。"""
import re
import unittest
from pathlib import Path

import main

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static/js/api-settings.js").read_text(encoding="utf-8")
HTML = (ROOT / "static/api-settings.html").read_text(encoding="utf-8")


def body(name):
    match = re.search(r"function " + name + r"\(.*?\)\{.*?\n\}", JS, re.S)
    assert match, name
    return match.group(0)


class RestoreImageModeAutoTests(unittest.TestCase):
    def test_button_lives_next_to_the_mode_select(self):
        wrap = re.search(r'<div class="field-frame image-request-mode-wrap">.*?</div>', HTML, re.S).group(0)
        self.assertIn('id="imageRequestModeAutoBtn"', wrap)
        self.assertIn('onclick="restoreImageRequestModeAuto()"', wrap)
        self.assertIn(" hidden>", wrap)

    def test_visibility_follows_the_manual_flag(self):
        sync = body("syncImageRequestModeAutoBtn")
        self.assertIn("item.image_request_mode_manual === true", sync)
        self.assertIn("!imageRequestModeInput.disabled", sync)
        listener = re.search(r"imageRequestModeInput\.addEventListener\('change', event => \{.*?\n    \}\);", JS, re.S).group(0)
        self.assertIn("syncImageRequestModeAutoBtn(item);", listener)
        self.assertIn("syncImageRequestModeAutoBtn(item);", body("renderEditor"))

    def test_restore_clears_the_flag_and_reapplies_rules(self):
        restore = body("restoreImageRequestModeAuto")
        self.assertIn("item.image_request_mode_manual = false;", restore)
        self.assertIn("autoImageRequestMode(item)", restore)
        self.assertIn("window.restoreImageRequestModeAuto = restoreImageRequestModeAuto;", JS)

    def test_frontend_rules_match_the_backend(self):
        rules = body("autoImageRequestMode")
        self.assertIn("apihub.agnes-ai.com", rules)
        self.assertIn("startsWith('agnes-image-')", rules)
        self.assertIn("api.ai-tudou.net", rules)
        self.assertEqual(main.detect_image_request_mode("https://apihub.agnes-ai.com/v1", []), "openai-json")
        self.assertEqual(main.detect_image_request_mode("https://x.test/v1", ["agnes-image-2.1-flash"]), "openai-json")
        # 保存时后端也会按同样的规则识别：清除手动标记后 Agnes 回到 OpenAI JSON
        saved = main.normalize_provider({"id": "agnes-x", "base_url": "https://apihub.agnes-ai.com/v1",
                                         "image_request_mode": "openai", "image_request_mode_manual": False})
        self.assertEqual((saved["image_request_mode"], saved["image_request_mode_manual"]), ("openai-json", False))


if __name__ == "__main__":
    unittest.main()
