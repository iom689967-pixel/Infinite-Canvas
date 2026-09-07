import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART_HTML = (ROOT / "static/smart-canvas.html").read_text(encoding="utf-8")
SMART_CSS = (ROOT / "static/css/smart-canvas.css").read_text(encoding="utf-8")
SMART_JS = (ROOT / "static/js/smart-canvas.js").read_text(encoding="utf-8")


def css_rule(selector):
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]+)\}}", SMART_CSS)
    if not match:
        raise AssertionError(f"missing CSS rule: {selector}")
    return match.group(1)


class SmartComposerVerticalLayoutTests(unittest.TestCase):
    def test_middle_prompt_regions_share_one_bounded_layout_context(self):
        main_start = SMART_HTML.index('<div class="composer-main-content">')
        upstream = SMART_HTML.index('id="inputPromptPreview"', main_start)
        prompt = SMART_HTML.index('id="promptInput"', upstream)
        params = SMART_HTML.index('<div class="param-row">', prompt)
        main_end = SMART_HTML.rfind('</div>', main_start, params)
        self.assertLess(main_start, upstream)
        self.assertLess(upstream, prompt)
        self.assertLess(prompt, main_end)
        self.assertLess(main_end, params)

        card = css_rule(".composer-card")
        self.assertIn("grid-template-rows:auto auto minmax(0, 1fr) auto", card)
        self.assertIn('"main main" "params run"', card)

        main = css_rule(".composer-main-content")
        self.assertIn("min-height:0", main)
        self.assertIn("display:flex", main)
        self.assertIn("flex-direction:column", main)

    def test_both_prompt_contents_scroll_instead_of_expanding_the_card(self):
        preview = css_rule(".input-prompt-preview.has-text")
        preview_text = css_rule(".input-prompt-preview-text")
        prompt_row = css_rule(".composer-card .prompt-row")
        prompt_input = css_rule(".prompt-input")

        self.assertIn("flex:0 1 auto", preview)
        self.assertIn("min-height:0", preview_text)
        self.assertIn("overflow-y:auto", preview_text)
        self.assertIn("flex:1 1 var(--prompt-h, 240px)", prompt_row)
        self.assertIn("min-height:0", prompt_row)
        self.assertIn("min-height:0", prompt_input)
        self.assertIn("max-height:100%", prompt_input)
        self.assertIn("overflow-y:auto", prompt_input)
        self.assertNotIn("min-height:var(--prompt-h", prompt_input)

    def test_prompt_resize_uses_parent_flex_basis_without_fixed_overhead(self):
        self.assertIn("composer.style.setProperty('--prompt-h'", SMART_JS)
        self.assertIn("startPanelH:composerLayoutSize(node).height", SMART_JS)
        self.assertIn("promptResizeState.startPanelH + promptDelta", SMART_JS)
        self.assertNotIn("settings.promptH + 220", SMART_JS)


if __name__ == "__main__":
    unittest.main()
