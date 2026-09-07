import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException

import main


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.content = self.text.encode("utf-8")

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


class ClientFactory:
    def __init__(self, client):
        self.client = client
        self.timeouts = []

    def __call__(self, *args, **kwargs):
        self.timeouts.append(kwargs.get("timeout"))
        return self.client


class CanvasLLMTimeoutTests(unittest.IsolatedAsyncioTestCase):
    def test_diagnostic_url_never_logs_credentials_or_query_tokens(self):
        self.assertEqual(
            main.request_url_for_log(
                "https://user:password@upstream.test/v1/chat/completions?api_key=secret#token"
            ),
            "https://upstream.test/v1/chat/completions",
        )

    def test_canvas_llm_has_independent_120_second_default(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn(
            'CANVAS_LLM_TIMEOUT = float(os.getenv("CANVAS_LLM_TIMEOUT", "120"))',
            source,
        )
        self.assertIn(
            'IMAGE_TASK_TIMEOUT = float(os.getenv("IMAGE_TASK_TIMEOUT", str(AI_REQUEST_TIMEOUT)))',
            source,
        )

    async def test_timeout_returns_504_and_uses_canvas_llm_timeout(self):
        request = httpx.Request("POST", "https://upstream.test/v1/chat/completions")
        client = FakeClient(error=httpx.ReadTimeout("mock timeout", request=request))
        factory = ClientFactory(client)
        provider = {
            "id": "openai-compatible",
            "name": "OpenAI-compatible",
            "protocol": "openai",
        }
        payload = main.CanvasLLMRequest(
            message="reply OK only",
            provider="openai-compatible",
            model="mock-chat",
        )
        with patch.object(main, "get_api_provider", return_value=provider), patch.object(
            main,
            "resolve_chat_provider",
            return_value=("https://upstream.test/v1", {"Authorization": "Bearer hidden"}, "mock-chat"),
        ), patch.object(main.httpx, "AsyncClient", new=factory):
            with self.assertRaises(HTTPException) as raised:
                await main.canvas_llm(payload)

        self.assertEqual(raised.exception.status_code, 504)
        self.assertIn(f"{main.CANVAS_LLM_TIMEOUT:g} 秒", raised.exception.detail)
        self.assertEqual(factory.timeouts, [main.CANVAS_LLM_TIMEOUT])

    async def test_gemini_openai_and_modelscope_keep_their_protocol_paths(self):
        cases = (
            (
                "gemini",
                {"id": "gemini", "protocol": "gemini"},
                "https://upstream.test/v1beta",
                "gemini-3.8-flash",
                {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]},
                "/v1beta/models/gemini-3.8-flash:generateContent",
            ),
            (
                "openai-compatible",
                {"id": "openai-compatible", "protocol": "openai"},
                "https://upstream.test/v1",
                "mock-chat",
                {"choices": [{"message": {"content": "OK"}}]},
                "/v1/chat/completions",
            ),
            (
                "modelscope",
                {},
                "https://modelscope.test/v1",
                "Qwen/mock-chat",
                {"choices": [{"message": {"content": "OK"}}]},
                "/v1/chat/completions",
            ),
        )
        for provider_id, provider, chat_base, model, response, expected_suffix in cases:
            with self.subTest(provider=provider_id):
                client = FakeClient(response=FakeResponse(response))
                factory = ClientFactory(client)
                payload = main.CanvasLLMRequest(
                    message="reply OK only",
                    provider=provider_id,
                    model=model,
                    ms_model=model if provider_id == "modelscope" else "",
                )
                with patch.object(main, "get_api_provider", return_value=provider), patch.object(
                    main,
                    "resolve_chat_provider",
                    return_value=(chat_base, {"Authorization": "Bearer hidden"}, model),
                ), patch.object(main.httpx, "AsyncClient", new=factory):
                    result = await main.canvas_llm(payload)

                self.assertEqual(result["text"], "OK")
                self.assertEqual(factory.timeouts, [main.CANVAS_LLM_TIMEOUT])
                self.assertTrue(client.calls[0][0].endswith(expected_suffix), client.calls[0][0])


if __name__ == "__main__":
    unittest.main()
