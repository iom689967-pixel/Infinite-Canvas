"""C9：本地模式的画布生成任务持久化 + TTL；重启后按实例模式（27982b4）的状态约定恢复，并复用 /refresh 只查询原任务。"""
import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main

ROOT = Path(__file__).resolve().parents[1]


class LocalLedgerTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = str(Path(self.temp.name) / "canvas_tasks.sqlite3")
        patchers = [
            patch.object(main, "CANVAS_TASK_DB_PATH", self.db_path),
            patch.dict(main.CANVAS_TASKS, {}, clear=True),
            patch.dict(main.CANVAS_TASK_RUNNERS, {}, clear=True),
            patch.object(main.PATHS, "explicit", False),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def put(self, task_id, **fields):
        task = {"id": task_id, "type": "online-image", "status": "queued", "created_at": time.time(),
                "updated_at": time.time(), "result": None, "error": "", "provider_id": "kie", "model": "m"}
        task.update(fields)
        main.CANVAS_TASKS[task_id] = task
        main.persist_canvas_task(task_id)
        return task

    def rows(self):
        with sqlite3.connect(self.db_path) as db:
            return {row[0]: json.loads(row[1]) for row in db.execute("SELECT id, data FROM jobs")}

    def restart(self):
        main.CANVAS_TASKS.clear()
        return main.restore_canvas_tasks()


class RestartMappingTests(LocalLedgerTestCase):
    def test_restart_maps_unfinished_tasks_without_resubmitting(self):
        self.put("t-downloading", status="generating", upstream_task_id="up-1", upstream_status="success")
        self.put("t-generating", status="generating", upstream_task_id="up-2", upstream_status="generating")
        self.put("t-queued", status="queued")
        self.put("t-done", status="succeeded", result={"images": ["/assets/output/a.png"], "raw": {"secret": "x"}})
        self.put("t-jimeng", status="jimeng_pending", submit_id="s1")
        self.assertEqual(self.restart(), 5)
        tasks = main.CANVAS_TASKS
        self.assertEqual(tasks["t-downloading"]["status"], "result_recovery_required")
        self.assertEqual(tasks["t-downloading"]["recovery"], "query-existing")
        self.assertEqual(tasks["t-generating"]["status"], "failed")
        self.assertEqual(tasks["t-generating"]["recovery"], "query-existing")
        self.assertEqual(tasks["t-generating"]["upstream_task_id"], "up-2")
        self.assertIn("不会重新提交", tasks["t-generating"]["error"])
        self.assertEqual(tasks["t-queued"]["status"], "failed")
        self.assertEqual(tasks["t-queued"]["recovery"], "")
        self.assertIn("重新生成", tasks["t-queued"]["error"])
        self.assertEqual(tasks["t-done"]["status"], "succeeded")
        self.assertEqual(tasks["t-done"]["result"], {"images": ["/assets/output/a.png"]})  # raw 不落盘
        self.assertEqual(tasks["t-jimeng"]["status"], "jimeng_pending")
        # 恢复后的状态也写回账本：再重启一次结果不变
        self.restart()
        self.assertEqual(main.CANVAS_TASKS["t-generating"]["status"], "failed")

    def test_get_after_restart_returns_task_instead_of_404(self):
        self.put("t-done", status="succeeded", result={"images": ["/assets/output/a.png"]})
        self.restart()
        client = TestClient(main.app, base_url="http://127.0.0.1")
        response = client.get("/api/canvas-image-tasks/t-done")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["images"], ["/assets/output/a.png"])

    def test_ttl_prunes_old_finished_tasks_from_memory_and_ledger(self):
        old = time.time() - main.CANVAS_TASK_TTL_SECONDS - 60
        self.put("old-done", status="succeeded", updated_at=old)
        self.put("old-running", status="generating", updated_at=old, upstream_task_id="up")
        self.put("fresh-done", status="succeeded")
        self.assertEqual(main.prune_canvas_tasks(), 1)
        self.assertNotIn("old-done", main.CANVAS_TASKS)
        self.assertEqual(set(self.rows()), {"old-running", "fresh-done"})

    def test_ledger_is_capped(self):
        with patch.object(main, "CANVAS_TASK_MAX_ROWS", 3):
            for index in range(5):
                self.put(f"t{index}", status="succeeded", updated_at=time.time() + index)
            main.prune_canvas_tasks()
        self.assertEqual(set(self.rows()), {"t2", "t3", "t4"})

    def test_instance_mode_does_not_use_the_local_ledger(self):
        with patch.object(main.PATHS, "explicit", True):
            self.put("inst", status="queued")
            self.assertEqual(main.restore_canvas_tasks(), 0)
        self.assertFalse(Path(self.db_path).exists())


