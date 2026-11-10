import contextlib
import copy
import io
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from providers.kie.client import KieAPIError, KieClient
from providers.kie.models import GPT_IMAGE_2, build_create_payload
from providers.kie.safe_log import log_kie_event

import main


PROMPT_MARKER = "PROMPT_SECRET_MARKER"
KEY_MARKER = "KEY_SECRET_MARKER"
SIGNED_MARKER = "SIGNED_SECRET_MARKER"
NESTED_MARKER = "NESTED_SECRET_MARKER"
EXCEPTION_MARKER = "EXCEPTION_SECRET_MARKER"


def structured_events(stream):
    events = []
    for line in stream.getvalue().splitlines():
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and str(value.get("event") or "").startswith("kie_"):
            events.append(value)
    return events


def assert_no_sensitive(test, output):
    for marker in (
        PROMPT_MARKER,
        KEY_MARKER,
        SIGNED_MARKER,
        NESTED_MARKER,
        EXCEPTION_MARKER,
        "Authorization",
        "requestUrl",
        "referenceAudits",
        '"payload"',
    ):
        test.assertNotIn(marker, output)


class FakeResponse:
    def __init__(self, status_code, payload=None, *, json_error=None):
        self.status_code = status_code
        self.payload = payload
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeHTTPClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class KieLogAllowlistTests(unittest.TestCase):
    def test_unknown_and_sensitive_fields_are_dropped(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            record = log_kie_event(
                "kie_create_request",
                trace_id="kie_deadbeefdeadbeef",
                provider="kie",
                model=GPT_IMAGE_2,
                stage="submit",
                reference_count=2,
                image_count=1,
                resolution="1K",
                aspect_ratio="1:1",
                elapsed_ms=12.3456,
                prompt=PROMPT_MARKER,
                payload={"nested": NESTED_MARKER},
                authorization=f"Bearer {KEY_MARKER}",
                reference_url=f"https://example.invalid/image.png?sig={SIGNED_MARKER}",
                exception=RuntimeError(EXCEPTION_MARKER),
            )

        self.assertEqual(
            set(record),
            {
                "time", "event", "trace_id", "provider", "model", "stage",
                "reference_count", "image_count", "resolution", "aspect_ratio",
                "elapsed_ms",
            },
        )
        assert_no_sensitive(self, output.getvalue())

    def test_invalid_identifiers_cannot_smuggle_urls_into_summary(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            record = log_kie_event(
                "kie_create_request",
                trace_id="kie_0123456789abcdef",
                provider=f"https://private.invalid/{SIGNED_MARKER}",
                model=f"https://private.invalid/{SIGNED_MARKER}",
                stage="submit",
            )
        self.assertNotIn("provider", record)
        self.assertNotIn("model", record)
        assert_no_sensitive(self, output.getvalue())


class KieClientLogRedactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_failure_timeout_and_invalid_response_are_summarized(self):
        signed_base = f"https://private-upstream.invalid/{SIGNED_MARKER}"
        original_payload = {
            "model": "mock-image",
            "input": {
                "prompt": PROMPT_MARKER,
                "image_input": [f"https://media.invalid/ref.png?sig={SIGNED_MARKER}"],
                "nested": {"secret": NESTED_MARKER},
            },
        }
        snapshot = copy.deepcopy(original_payload)
        request = httpx.Request("POST", f"{signed_base}/create?token={KEY_MARKER}")
        http = FakeHTTPClient([
            FakeResponse(200, {"code": 200, "data": {"taskId": f"task-{SIGNED_MARKER}"}}),
            FakeResponse(503, {"code": EXCEPTION_MARKER, "msg": f"{PROMPT_MARKER}-{NESTED_MARKER}"}),
            httpx.ReadTimeout(f"timeout {EXCEPTION_MARKER}", request=request),
            FakeResponse(200, json_error=ValueError(f"invalid {EXCEPTION_MARKER}")),
        ])
        client = KieClient(
            KEY_MARKER,
            base_url=signed_base,
            http_client=http,
            trace_id="kie_0123456789abcdef",
            provider_id="kie",
            model=GPT_IMAGE_2,
        )
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            task_id, _ = await client.create_task(original_payload)
            with self.assertRaises(KieAPIError):
                await client.create_task(original_payload)
            with self.assertRaises(KieAPIError):
                await client.create_task(original_payload)
            with self.assertRaises(KieAPIError):
                await client.create_task(original_payload)

        self.assertEqual(task_id, f"task-{SIGNED_MARKER}")
        self.assertIs(http.calls[0][2]["json"], original_payload)
        self.assertEqual(original_payload, snapshot)
        events = structured_events(output)
        self.assertTrue(any(item["event"] == "kie_create_task" and item.get("http_status") == 200 for item in events))
        failures = [item for item in events if item["event"] == "kie_request_failed"]
        self.assertEqual(
            {item.get("error_category") for item in failures},
            {"upstream", "timeout", "invalid_response"},
        )
        self.assertTrue(all(item.get("stage") == "submit" for item in failures))
        self.assertTrue(all("elapsed_ms" in item for item in events))
        assert_no_sensitive(self, output.getvalue())


class KiePipelineLogRedactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_logs_summary_but_sends_identical_payload(self):
        reference = {
            "url": "/assets/reference.png",
            "name": f"garment-{NESTED_MARKER}.png",
            "metadata": {"instruction": PROMPT_MARKER},
        }
        reference_snapshot = copy.deepcopy(reference)
        signed_reference = f"https://media.invalid/ref.png?signature={SIGNED_MARKER}"
        signed_result = f"https://media.invalid/result.png?signature={SIGNED_MARKER}"
        created = []

        class CaptureClient:
            def __init__(self, api_key, *, base_url):
                self.api_key = api_key
                self.base_url = base_url

            async def create_task(self, payload):
                created.append(payload)
                return "mock-task-id", {"code": 200, "data": {"taskId": "mock-task-id"}}

        expected, _ = build_create_payload(
            GPT_IMAGE_2,
            PROMPT_MARKER,
            [signed_reference],
            aspect_ratio="1:1",
            resolution="1K",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(
            main,
            "kie_public_reference_urls",
            AsyncMock(return_value=(
                [signed_reference],
                [{"public_url": signed_reference, "nested": {"secret": NESTED_MARKER}}],
            )),
        ), patch.object(main, "provider_env_key_value", return_value=KEY_MARKER), patch.object(
            main, "KieClient", CaptureClient
        ), patch.object(
            main,
            "poll_kie_task",
            AsyncMock(return_value={"resultUrls": [signed_result]}),
        ):
            image, raw = await main.generate_kie_provider_image(
                PROMPT_MARKER,
                GPT_IMAGE_2,
                [reference],
                {"id": "kie"},
                aspect_ratio="1:1",
                resolution="1K",
            )

        self.assertEqual(created, [expected])
        self.assertEqual(reference, reference_snapshot)
        self.assertEqual(image["value"], signed_result)
        self.assertEqual(raw["images"][0]["url"], signed_result)
        events = structured_events(output)
        request_event = next(item for item in events if item["event"] == "kie_create_request")
        self.assertEqual(request_event["provider"], "kie")
        self.assertEqual(request_event["model"], GPT_IMAGE_2)
        self.assertEqual(request_event["reference_count"], 1)
        self.assertEqual(request_event["image_count"], 1)
        self.assertEqual(request_event["resolution"], "1K")
        self.assertEqual(request_event["aspect_ratio"], "1:1")
        self.assertEqual(request_event["stage"], "submit")
        self.assertIn("elapsed_ms", request_event)
        assert_no_sensitive(self, output.getvalue())

    async def test_unexpected_exception_is_logged_without_its_message(self):
        class FailingClient:
            def __init__(self, api_key, *, base_url):
                self.api_key = api_key
                self.base_url = base_url

            async def create_task(self, _payload):
                raise RuntimeError(f"failure {EXCEPTION_MARKER} {SIGNED_MARKER}")

        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(
            main, "kie_public_reference_urls", AsyncMock(return_value=([], []))
        ), patch.object(main, "provider_env_key_value", return_value=KEY_MARKER), patch.object(
            main, "KieClient", FailingClient
        ):
            with self.assertRaises(HTTPException) as raised:
                await main.generate_kie_provider_image(
                    PROMPT_MARKER, GPT_IMAGE_2, [], {"id": "kie"}, resolution="1K"
                )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.detail, "Kie 请求发生内部错误")
        failure = next(item for item in structured_events(output) if item["event"] == "kie_request_failed")
        self.assertEqual(failure["error_category"], "internal")
        self.assertEqual(failure["stage"], "unknown")
        self.assertIn("elapsed_ms", failure)
        assert_no_sensitive(self, output.getvalue())

    async def test_reference_http_error_keeps_status_without_logging_detail(self):
        original_error = HTTPException(
            status_code=400,
            detail=f"invalid reference {SIGNED_MARKER} {PROMPT_MARKER}",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(
            main,
            "kie_public_reference_urls",
            AsyncMock(side_effect=original_error),
        ):
            with self.assertRaises(HTTPException) as raised:
                await main.generate_kie_provider_image(
                    PROMPT_MARKER, GPT_IMAGE_2, [], {"id": "kie"}, resolution="1K"
                )

        self.assertIs(raised.exception, original_error)
        failure = next(item for item in structured_events(output) if item["event"] == "kie_request_failed")
        self.assertEqual(failure["http_status"], 400)
        self.assertEqual(failure["error_category"], "reference")
        self.assertEqual(failure["stage"], "prepare")
        assert_no_sensitive(self, output.getvalue())


if __name__ == "__main__":
    unittest.main()
