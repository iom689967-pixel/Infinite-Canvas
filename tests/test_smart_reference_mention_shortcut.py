import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART = (ROOT / "static/js/smart-canvas.js").read_text(encoding="utf-8")
SMART_CSS = (ROOT / "static/css/smart-canvas.css").read_text(encoding="utf-8")
CLASSIC = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")


class SmartReferenceMentionShortcutContractTests(unittest.TestCase):
    def test_01_shortcut_is_rendered_only_for_existing_input_mention_candidates(self):
        render = SMART[SMART.index("function renderInputThumbsRow("):SMART.index("function bindInputThumbsDrag(")]
        self.assertIn("inputMentionCandidateImages(node)", render)
        self.assertIn("mentionCandidatesByKey.get(key) || mentionCandidatesByUrl.get(img.url)", render)
        self.assertIn("mentionCandidate && kind === 'image'", render)
        self.assertIn('data-input-insert-mention="${escapeAttr(inputRefKey(mentionCandidate))}"', render)

    def test_02_shortcut_reuses_existing_mention_inserter(self):
        binder = SMART[SMART.index("function bindInputThumbReferenceActions("):SMART.index("function bindInputThumbsDrag(")]
        self.assertIn("insertMentionToken(img)", binder)
        self.assertNotIn("document.createElement('span')", binder)

    def test_03_shortcut_preserves_range_before_focus_can_move(self):
        binder = SMART[SMART.index("function bindInputThumbReferenceActions("):SMART.index("function bindInputThumbsDrag(")]
        self.assertRegex(binder, r"preservePromptRange = event => \{\s*saveMentionRange\(\);")
        self.assertIn("btn.addEventListener('pointerdown', preservePromptRange)", binder)
        self.assertIn("btn.addEventListener('mousedown', preservePromptRange)", binder)

    def test_04_shortcut_blocks_thumbnail_click_and_drag_events(self):
        binder = SMART[SMART.index("function bindInputThumbReferenceActions("):SMART.index("function bindInputThumbsDrag(")]
        self.assertIn("event.preventDefault()", binder)
        self.assertIn("event.stopPropagation()", binder)
        self.assertIn("btn.addEventListener('dragstart', preservePromptRange)", binder)

    def test_05_never_focused_prompt_falls_back_to_end(self):
        insert = SMART[SMART.index("function insertMentionToken("):SMART.index("function collectPromptParts(")]
        self.assertIn("hasSavedPromptRange", insert)
        self.assertIn("endRange.selectNodeContents(promptInput)", insert)
        self.assertIn("endRange.collapse(false)", insert)

    def test_06_inserted_token_updates_saved_caret_for_consecutive_shortcuts(self):
        insert = SMART[SMART.index("function insertMentionToken("):SMART.index("function collectPromptParts(")]
        self.assertIn("range.setStartAfter(spacer)", insert)
        self.assertIn("mentionRange = range.cloneRange()", insert)
        self.assertLess(insert.index("range.setStartAfter(spacer)"), insert.index("mentionRange = range.cloneRange()"))

    def test_07_shortcut_has_requested_hover_geometry(self):
        rule = re.search(r"\.input-thumb-mention \{([^}]+)\}", SMART_CSS)
        self.assertIsNotNone(rule)
        css = rule.group(1)
        for declaration in ("right:5px", "bottom:5px", "width:22px", "height:22px", "border-radius:50%", "z-index:7", "opacity:0", "pointer-events:none"):
            self.assertIn(declaration, css)
        self.assertIn(".input-thumb:hover .input-thumb-mention", SMART_CSS)

    def test_08_normal_canvas_does_not_gain_a_parallel_mention_system(self):
        self.assertNotIn("data-input-insert-mention", CLASSIC)
        self.assertNotIn("insertMentionToken", CLASSIC)


if __name__ == "__main__":
    unittest.main()
