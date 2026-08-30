import asyncio
import json
import unittest
from io import BytesIO
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

from providers.kie.client import KieAPIError, KieClient
from providers.kie.models import (
    GPT_IMAGE_2,
    KIE_UI_MODELS,
    NANO_BANANA_PRO,
    KieValidationError,
    build_capability_schema,
    build_create_payload,
    build_routed_model_input,
)
from providers.kie.uploads import KieReferenceError, classify_reference_url, normalize_image_bytes
from providers.kie.tasks import KieTaskCancelled, KieTaskError, parse_result_urls, poll_task


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeHTTPClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class FakeTaskClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    async def query_task(self, _task_id):
        self.calls += 1
        return self.payloads.pop(0)


class KieModelTests(unittest.TestCase):
    def test_whitelist_contains_only_two_ui_models(self):
        self.assertEqual(KIE_UI_MODELS, (GPT_IMAGE_2, NANO_BANANA_PRO))

    def test_gpt_text_and_image_routes(self):
        text_payload, _ = build_create_payload(GPT_IMAGE_2, "lookbook", [], "1:1", "1K")
        image_payload, _ = build_create_payload(
            GPT_IMAGE_2,
            "edit coat",
            ["https://example.test/ref.png"],
            "16:9",
            "2K",
        )
        self.assertEqual(text_payload, {
            "model": "gpt-image-2-text-to-image",
            "input": {"prompt": "lookbook", "aspect_ratio": "1:1", "resolution": "1K"},
        })
        self.assertEqual(image_payload["model"], "gpt-image-2-image-to-image")
        self.assertEqual(image_payload["input"]["input_urls"], ["https://example.test/ref.png"])

    def test_nano_always_uses_list_and_output_format(self):
        payload, _ = build_create_payload(NANO_BANANA_PRO, "studio", [], "auto", "4K", "jpg")
        self.assertEqual(payload["model"], "nano-banana-pro")
        self.assertEqual(payload["input"]["image_input"], [])
        self.assertEqual(payload["input"]["output_format"], "jpg")

    def test_internal_gpt_image_15_route_uses_input_urls_without_ui_exposure(self):
        model_input = build_routed_model_input(
            "gpt-image/1.5-image-to-image",
            "edit",
            ["https://example.test/ref.png"],
            aspect_ratio="1:1",
            resolution="1K",
        )
        self.assertEqual(model_input["input_urls"], ["https://example.test/ref.png"])
        self.assertNotIn("gpt-image/1.5-image-to-image", KIE_UI_MODELS)

    def test_reference_source_classification_and_rgb_8bit_normalization(self):
        self.assertEqual(classify_reference_url("blob:https://canvas.test/id"), "blob")
        self.assertEqual(classify_reference_url("data:image/png;base64,AA=="), "data:image/base64")
        self.assertEqual(classify_reference_url("http://127.0.0.1:3000/assets/a.png"), "127.0.0.1/localhost URL")
        self.assertEqual(classify_reference_url("/assets/input/a.png"), "local canvas URL")
        self.assertEqual(classify_reference_url("/Users/test/a.png"), "local file path")
        self.assertEqual(classify_reference_url("https://example.test/a.png"), "public HTTPS URL")

        source = BytesIO()
        Image.new("RGBA", (3, 2), (10, 20, 30, 128)).save(source, format="PNG")
        content, meta = normalize_image_bytes(
            source.getvalue(), index=1, filename="rgba.png", max_bytes=1024 * 1024
        )
        with Image.open(BytesIO(content)) as normalized:
            self.assertEqual(normalized.mode, "RGB")
            self.assertEqual(normalized.format, "PNG")
        self.assertEqual(meta["bits_per_channel"], 8)
        self.assertGreater(meta["bytes"], 0)

    def test_invalid_or_truncated_image_reports_reference_context(self):
        with self.assertRaises(KieReferenceError) as failure:
            normalize_image_bytes(b"not-an-image", index=2, filename="bad.png", max_bytes=1024)
        message = str(failure.exception)
        self.assertIn("第2张参考图", message)
        self.assertIn("bad.png", message)
        self.assertIn("上传失败", message)
        self.assertIn("HTTP 未取得", message)
        self.assertIn("Content-Type 未取得", message)

    def test_illegal_model_ratio_and_reference_count_fail_before_request(self):
        with self.assertRaises(KieValidationError):
            build_create_payload("anything-else", "x")
        with self.assertRaises(KieValidationError):
            build_create_payload(GPT_IMAGE_2, "x", [], "5:4", "2K")
        with self.assertRaises(KieValidationError):
            build_create_payload(NANO_BANANA_PRO, "x", [f"https://e.test/{i}.png" for i in range(9)])

    def test_schema_is_model_specific(self):
        gpt = build_capability_schema(GPT_IMAGE_2)
        nano = build_capability_schema(NANO_BANANA_PRO)
        self.assertEqual(gpt["reference_image_limit"], 16)
        self.assertEqual(nano["reference_image_limit"], 8)
        self.assertFalse(any(field["key"] == "output_format" for field in gpt["fields"]))
        self.assertTrue(any(field["key"] == "output_format" for field in nano["fields"]))

    def test_result_json_parsing_and_failure(self):
        payload = {"data": {"resultJson": json.dumps({"resultUrls": ["https://e.test/out.png"]})}}
        self.assertEqual(parse_result_urls(payload), ["https://e.test/out.png"])
        with self.assertRaises(KieTaskError):
            parse_result_urls({"data": {"resultJson": "not-json"}})


class KieClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_once_and_query_paths(self):
        http = FakeHTTPClient([
            FakeResponse({"code": 200, "data": {"taskId": "task-1"}}),
            FakeResponse({"code": 200, "data": {"state": "success"}}),
        ])
        client = KieClient("secret", http_client=http)
        task_id, _ = await client.create_task({"model": "nano-banana-pro", "input": {}})
        await client.query_task(task_id)
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(http.calls[0][0:2], ("POST", "https://api.kie.ai/api/v1/jobs/createTask"))
        self.assertEqual(http.calls[1][0:2], ("GET", "https://api.kie.ai/api/v1/jobs/recordInfo?taskId=task-1"))
        self.assertEqual(http.calls[0][2]["headers"]["Authorization"], "Bearer secret")

    async def test_http_and_response_codes_are_errors(self):
        for response in (
            FakeResponse({"msg": "unauthorized"}, 401),
            FakeResponse({"code": 500, "msg": "provider failed"}, 200),
        ):
            with self.subTest(response=response.payload):
                client = KieClient("secret", http_client=FakeHTTPClient([response]))
                with self.assertRaises(KieAPIError):
                    await client.create_task({"model": "x", "input": {}})

    async def test_poll_statuses_success_failure_timeout_and_cancel(self):
        success = FakeTaskClient([
            {"data": {"state": "waiting"}},
            {"data": {"state": "queuing"}},
            {"data": {"state": "generating"}},
            {"data": {"state": "success", "resultJson": json.dumps({"resultUrls": ["https://e.test/a.png"]})}},
        ])
        result = await poll_task(success, "task-1", initial_interval=0.001, max_interval=0.001)
        self.assertEqual(result["resultUrls"], ["https://e.test/a.png"])
        self.assertEqual(success.calls, 4)

        failed = FakeTaskClient([{"data": {"state": "fail", "failCode": "E1", "failMsg": "bad"}}])
        with self.assertRaises(KieTaskError) as failure:
            await poll_task(failed, "task-2", initial_interval=0.001, max_interval=0.001)
        self.assertEqual(failure.exception.fail_code, "E1")

        timeout = FakeTaskClient([{"data": {"state": "waiting"}}] * 400)
        with self.assertRaises(TimeoutError):
            await poll_task(timeout, "task-3", timeout_seconds=0.02, initial_interval=0.005, max_interval=0.005)

        event = asyncio.Event()
        event.set()
        canceled = FakeTaskClient([])
        with self.assertRaises(KieTaskCancelled):
            await poll_task(canceled, "task-4", cancel_event=event, initial_interval=0.001)
        self.assertEqual(canceled.calls, 0)


class KieServerWhitelistTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_only_pipeline_builds_text_route_without_upload_or_paid_network(self):
        import main

        created = []

        class CaptureClient:
            def __init__(self, api_key, base_url):
                self.api_key = api_key
                self.base_url = base_url

            async def create_task(self, payload):
                created.append(payload)
                return "mock-task", {"code": 200, "data": {"taskId": "mock-task"}}

        with patch.object(main, "prepare_kie_references", AsyncMock(return_value=([], []))) as prepare, patch.object(
            main, "provider_env_key_value", return_value="test-secret"
        ), patch.object(main, "KieClient", CaptureClient), patch.object(
            main,
            "poll_kie_task",
            AsyncMock(return_value={"resultUrls": ["https://example.test/text.png"]}),
        ):
            image, _raw = await main.generate_kie_provider_image(
                "minimal studio product image",
                GPT_IMAGE_2,
                [],
                {"id": "kie"},
                aspect_ratio="1:1",
                resolution="1K",
            )

        prepare.assert_awaited_once()
        self.assertEqual(image["value"], "https://example.test/text.png")
        self.assertEqual(created, [{
            "model": "gpt-image-2-text-to-image",
            "input": {
                "prompt": "minimal studio product image",
                "aspect_ratio": "1:1",
                "resolution": "1K",
            },
        }])

    async def test_api_settings_protocol_validation_is_static_and_non_paid(self):
        import main

        payload = main.TestConnectionPayload(
            provider_id="kie",
            base_url="https://api.kie.ai",
            protocol="openai",
            image_request_mode="openai",
        )
        with patch.object(main, "provider_env_key_value", return_value="test-secret"), patch.object(
            main.httpx,
            "AsyncClient",
            side_effect=AssertionError("Kie protocol validation must not make an upstream request"),
        ):
            result = await main.probe_async_endpoint(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(result["protocol"], "kie")
        self.assertEqual(result["image_request_mode"], "kie")
        self.assertEqual(result["model_count"], 2)
        self.assertEqual(result["model_names"], {
            "gpt-image-2": "GPT Image 2",
            "nano-banana-pro": "Nano Banana Pro",
        })
        self.assertNotIn("Ark", result["message"])
        self.assertNotIn("OpenAI", result["message"])

    async def test_api_settings_address_validation_only_contacts_kie_root(self):
        import main

        calls = []

        class AddressClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, **kwargs):
                calls.append((url, kwargs))
                return type("AddressResponse", (), {"status_code": 404})()

        payload = main.TestConnectionPayload(
            provider_id="kie",
            base_url="https://api.kie.ai",
            protocol="openai",
        )
        with patch.object(main, "provider_env_key_value", return_value="test-secret"), patch.object(
            main.httpx,
            "AsyncClient",
            return_value=AddressClient(),
        ):
            result = await main.test_provider_connection(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(result["protocol"], "kie")
        self.assertEqual(result["model_count"], 2)
        self.assertEqual(calls, [("https://api.kie.ai", {"headers": {"Accept": "text/html,application/json"}})])
        self.assertNotIn("Authorization", calls[0][1]["headers"])

    async def test_server_normalizes_kie_to_static_whitelist(self):
        import main

        normalized = main.normalize_provider({
            "id": "kie",
            "name": "changed",
            "base_url": "https://wrong.test",
            "protocol": "openai",
            "image_models": ["forbidden-model"],
        })
        self.assertEqual(normalized["base_url"], "https://api.kie.ai")
        self.assertEqual(normalized["protocol"], "kie")
        self.assertEqual(tuple(normalized["image_models"]), KIE_UI_MODELS)

        schema = await main.image_params("kie", GPT_IMAGE_2)
        self.assertEqual(schema["provider_id"], "kie")
        self.assertEqual(schema["reference_image_limit"], 16)

    async def test_local_cancel_sets_event_without_upstream_call(self):
        import main

        task_id = "test-kie-cancel"
        event = asyncio.Event()
        main.CANVAS_TASKS[task_id] = {"id": task_id, "status": "generating"}
        main.CANVAS_TASK_CANCEL_EVENTS[task_id] = event
        try:
            result = await main.cancel_canvas_image_task(task_id)
            self.assertEqual(result["status"], "canceled")
            self.assertTrue(event.is_set())
            self.assertEqual(main.CANVAS_TASKS[task_id]["status"], "canceled")
        finally:
            main.CANVAS_TASKS.pop(task_id, None)
            main.CANVAS_TASK_CANCEL_EVENTS.pop(task_id, None)

    async def test_recovery_query_dispatches_to_kie_adapter(self):
        import main

        queried = []

        class RecoveryClient:
            def __init__(self, api_key, base_url):
                self.api_key = api_key
                self.base_url = base_url

            async def query_task(self, task_id):
                queried.append((self.base_url, task_id))
                return {"code": 200, "data": {"state": "generating"}}

        payload = main.ImageTaskQueryRequest(provider_id="kie", task_id="kie-task-1")
        with patch.object(main, "KieClient", RecoveryClient), patch.object(
            main,
            "fetch_image_task_payload",
            AsyncMock(side_effect=AssertionError("Kie must not use the legacy task endpoint")),
        ):
            result = await main.query_image_task(payload)

        self.assertEqual(queried, [("https://api.kie.ai", "kie-task-1")])
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["upstream_status"], "generating")

    async def test_recovery_query_parses_kie_result_json(self):
        import main

        class RecoveryClient:
            def __init__(self, api_key, base_url):
                self.base_url = base_url

            async def query_task(self, _task_id):
                return {
                    "code": 200,
                    "data": {
                        "state": "success",
                        "resultJson": json.dumps({"resultUrls": ["https://example.test/result.png"]}),
                    },
                }

        payload = main.ImageTaskQueryRequest(provider_id="kie", task_id="kie-task-2")
        with patch.object(main, "KieClient", RecoveryClient), patch.object(
            main,
            "save_ai_image_to_output",
            AsyncMock(return_value="/assets/output/recovered.png"),
        ) as save_image, patch.object(main, "image_output_meta", return_value={"url": "/assets/output/recovered.png"}), patch.object(
            main,
            "save_to_history",
        ), patch.object(main, "GLOBAL_LOOP", None), patch.object(
            main,
            "fetch_image_task_payload",
            AsyncMock(side_effect=AssertionError("Kie must not use the legacy task endpoint")),
        ):
            result = await main.query_image_task(payload)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["images"], ["/assets/output/recovered.png"])
        save_image.assert_awaited_once_with(
            {"type": "url", "value": "https://example.test/result.png"},
            prefix="online_",
        )

    async def test_recovery_query_parses_kie_fail_code_and_message(self):
        import main

        class RecoveryClient:
            def __init__(self, api_key, base_url):
                self.base_url = base_url

            async def query_task(self, _task_id):
                return {"code": 200, "data": {"state": "fail", "failCode": "E42", "failMsg": "bad input"}}

        payload = main.ImageTaskQueryRequest(provider_id="kie", task_id="kie-task-3")
        with patch.object(main, "KieClient", RecoveryClient), patch.object(
            main,
            "fetch_image_task_payload",
            AsyncMock(side_effect=AssertionError("Kie must not use the legacy task endpoint")),
        ):
            result = await main.query_image_task(payload)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["fail_code"], "E42")
        self.assertEqual(result["error"], "bad input")


if __name__ == "__main__":
    unittest.main()
