import re
import unittest
from pathlib import Path

from pydantic import ValidationError

import main


ROOT = Path(__file__).resolve().parents[1]


class LLMTextLengthLimitTests(unittest.TestCase):
    def assert_canvas_message_accepted(self, length):
        payload = main.CanvasLLMRequest(message="x" * length)
        self.assertEqual(len(payload.message), length)

    def test_canvas_llm_message_accepts_requested_boundaries(self):
        for length in (19_999, 20_001, 50_000, 99_999, 100_000):
            with self.subTest(length=length):
                self.assert_canvas_message_accepted(length)

    def test_canvas_llm_message_rejects_above_limit(self):
        with self.assertRaises(ValidationError):
            main.CanvasLLMRequest(message="x" * 100_001)

    def test_chat_message_uses_same_limit(self):
        self.assertEqual(main.ChatRequest(message="x" * 100_000).message, "x" * 100_000)
        with self.assertRaises(ValidationError):
            main.ChatRequest(message="x" * 100_001)

    def test_system_prompt_uses_same_limit(self):
        payload = main.CanvasLLMRequest(message="ok", system_prompt="x" * 100_000)
        self.assertEqual(len(payload.system_prompt), 100_000)
        with self.assertRaises(ValidationError):
            main.CanvasLLMRequest(message="ok", system_prompt="x" * 100_001)

    def test_canvas_llm_history_message_content_uses_same_limit(self):
        payload = main.CanvasLLMRequest(
            message="ok",
            messages=[{"role": "user", "content": "x" * 100_000}],
        )
        self.assertEqual(len(payload.messages[0]["content"]), 100_000)
        with self.assertRaises(ValidationError):
            main.CanvasLLMRequest(
                message="ok",
                messages=[{"role": "user", "content": "x" * 100_001}],
            )

    def test_smart_and_normal_prompt_editors_share_frontend_limit(self):
        for rel_path in ("static/js/canvas.js", "static/js/smart-canvas.js"):
            with self.subTest(path=rel_path):
                source = (ROOT / rel_path).read_text(encoding="utf-8")
                match = re.search(r"const PROMPT_TEXT_MAX_LENGTH = (?P<limit>\d+);", source)
                self.assertIsNotNone(match)
                self.assertEqual(match.group("limit"), "100000")
                self.assertIn('maxlength="${PROMPT_TEXT_MAX_LENGTH}"', source)


if __name__ == "__main__":
    unittest.main()
