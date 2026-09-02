import asyncio
import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from providers.kie.models import GPT_IMAGE_2


ROOT = Path(__file__).resolve().parents[1]
SMART_PATH = ROOT / "static/js/smart-canvas.js"
SMART = SMART_PATH.read_text(encoding="utf-8")
SMART_CSS = (ROOT / "static/css/smart-canvas.css").read_text(encoding="utf-8")


def function_source(name):
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(", SMART)
    if not match:
        raise AssertionError(f"missing function: {name}")
    paren = SMART.find("(", match.start())
    paren_depth = 0
    params_end = -1
    for index in range(paren, len(SMART)):
        if SMART[index] == "(":
            paren_depth += 1
        elif SMART[index] == ")":
            paren_depth -= 1
            if paren_depth == 0:
                params_end = index
                break
    brace = SMART.find("{", params_end)
    depth = 0
    for index in range(brace, len(SMART)):
        if SMART[index] == "{":
            depth += 1
        elif SMART[index] == "}":
            depth -= 1
            if depth == 0:
                return SMART[match.start():index + 1]
    raise AssertionError(f"unterminated function: {name}")


class SmartCanvasCancelBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_01_pre_submit_cancel_never_calls_create_task(self):
        import main

        cancel_event = asyncio.Event()
        cancel_event.set()
        prepare = AsyncMock(return_value=([], []))
        client = type("Client", (), {"create_task": AsyncMock()})()
        with (
            patch.object(main, "kie_public_reference_urls", prepare),
            patch.object(main, "provider_env_key_value", return_value="mock-key"),
            patch.object(main, "KieClient", return_value=client),
        ):
            with self.assertRaises(HTTPException) as raised:
                await main.generate_kie_provider_image(
                    "mock prompt", GPT_IMAGE_2, [], {}, cancel_event=cancel_event
                )
        self.assertEqual(raised.exception.status_code, 499)
        prepare.assert_not_awaited()
        client.create_task.assert_not_awaited()

    async def test_02_cancel_after_reference_prepare_still_blocks_create_task(self):
        import main

        cancel_event = asyncio.Event()

        async def prepare(*_args, **_kwargs):
            cancel_event.set()
            return [], []

        client = type("Client", (), {"create_task": AsyncMock()})()
        with (
            patch.object(main, "kie_public_reference_urls", side_effect=prepare),
            patch.object(main, "provider_env_key_value", return_value="mock-key"),
            patch.object(main, "KieClient", return_value=client),
        ):
            with self.assertRaises(HTTPException) as raised:
                await main.generate_kie_provider_image(
                    "mock prompt", GPT_IMAGE_2, [], {}, cancel_event=cancel_event
                )
        self.assertEqual(raised.exception.status_code, 499)
        client.create_task.assert_not_awaited()

    async def test_03_post_submit_cancel_keeps_upstream_id_and_skips_poll(self):
        import main

        cancel_event = asyncio.Event()

        async def create_task(_payload):
            cancel_event.set()
            return "upstream-task-1", {}

        client = type("Client", (), {"create_task": AsyncMock(side_effect=create_task)})()
        with (
            patch.object(main, "kie_public_reference_urls", AsyncMock(return_value=([], []))),
            patch.object(main, "provider_env_key_value", return_value="mock-key"),
            patch.object(main, "KieClient", return_value=client),
            patch.object(main, "poll_kie_task", AsyncMock()) as poll,
        ):
            with self.assertRaises(HTTPException) as raised:
                await main.generate_kie_provider_image(
                    "mock prompt", GPT_IMAGE_2, [], {}, cancel_event=cancel_event
                )
        self.assertEqual(raised.exception.status_code, 499)
        self.assertEqual(getattr(raised.exception, "upstream_task_id", ""), "upstream-task-1")
        client.create_task.assert_awaited_once()
        poll.assert_not_awaited()

    async def test_04_cancel_endpoint_hard_cancels_only_pre_submit_runner(self):
        import main

        task_id = "cancel-pre-submit"
        event = asyncio.Event()
        runner = asyncio.create_task(asyncio.sleep(30))
        main.CANVAS_TASKS[task_id] = {"id": task_id, "status": "preparing"}
        main.CANVAS_TASK_CANCEL_EVENTS[task_id] = event
        main.CANVAS_TASK_RUNNERS[task_id] = runner
        result = await main.cancel_canvas_image_task(task_id)
        await asyncio.sleep(0)
        self.assertEqual(result["cancel_scope"], "pre-submit")
        self.assertTrue(event.is_set())
        self.assertTrue(runner.cancelled())
        self.assertFalse(result["upstream_cancel_supported"])
        main.CANVAS_TASKS.pop(task_id, None)

    async def test_05_submitting_cancel_is_local_only_and_does_not_abort_runner(self):
        import main

        task_id = "cancel-submitting"
        event = asyncio.Event()
        runner = asyncio.create_task(asyncio.sleep(30))
        main.CANVAS_TASKS[task_id] = {"id": task_id, "status": "submitting"}
        main.CANVAS_TASK_CANCEL_EVENTS[task_id] = event
        main.CANVAS_TASK_RUNNERS[task_id] = runner
        try:
            result = await main.cancel_canvas_image_task(task_id)
            self.assertEqual(result["cancel_scope"], "local-only")
            self.assertTrue(event.is_set())
            self.assertFalse(runner.done())
            self.assertFalse(result["upstream_cancel_supported"])
        finally:
            runner.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await runner
            main.CANVAS_TASKS.pop(task_id, None)
            main.CANVAS_TASK_CANCEL_EVENTS.pop(task_id, None)
            main.CANVAS_TASK_RUNNERS.pop(task_id, None)


class SmartCanvasCancelFrontendContractTests(unittest.TestCase):
    def test_06_running_overlay_has_clickable_cancel_and_disabled_state(self):
        overlay = function_source("nodeGenerationRunningOverlayHtml")
        bind = function_source("bindNodeEvents")
        self.assertIn("data-cancel-generation", overlay)
        self.assertIn("取消生成", overlay)
        self.assertIn("取消中...", overlay)
        self.assertIn("cancelSmartImageGeneration", bind)
        self.assertIn("pointer-events:auto", SMART_CSS)

    def test_07_cancel_marks_history_before_stopping_tasks_and_is_idempotent(self):
        cancel = function_source("cancelSmartImageGeneration")
        self.assertIn("cancellingSmartGenerationIds.has(attempt.id)", cancel)
        self.assertLess(
            cancel.index("finishNodeGenerationAttempt(node, 'cancelled'"),
            cancel.index("Promise.allSettled"),
        )
        self.assertIn("token.cancelRequested = true", cancel)
        self.assertIn("delete node.pendingTasks", cancel)

    def test_08_late_success_and_old_attempt_cannot_adopt(self):
        finalizer = function_source("finalizeSmartPendingTask")
        complete = function_source("completeNodeGenerationAttempt")
        run = function_source("runGeneration")
        self.assertLess(
            finalizer.index("generationAttempt?.status === 'cancelled'"),
            finalizer.index("node.pendingTasks ="),
        )
        self.assertIn("attempt.status !== 'running'", complete)
        self.assertIn("generationRunToken?.cancelRequested", run)
        self.assertIn("lateTaskIds.forEach(cancelSmartCanvasTask)", run)
        self.assertIn("supersededByNewAttempt", run)
        self.assertIn("const generationWasSuperseded", run)
        self.assertLess(
            run.index("const supersededByNewAttempt = generationWasSuperseded()"),
            run.index("if(!supersededByNewAttempt) pendingNode.pending = 0"),
        )
        self.assertIn("pendingNode.activeGenerationId !== generationAttempt.id", run)

    def test_09_cancelled_is_distinct_from_failure_and_survives_refresh(self):
        finish = function_source("finishNodeGenerationAttempt")
        reconcile = function_source("reconcileNodeGenerationHistory")
        self.assertIn("attempt.cancelledAt", finish)
        self.assertIn("status === 'cancelled' ? 'cancelled' : 'failed'", finish)
        self.assertIn("['running','success','failed','cancelled']", reconcile)

    def test_10_existing_uncancelled_generation_flow_remains(self):
        run = function_source("runGeneration")
        resume = function_source("resumeSmartPendingNode")
        resume_all = function_source("resumeSmartPendingTasks")
        self.assertIn("await runApiGeneration(prompt, refs)", run)
        self.assertIn("await resumeSmartPendingNode", run)
        self.assertIn("completeNodeGenerationAttempt", resume)
        self.assertIn("if(error?.smartTaskCancelled) return", resume_all)
        self.assertNotIn("upstream cancel", SMART.lower())


if __name__ == "__main__":
    unittest.main()
