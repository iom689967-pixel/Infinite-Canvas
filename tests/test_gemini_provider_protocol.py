import json
import unittest
from pathlib import Path
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://api.cannacoastpackaging.com/antigravity"


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.content = self.text.encode("utf-8")
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class CaptureClient:
    def __init__(self, response):
        self.responses = response if isinstance(response, list) else [response]
        self.calls = []

    def next_response(self):
        index = min(len(self.calls), len(self.responses) - 1)
        return self.responses[index]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kwargs):
        response = self.next_response()
        self.calls.append(("GET", url, kwargs))
        return response

    async def post(self, url, **kwargs):
        response = self.next_response()
        self.calls.append(("POST", url, kwargs))
        return response


class GeminiProviderProtocolTests(unittest.TestCase):
    def test_models_endpoint_normalizes_version_suffixes(self):
        expected = f"{BASE_URL}/v1beta/models"
        for base_url in (
            BASE_URL,
            f"{BASE_URL}/",
            f"{BASE_URL}/v1",
            f"{BASE_URL}/v1beta",
            f"{BASE_URL}/v1/v1",
            f"{BASE_URL}/v1/v1beta",
            f"{BASE_URL}/v1beta/v1beta",
            f"{BASE_URL}/v1beta/v1",
            f"{BASE_URL}/v1beta/models",
            f"{BASE_URL}/v1/models",
        ):
            with self.subTest(base_url=base_url):
                self.assertEqual(main.upstream_models_url(base_url, "gemini"), expected)

    def test_gemini_models_are_classified_by_capability_not_version(self):
        grouped, model_ids = main.parse_upstream_models({
            "models": [
                {"name": "models/gemini-2.5-flash"},
                {"name": "models/gemini-3.1-pro-high"},
                {"name": "models/gemini-3.1-flash-image-preview"},
                {"name": "models/gemini-3.8-flash"},
                {"name": "models/gemini-3.8-flash-tiered"},
                {"name": "models/gemini-video-preview"},
                {"name": "models/gpt-oss-120b-medium"},
                {"name": "models/veo-3.1-generate-preview"},
            ]
        }, "gemini")
        self.assertEqual(grouped["chat"], [
            "gemini-2.5-flash",
            "gemini-3.1-pro-high",
            "gemini-3.8-flash",
            "gemini-3.8-flash-tiered",
            "gemini-video-preview",
        ])
        self.assertEqual(grouped["image"], ["gemini-3.1-flash-image-preview"])
        self.assertEqual(grouped["video"], [])
        self.assertEqual(len(model_ids), 6)
        self.assertNotIn("gpt-oss-120b-medium", model_ids)
        self.assertNotIn("veo-3.1-generate-preview", model_ids)

    def test_generate_content_request_uses_native_gemini_endpoint_and_body(self):
        provider = {"id": "antigravity", "base_url": BASE_URL, "protocol": "gemini"}
        url, body = main.chat_upstream_request(
            provider,
            f"{BASE_URL}/v1beta",
            "models/gemini-3.1-pro-high",
            [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "Reply OK."},
                {"role": "assistant", "content": "Previous reply."},
            ],
        )
        self.assertEqual(url, f"{BASE_URL}/v1beta/models/gemini-3.1-pro-high:generateContent")
        self.assertNotIn("model", body)
        self.assertEqual(body["systemInstruction"]["parts"], [{"text": "Be concise."}])
        self.assertEqual(body["contents"], [
            {"role": "user", "parts": [{"text": "Reply OK."}]},
            {"role": "model", "parts": [{"text": "Previous reply."}]},
        ])
        self.assertEqual(
            main.text_from_chat_response({"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}),
            "OK",
        )

    def test_api_settings_preserves_manually_selected_gemini(self):
        source = (ROOT / "static/js/api-settings.js").read_text(encoding="utf-8")
        self.assertNotIn("function preservesManualProtocol(protocol)", source)
        self.assertGreaterEqual(source.count("currentProtocol !== 'gemini'"), 2)


class GeminiProviderNetworkPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_address_validation_only_requests_gemini_models_endpoint(self):
        client = CaptureClient(FakeResponse({"models": [{"name": "models/gemini-3.1-pro-high"}]}))
        payload = main.TestConnectionPayload(
            provider_id="antigravity",
            base_url=BASE_URL,
            api_key="test-secret",
            protocol="gemini",
        )
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.test_provider_connection(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(result["protocol"], "gemini")
        self.assertEqual(result["chat_models"], ["gemini-3.1-pro-high"])
        self.assertEqual(result["image_models"], [])
        self.assertEqual([call[1] for call in client.calls], [
            f"{BASE_URL}/v1beta/models",
            "https://api.cannacoastpackaging.com/v1/models",
        ])
        self.assertEqual(client.calls[0][2]["headers"]["x-goog-api-key"], "test-secret")
        self.assertEqual(client.calls[1][2]["headers"]["Authorization"], "Bearer test-secret")

    async def test_protocol_probe_does_not_fall_back_to_openai(self):
        client = CaptureClient(FakeResponse({"models": [{"name": "models/gemini-3.1-pro-high"}]}))
        payload = main.TestConnectionPayload(
            provider_id="antigravity",
            base_url=BASE_URL,
            api_key="test-secret",
            protocol="gemini",
        )
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.probe_async_endpoint(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(result["protocol"], "gemini")
        self.assertEqual(result["status_code"], 200)
        self.assertEqual([call[1] for call in client.calls], [
            f"{BASE_URL}/v1beta/models",
            "https://api.cannacoastpackaging.com/v1/models",
        ])

    async def test_fetch_models_returns_gemini_protocol_and_llm_category(self):
        client = CaptureClient([
            FakeResponse({"models": [
                {"name": "models/gemini-3.1-pro-high"},
                {"name": "models/gemini-3.1-flash-image-preview"},
            ]}),
            FakeResponse({"data": [
                {"id": "gemini-3.8-flash"},
                {"id": "gemini-3.8-flash-tiered"},
                {"id": "gpt-oss-120b-medium"},
            ]}),
        ])
        with patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.fetch_models_from_upstream(BASE_URL, "test-secret", "gemini")

        self.assertEqual(result["protocol"], "gemini")
        self.assertEqual(result["chat_models"], [
            "gemini-3.1-pro-high",
            "gemini-3.8-flash",
            "gemini-3.8-flash-tiered",
        ])
        self.assertEqual(result["image_models"], ["gemini-3.1-flash-image-preview"])
        self.assertEqual(result["video_models"], [])
        self.assertNotIn("gpt-oss-120b-medium", result["all"])
        self.assertEqual([call[1] for call in client.calls], [
            f"{BASE_URL}/v1beta/models",
            "https://api.cannacoastpackaging.com/v1/models",
        ])

    async def test_smart_canvas_llm_posts_native_generate_content_request(self):
        provider = {
            "id": "antigravity",
            "name": "Antigravity Gemini",
            "base_url": BASE_URL,
            "protocol": "gemini",
            "chat_models": ["gemini-3.1-pro-high"],
        }
        client = CaptureClient(FakeResponse({
            "candidates": [{"content": {"parts": [{"text": "OK"}]}}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
        }))
        payload = main.CanvasLLMRequest(
            message="Reply OK.",
            system_prompt="Be concise.",
            provider="antigravity",
            model="gemini-3.1-pro-high",
        )
        with patch.object(main, "get_api_provider", return_value=provider), patch.object(
            main, "provider_env_key_value", return_value="test-secret"
        ), patch.object(main.httpx, "AsyncClient", return_value=client):
            result = await main.canvas_llm(payload)

        self.assertEqual(result["text"], "OK")
        self.assertEqual(result["raw_usage"]["candidatesTokenCount"], 1)
        self.assertEqual(len(client.calls), 1)
        method, url, kwargs = client.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, f"{BASE_URL}/v1beta/models/gemini-3.1-pro-high:generateContent")
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "test-secret")
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertEqual(kwargs["json"]["contents"][-1]["parts"], [{"text": "Reply OK."}])


if __name__ == "__main__":
    unittest.main()
