"""Character boundaries at the real request schemas; no provider calls or writes."""
import json
import inspect
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

import main


ROOT = Path(__file__).resolve().parents[1]
CASES = [("x", n) for n in (20_000, 20_001, 99_999, 100_000, 100_001)] + [
    (char, n) for char in ("中", "😀") for n in (100_000, 100_001)
]
PROMPT_MODELS = (
    main.GenerateRequest, main.CloudGenRequest, main.MsGenerateRequest,
    main.OnlineImageRequest, main.MidjourneySubmitRequest,
    main.MidjourneyActionRequest, main.MidjourneyModalRequest,
    main.CanvasVideoRequest, main.LocalAssetCaptionRequest,
    main.LocalAssetClassifyRequest, main.LocalAssetUrlImportRequest,
    main.AssetLibraryClassifyRequest,
)
TEXT_MODELS = PROMPT_MODELS + (main.ChatRequest, main.CanvasLLMRequest)


def base_payload(model):
    return {"prompt": "ok", "message": "ok", "task_id": "validation-only"}


class PromptLengthTests(unittest.TestCase):
    def test_every_generation_text_schema_boundary(self):
        for model in TEXT_MODELS:
            fields = [name for name in ("prompt", "message", "system_prompt")
                      if name in model.model_fields]
            for field in fields:
                for char, length in CASES:
                    with self.subTest(model=model.__name__, field=field, char=char, length=length):
                        data = {**base_payload(model), field: char * length}
                        if length <= 100_000:
                            self.assertEqual(len(getattr(model(**data), field)), length)
                        else:
                            with self.assertRaises(ValidationError) as raised:
                                model(**data)
                            self.assertEqual(raised.exception.errors()[0]["loc"], (field,))

    def test_classification_instruction_boundary_without_touching_owner_data(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main, "DATA_DIR", directory), patch.object(
            main, "ASSET_CLASSIFICATION_PROMPT_FILE", str(Path(directory) / "prompt.txt")
        ):
            for char, length in CASES:
                with self.subTest(char=char, length=length):
                    if length <= 100_000:
                        self.assertEqual(len(main.save_asset_classification_prompt(char * length)), length)
                    else:
                        with self.assertRaises(HTTPException) as raised:
                            main.save_asset_classification_prompt(char * length)
                        self.assertEqual(raised.exception.status_code, 400)

    def test_security_field_limits_are_preserved(self):
        for model, data in (
            (main.MidjourneyActionRequest, {"task_id": "x" * 241}),
            (main.MidjourneyActionRequest, {"task_id": "ok", "custom_id": "x" * 601}),
            (main.CanvasLLMRequest, {"message": "ok", "request_id": "x" * 81}),
        ):
            with self.subTest(model=model.__name__), self.assertRaises(ValidationError):
                model(**data)

    def test_editors_count_and_trim_unicode_characters_like_python(self):
        for name, trim, count in (
            ("canvas.js", "normalizedCanvasPromptText", "promptTextLength"),
            ("smart-canvas.js", "normalizedPromptNodeText", "promptEditorCountLabel"),
        ):
            source = (ROOT / "static/js" / name).read_text()
            # Pure functions are extracted from the shipped frontend, not reimplemented.
            functions = []
            for function in (trim, count):
                match = re.search(r"function " + function + r"\([^)]*\)\{.*?\n\}", source, re.S)
                self.assertIsNotNone(match, function)
                functions.append(match.group())
            code = "const PROMPT_TEXT_MAX_LENGTH = 100000;\n" + "\n".join(functions)
            code += "\nconst cases = " + json.dumps(CASES) + ";\n"
            code += f"console.log(JSON.stringify(cases.map(([c,n]) => [Array.from({trim}(c.repeat(n))).length, {count}(c.repeat(n))])));"
            result = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True)
            for (char, length), (actual_length, label) in zip(CASES, json.loads(result.stdout)):
                self.assertEqual(actual_length, min(length, 100_000))
                self.assertIn(str(length), str(label).replace(",", ""))
            self.assertNotIn(' maxlength="${PROMPT_TEXT_MAX_LENGTH}"', source)


class PromptHTTPBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_routes_validate_before_mocked_handler(self):
        tested = []
        transport = httpx.ASGITransport(app=main.app)
        # Fail closed if any route unexpectedly attempts an actual connection.
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden in validation tests")):
            async with httpx.AsyncClient(transport=transport, base_url="http://owner.test") as client:
                for route in main.app.routes:
                    dependant = getattr(route, "dependant", None)
                    if not dependant or "POST" not in (getattr(route, "methods", None) or set()):
                        continue
                    for body_param in dependant.body_params:
                        model = body_param.field_info.annotation
                        if model not in TEXT_MODELS:
                            continue
                        tested.append(route.path)
                        field = "prompt" if model in PROMPT_MODELS else "message"

                        def accepted(**kwargs):
                            payload = next(v for v in kwargs.values() if isinstance(v, model))
                            return {"validated_characters": len(getattr(payload, field)), "mock": True}

                        handler = (AsyncMock if inspect.iscoroutinefunction(dependant.call) else Mock)(side_effect=accepted)
                        with patch.object(dependant, "call", handler):
                            for char, length in CASES:
                                with self.subTest(route=route.path, char=char, length=length):
                                    handler.reset_mock()
                                    response = await client.post(route.path, json={**base_payload(model), field: char * length})
                                    self.assertEqual(response.status_code, 200 if length <= 100_000 else 422)
                                    if length <= 100_000:
                                        self.assertEqual(response.json()["validated_characters"], length)
                                        handler.assert_called_once()
                                    else:
                                        handler.assert_not_called()
                                        self.assertIn("100000", response.text)
        self.assertTrue({"/api/online-image", "/api/canvas-image-tasks", "/api/canvas-video", "/api/canvas-llm"}.issubset(tested))


if __name__ == "__main__":
    unittest.main()
