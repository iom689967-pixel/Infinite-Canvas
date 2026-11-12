"""C6（剩余部分）：画布 PUT 成功和 409 都只返回 updated_at，不再回传整张画布；
编辑器收到 409 后自己 GET 最新版本再三方合并，取不到时报错重试，不会只换基线就覆盖对方。"""
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main

ROOT = Path(__file__).resolve().parents[1]


def function_body(source, name):
    match = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", source)
    assert match, name
    depth = 0
    for index in range(source.index("{", match.end()), len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[match.start():index + 1]
    raise AssertionError(name)


class SlimPutResponseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (patch.object(main, "CANVAS_DIR", self.temp.name), patch.object(main, "INSTANCE_STORAGE", None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app, base_url="http://127.0.0.1")
        main.save_canvas({"id": "slim", "kind": "smart", "title": "t", "nodes": [{"id": "n", "text": "x" * 5000}],
                          "connections": [], "logs": []})

    def current(self):
        return self.client.get("/api/canvases/slim").json()["canvas"]

    def test_success_returns_only_new_updated_at(self):
        base = self.current()["updated_at"]
        response = self.client.put("/api/canvases/slim", json={"nodes": [{"id": "n2"}], "base_updated_at": base})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"updated_at"})
        self.assertEqual(body["updated_at"], self.current()["updated_at"])
        self.assertGreater(body["updated_at"], base)
        self.assertLess(len(response.content), 64)

    def test_conflict_returns_only_message_and_updated_at(self):
        current = self.current()
        response = self.client.put("/api/canvases/slim", json={"nodes": [], "base_updated_at": current["updated_at"] - 1})
        self.assertEqual(response.status_code, 409)
        detail = response.json()["detail"]
        self.assertEqual(set(detail), {"message", "updated_at"})
        self.assertEqual(detail["updated_at"], current["updated_at"])
        self.assertNotIn("xxxxx", response.text)
        self.assertEqual(self.current()["nodes"], current["nodes"])  # 被拒绝的请求没有写入


class EditorsFetchCanvasOnConflictTests(unittest.TestCase):
    classic = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")
    smart = (ROOT / "static/js/smart-canvas.js").read_text(encoding="utf-8")

    def test_classic_conflict_fetches_then_merges(self):
        body = function_body(self.classic, "saveCanvas")
        conflict = body[body.index("if(res.status === 409){"):body.index("if(!res.ok)")]
        self.assertIn("await fetchServerCanvasForMerge(canvas.id)", conflict)
        self.assertIn("mergeRemoteCanvasIntoLocal(remote)", conflict)
        self.assertIn("if(!remote) throw", conflict)
        self.assertNotIn("lastCanvasUpdatedAt = Number(data.detail", conflict)  # 不再只换基线重存

    def test_classic_success_uses_updated_at_only(self):
        body = function_body(self.classic, "saveCanvas")
        self.assertIn("data.updated_at", body)
        self.assertNotIn("...data.canvas", body)

    def test_smart_conflict_fetches_then_merges(self):
        body = function_body(self.smart, "performSmartCanvasSave")
        self.assertIn("await fetchSmartServerCanvasForMerge(canvasId)", body)
        self.assertIn("applyMergedServerCanvas(serverCanvas)", body)
        self.assertNotIn("canvas.updated_at = data.detail.updated_at", body)
        self.assertIn("data.updated_at", body)

    def test_fetch_helpers_check_the_canvas_id(self):
        self.assertIn("data?.canvas?.id === id", function_body(self.classic, "fetchServerCanvasForMerge"))
        self.assertIn("data?.canvas?.id === id", function_body(self.smart, "fetchSmartServerCanvasForMerge"))


if __name__ == "__main__":
    unittest.main()
