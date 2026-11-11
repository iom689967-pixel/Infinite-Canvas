import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class CanvasSaveSemanticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.canvases = Path(self.temp.name) / "canvases"
        self.canvases.mkdir()
        patcher = patch.object(main, "CANVAS_DIR", str(self.canvases))
        patcher.start()
        self.addAsyncCleanup(self._stop, patcher)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://127.0.0.1:3000")
        canvas = main.new_canvas("Original", "sparkles", "smart")
        self.cid = canvas["id"]
        res = await self.client.put(f"/api/canvases/{self.cid}", json={
            "title": "Original", "icon": "sparkles", "nodes": [{"id": "n", "x": 1}], "connections": [],
            "viewport": {"x": 5, "y": 6, "scale": 1}, "logs": [{"id": "log-1"}], "settings": {"k": 1},
            "base_updated_at": canvas["updated_at"],
        })
        self.assertEqual(res.status_code, 200)

    async def _stop(self, patcher):
        await self.client.aclose()
        patcher.stop()

    async def current(self):
        return (await self.client.get(f"/api/canvases/{self.cid}")).json()["canvas"]

    async def test_omitted_fields_are_kept(self):
        cur = await self.current()
        res = await self.client.put(f"/api/canvases/{self.cid}", json={"nodes": [{"id": "n", "x": 9}], "base_updated_at": cur["updated_at"]})
        self.assertEqual(res.status_code, 200)
        after = await self.current()
        self.assertEqual(after["title"], "Original")
        self.assertEqual(after["icon"], "sparkles")
        self.assertEqual(after["logs"], [{"id": "log-1"}])
        self.assertEqual(after["settings"], {"k": 1})
        self.assertEqual(after["viewport"], {"x": 5, "y": 6, "scale": 1})
        self.assertEqual(after["nodes"], [{"id": "n", "x": 9}])

    async def test_explicit_empty_values_still_apply(self):
        cur = await self.current()
        res = await self.client.put(f"/api/canvases/{self.cid}", json={"logs": [], "settings": {}, "base_updated_at": cur["updated_at"]})
        self.assertEqual(res.status_code, 200)
        after = await self.current()
        self.assertEqual(after["logs"], [])
        self.assertEqual(after["settings"], {})
        self.assertEqual(after["nodes"], [{"id": "n", "x": 1}])

    async def test_conflict_contract(self):
        cur = await self.current()
        res = await self.client.put(f"/api/canvases/{self.cid}", json={"nodes": [], "base_updated_at": cur["updated_at"] - 1})
        self.assertEqual(res.status_code, 409)
        # C6：409 不再带回整张画布（编辑器自己 GET 最新版本再合并）
        self.assertEqual(sorted(res.json()["detail"].keys()), ["message", "updated_at"])
        res = await self.client.put(f"/api/canvases/{self.cid}", json={"nodes": [{"id": "n", "x": 2}], "base_updated_at": 0})
        self.assertEqual(res.status_code, 200)

    async def test_conflict_check_waits_for_lock_holder(self):
        """另一线程持锁写入期间到达的 PUT，必须在锁内看到新版本并返回 409。"""
        cur = await self.current()
        base = cur["updated_at"]
        holding = threading.Event()
        release = threading.Event()

        def writer():
            with main.CANVAS_LOCK:
                canvas = main.load_canvas(self.cid)
                holding.set()
                release.wait(5)
                canvas["logs"] = []
                main.save_canvas(canvas)

        thread = threading.Thread(target=writer)
        thread.start()
        holding.wait(5)
        threading.Timer(0.2, release.set).start()
        res = await self.client.put(f"/api/canvases/{self.cid}", json={"logs": cur["logs"], "base_updated_at": base})
        thread.join(5)
        self.assertEqual(res.status_code, 409)
        self.assertEqual((await self.current())["logs"], [])


if __name__ == "__main__":
    unittest.main()
