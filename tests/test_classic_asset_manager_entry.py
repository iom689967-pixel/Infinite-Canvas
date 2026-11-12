"""C10 后续：普通画布编辑器的资产面板标题栏有"资产库管理"按钮，点击打开资产库管理弹窗。
以前唯一的入口在已拆走的"选画布"页面上，弹窗在编辑器里打不开。"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static/canvas.html").read_text(encoding="utf-8")
JS = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")
I18N = (ROOT / "static/js/i18n/canvas.js").read_text(encoding="utf-8")
LOADER = (ROOT / "static/js/i18n.js").read_text(encoding="utf-8")


class ClassicAssetManagerEntryTests(unittest.TestCase):
    def test_button_sits_in_the_asset_panel_header(self):
        panel = re.search(r'<aside id="canvasAssetPanel".*?</aside>', HTML, re.S).group(0)
        head = re.search(r'<div class="canvas-asset-head">.*?</div>\s*</div>', panel, re.S).group(0)
        self.assertIn('id="canvasAssetManageBtn"', head)
        self.assertIn('data-i18n-title="canvas.assetManager"', head)
        self.assertIn('id="canvasAssetCloseBtn"', head)

    def test_button_opens_the_manager(self):
        self.assertIn("const canvasAssetManageBtn = document.getElementById('canvasAssetManageBtn');", JS)
        self.assertIn("canvasAssetManageBtn?.addEventListener('click', openAssetManager);", JS)
        self.assertRegex(JS, r"function openAssetManager\(\)\{\n    assetManagerModal\?\.classList\.add\('open'\);")

    def test_label_is_translated_and_loader_version_bumped(self):
        self.assertIn('"canvas.assetManager": { zh: "资产库管理", en: "Manage asset library" }', I18N)
        self.assertNotIn("2026.09.01.run-intent.1", LOADER)  # 让浏览器重新拉取带新文案的 i18n 文件


if __name__ == "__main__":
    unittest.main()
