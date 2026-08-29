import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CanvasGenerationRequestTests(unittest.TestCase):
    def test_classic_canvas_forwards_selected_ratio_and_resolution(self):
        source = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")
        match = re.search(
            r"async function runGenerator\(genId, opts=\{\}\)\{(?P<body>.*?)\n\}",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "找不到普通画布 runGenerator")
        body = match.group("body")
        payload_match = re.search(r"const payload = \{(?P<payload>.*?)\n    \};", body, re.DOTALL)
        self.assertIsNotNone(payload_match, "找不到普通画布生图请求 payload")
        payload = payload_match.group("payload")
        self.assertIn("aspect_ratio:API_RATIO_VALUES[gen.ratio]", payload)
        self.assertIn("resolution:gen.resolution", payload)
        self.assertIn("reference_images:", payload)

    def test_smart_canvas_normalizes_resolution_case_before_request(self):
        source = (ROOT / "static/js/smart-canvas.js").read_text(encoding="utf-8")
        match = re.search(
            r"async function runApiGeneration\(prompt, refs, runSettings=settings\)\{(?P<body>.*?)\n\}",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "找不到智能画布 runApiGeneration")
        body = match.group("body")
        self.assertIn(".trim().toLowerCase()", body)
        self.assertIn("['1k','2k','4k'].includes(apiResolution)", body)


if __name__ == "__main__":
    unittest.main()
