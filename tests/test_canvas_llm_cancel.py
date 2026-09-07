import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

import main


class CancelAwareClient:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class CanvasLLMCancelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.CANVAS_LLM_ACTIVE_REQUESTS.clear()
        main.CANVAS_LLM_CANCEL_MARKERS.clear()

    async def asyncTearDown(self):
        for task in tuple(main.CANVAS_LLM_ACTIVE_REQUESTS.values()):
            if not task.done():
                task.cancel()
        await asyncio.sleep(0)
        main.CANVAS_LLM_ACTIVE_REQUESTS.clear()
        main.CANVAS_LLM_CANCEL_MARKERS.clear()

    def payload(self, request_id):
        return main.CanvasLLMRequest(
            message="reply OK only",
            provider="gemini",
            model="gemini-3.8-flash",
            request_id=request_id,
        )

    async def test_active_cancel_interrupts_upstream_await_and_returns_499(self):
        started = asyncio.Event()
        upstream_cancelled = asyncio.Event()

        async def waiting_impl(payload, request_id, started_at):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                upstream_cancelled.set()
                raise

        with patch.object(main, "_canvas_llm_impl", side_effect=waiting_impl):
            request_task = asyncio.create_task(main.canvas_llm(self.payload("request-active")))
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertIs(main.CANVAS_LLM_ACTIVE_REQUESTS["request-active"], request_task)

            result = await main.cancel_canvas_llm(
                main.CanvasLLMCancelRequest(request_id="request-active")
            )
            with self.assertRaises(HTTPException) as raised:
                await request_task

        self.assertEqual(raised.exception.status_code, 499)
        self.assertTrue(result["active"])
        self.assertTrue(upstream_cancelled.is_set())
        self.assertNotIn("request-active", main.CANVAS_LLM_ACTIVE_REQUESTS)
        self.assertNotIn("request-active", main.CANVAS_LLM_CANCEL_MARKERS)

    async def test_active_cancel_reaches_the_awaited_httpx_post(self):
        client = CancelAwareClient()
        provider = {
            "id": "openai-compatible",
            "name": "OpenAI-compatible",
            "protocol": "openai",
        }

        with patch.object(main, "get_api_provider", return_value=provider), patch.object(
            main,
            "resolve_chat_provider",
            return_value=(
                "https://upstream.test/v1",
                {"Authorization": "Bearer hidden"},
                "mock-chat",
            ),
        ), patch.object(main.httpx, "AsyncClient", return_value=client):
            task = asyncio.create_task(
                main.canvas_llm(
                    main.CanvasLLMRequest(
                        message="reply OK only",
                        provider="openai-compatible",
                        model="mock-chat",
                        request_id="request-httpx",
                    )
                )
            )
            await asyncio.wait_for(client.started.wait(), timeout=1)
            await main.cancel_canvas_llm(
                main.CanvasLLMCancelRequest(request_id="request-httpx")
            )
            with self.assertRaises(HTTPException) as raised:
                await task

        self.assertEqual(raised.exception.status_code, 499)
        self.assertTrue(client.cancelled.is_set())
        self.assertTrue(client.calls[0][0].endswith("/v1/chat/completions"))
        self.assertNotIn("request-httpx", main.CANVAS_LLM_ACTIVE_REQUESTS)
        self.assertNotIn("request-httpx", main.CANVAS_LLM_CANCEL_MARKERS)

    async def test_cancel_endpoint_returns_499_from_the_original_http_request(self):
        started = asyncio.Event()

        async def waiting_impl(payload, request_id, started_at):
            started.set()
            await asyncio.Future()

        transport = httpx.ASGITransport(app=main.app)
        with patch.object(main, "_canvas_llm_impl", side_effect=waiting_impl):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://canvas.test"
            ) as client:
                original_request = asyncio.create_task(
                    client.post(
                        "/api/canvas-llm",
                        json={
                            "message": "reply OK only",
                            "provider": "gemini",
                            "model": "gemini-3.8-flash",
                            "request_id": "request-asgi",
                        },
                    )
                )
                await asyncio.wait_for(started.wait(), timeout=1)
                cancel_response = await client.post(
                    "/api/canvas-llm/cancel",
                    json={"request_id": "request-asgi"},
                )
                original_response = await asyncio.wait_for(original_request, timeout=1)

        self.assertEqual(cancel_response.status_code, 200)
        self.assertTrue(cancel_response.json()["active"])
        self.assertEqual(original_response.status_code, 499)
        self.assertIn("用户取消", original_response.json()["detail"])
        self.assertNotIn("request-asgi", main.CANVAS_LLM_ACTIVE_REQUESTS)

    async def test_early_cancel_marker_prevents_upstream_start(self):
        result = await main.cancel_canvas_llm(
            main.CanvasLLMCancelRequest(request_id="request-early")
        )
        implementation = AsyncMock(return_value={"text": "must not run"})

        with patch.object(main, "_canvas_llm_impl", implementation):
            with self.assertRaises(HTTPException) as raised:
                await main.canvas_llm(self.payload("request-early"))

        self.assertEqual(raised.exception.status_code, 499)
        self.assertFalse(result["active"])
        self.assertTrue(result["pending_registration"])
        implementation.assert_not_awaited()
        self.assertNotIn("request-early", main.CANVAS_LLM_ACTIVE_REQUESTS)
        self.assertNotIn("request-early", main.CANVAS_LLM_CANCEL_MARKERS)

    async def test_cancel_one_request_does_not_cancel_another(self):
        started = {"request-a": asyncio.Event(), "request-b": asyncio.Event()}
        release_b = asyncio.Event()

        async def isolated_impl(payload, request_id, started_at):
            started[request_id].set()
            if request_id == "request-a":
                await asyncio.Future()
            await release_b.wait()
            return {"text": "B"}

        with patch.object(main, "_canvas_llm_impl", side_effect=isolated_impl):
            task_a = asyncio.create_task(main.canvas_llm(self.payload("request-a")))
            task_b = asyncio.create_task(main.canvas_llm(self.payload("request-b")))
            await asyncio.wait_for(
                asyncio.gather(started["request-a"].wait(), started["request-b"].wait()),
                timeout=1,
            )
            await main.cancel_canvas_llm(main.CanvasLLMCancelRequest(request_id="request-a"))
            release_b.set()
            with self.assertRaises(HTTPException) as raised:
                await task_a
            result_b = await asyncio.wait_for(task_b, timeout=1)

        self.assertEqual(raised.exception.status_code, 499)
        self.assertEqual(result_b, {"text": "B"})
        self.assertEqual(main.CANVAS_LLM_ACTIVE_REQUESTS, {})
        self.assertEqual(main.CANVAS_LLM_CANCEL_MARKERS, {})

    async def test_registry_cleanup_after_success_error_timeout_and_unexpected_error(self):
        outcomes = (
            ("success", {"text": "OK"}, None),
            ("http-error", None, HTTPException(status_code=502, detail="upstream failed")),
            ("timeout", None, HTTPException(status_code=504, detail="timed out")),
            ("unexpected", None, RuntimeError("boom")),
        )
        for suffix, result, error in outcomes:
            request_id = f"request-{suffix}"

            async def implementation(payload, actual_request_id, started_at):
                self.assertEqual(actual_request_id, request_id)
                if error:
                    raise error
                return result

            with self.subTest(outcome=suffix), patch.object(
                main, "_canvas_llm_impl", side_effect=implementation
            ):
                if error:
                    with self.assertRaises(type(error)):
                        await main.canvas_llm(self.payload(request_id))
                else:
                    self.assertEqual(await main.canvas_llm(self.payload(request_id)), result)

            self.assertNotIn(request_id, main.CANVAS_LLM_ACTIVE_REQUESTS)
            self.assertNotIn(request_id, main.CANVAS_LLM_CANCEL_MARKERS)

    async def test_duplicate_active_request_id_is_rejected_without_replacing_owner(self):
        started = asyncio.Event()

        async def waiting_impl(payload, request_id, started_at):
            started.set()
            await asyncio.Future()

        with patch.object(main, "_canvas_llm_impl", side_effect=waiting_impl):
            owner = asyncio.create_task(main.canvas_llm(self.payload("request-duplicate")))
            await asyncio.wait_for(started.wait(), timeout=1)
            with self.assertRaises(HTTPException) as raised:
                await main.canvas_llm(self.payload("request-duplicate"))
            self.assertIs(main.CANVAS_LLM_ACTIVE_REQUESTS["request-duplicate"], owner)
            await main.cancel_canvas_llm(
                main.CanvasLLMCancelRequest(request_id="request-duplicate")
            )
            with self.assertRaises(HTTPException):
                await owner

        self.assertEqual(raised.exception.status_code, 409)

    def test_request_id_validation_rejects_unsafe_values(self):
        with self.assertRaises(ValueError):
            main.CanvasLLMRequest(
                message="reply OK only",
                request_id="bad/request?id=secret",
            )
        with self.assertRaises(ValueError):
            main.CanvasLLMCancelRequest(request_id="")

    def test_expired_cancel_markers_are_removed(self):
        main.CANVAS_LLM_CANCEL_MARKERS.update({"old": 1.0, "new": 25.0})
        with patch.object(main, "CANVAS_LLM_CANCEL_MARKER_TTL", 20.0):
            main.cleanup_canvas_llm_cancel_markers(now=30.0)
        self.assertNotIn("old", main.CANVAS_LLM_CANCEL_MARKERS)
        self.assertIn("new", main.CANVAS_LLM_CANCEL_MARKERS)


if __name__ == "__main__":
    unittest.main()