class RunnerPersistsUpstreamIdTests(LocalLedgerTestCase):
    def test_upstream_id_is_on_disk_before_the_result_arrives(self):
        release = asyncio.Event()

        async def fake_build(payload, cancel_event=None, status_callback=None):
            status_callback("generating", {"taskId": "up-live"})
            await release.wait()
            return {"images": ["/assets/output/x.png"]}

        async def scenario():
            self.put("t-live", status="queued")
            payload = main.OnlineImageRequest(provider_id="kie", model="m", prompt="p")
            with patch.object(main, "build_online_image_result", side_effect=fake_build):
                runner = asyncio.create_task(main.run_canvas_image_task("t-live", payload))
                for _ in range(50):
                    await asyncio.sleep(0.01)
                    if self.rows().get("t-live", {}).get("upstream_task_id"):
                        break
                mid = self.rows()["t-live"]
                release.set()
                await runner
            return mid, self.rows()["t-live"]

        mid, final = asyncio.run(scenario())
        self.assertEqual(mid["upstream_task_id"], "up-live")
        self.assertEqual(mid["status"], "generating")
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["result"]["images"], ["/assets/output/x.png"])


class LocalRefreshTests(LocalLedgerTestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app, base_url="http://127.0.0.1")
        self.providers = [{"id": "kie", "name": "Kie", "enabled": True}]
        patcher = patch.object(main, "load_api_providers", side_effect=lambda: self.providers)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.queries = []

    def query_returning(self, result):
        async def fake(payload):
            self.queries.append((payload.provider_id, payload.task_id))
            return result
        return patch.object(main, "query_image_task", side_effect=fake)

    def restored_recovery_task(self):
        self.put("t-rec", status="generating", upstream_task_id="up-9", upstream_status="success")
        self.restart()
        self.assertEqual(main.CANVAS_TASKS["t-rec"]["status"], "result_recovery_required")

    def test_refresh_queries_only_the_original_task_and_persists_result(self):
        self.restored_recovery_task()
        with self.query_returning({"status": "succeeded", "images": ["/assets/output/r.png"], "image_items": [], "raw": {"k": 1}}):
            response = self.client.post("/api/canvas-image-tasks/t-rec/refresh")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["result"]["images"], ["/assets/output/r.png"])
        self.assertNotIn("raw", body["result"])
        self.assertEqual(self.queries, [("kie", "up-9")])
        self.restart()
        self.assertEqual(main.CANVAS_TASKS["t-rec"]["status"], "succeeded")
        # 已完成的任务再次恢复不会再查询上游
        with self.query_returning({"status": "succeeded", "images": ["/x.png"]}):
            self.client.post("/api/canvas-image-tasks/t-rec/refresh")
        self.assertEqual(len(self.queries), 1)

    def test_refresh_while_upstream_still_generating_keeps_recovery_state(self):
        self.restored_recovery_task()
        with self.query_returning({"status": "generating", "message": "仍在生成"}):
            body = self.client.post("/api/canvas-image-tasks/t-rec/refresh").json()
        self.assertEqual(body["status"], "result_recovery_required")
        self.assertEqual(body["error"], "仍在生成")

    def test_refresh_reports_upstream_failure(self):
        self.restored_recovery_task()
        with self.query_returning({"status": "failed", "error": "内容审核未通过"}):
            body = self.client.post("/api/canvas-image-tasks/t-rec/refresh").json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["upstream_status"], "fail")

    def test_refresh_refuses_without_upstream_id_or_original_provider(self):
        self.put("t-none", status="queued")
        self.restart()
        response = self.client.post("/api/canvas-image-tasks/t-none/refresh")
        self.assertEqual(response.status_code, 409)
        self.restored_recovery_task()
        self.providers = []
        with self.query_returning({"status": "succeeded", "images": ["/x.png"]}):
            response = self.client.post("/api/canvas-image-tasks/t-rec/refresh")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.queries, [])  # 不会改用其他平台查询

    def test_refresh_rejects_override_parameters_and_unknown_tasks(self):
        self.restored_recovery_task()
        self.assertEqual(self.client.post("/api/canvas-image-tasks/t-rec/refresh", json={"provider_id": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/api/canvas-image-tasks/nope/refresh").status_code, 404)


class FrontendRecoveryWiringTests(unittest.TestCase):
    def test_classic_poll_handles_recovery_via_refresh(self):
        source = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")
        self.assertIn("/refresh`, {method:'POST'}", source)
        poll = source[source.index("async function pollCanvasImageTask"):source.index("async function waitCanvasImageTaskResult")]
        self.assertIn("data.status === 'result_recovery_required'", poll)
        self.assertIn("recoverCanvasImageTask(taskId", poll)
        wait = source[source.index("async function waitCanvasImageTaskResult"):source.index("function completeCanvasImageTask")]
        self.assertIn("data.status === 'result_recovery_required'", wait)

    def test_smart_already_handles_both_recovery_paths(self):
        source = (ROOT / "static/js/smart-canvas.js").read_text(encoding="utf-8")
        self.assertIn("task.status === 'result_recovery_required'", source)
        self.assertIn("/refresh`, {method:'POST'}", source)
        self.assertIn("recoverTaskId && !smartTaskHasTerminalFailure", source)


if __name__ == "__main__":
    unittest.main()
