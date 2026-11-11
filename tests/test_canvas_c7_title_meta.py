"""C7：在列表里改的名字（POST /meta）不能被编辑器的保存改回去。

- /meta 改标题或图标时不推进 updated_at（列表排序不变），但记下 meta_updated_at；
- PUT 的基线早于 meta_updated_at 时，请求里的标题/图标是旧值，不再覆盖，其他字段照常保存；
- 不带基线的 PUT（显式改名）照常生效；
- 两个编辑器的自动保存不再发送标题/图标，侧栏改名/改图标改走 /meta。
"""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main

ROOT = Path(__file__).resolve().parents[1]


class CanvasTitleMetaApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        patchers = [patch.object(main, "CANVAS_DIR", str(self.dir)), patch.object(main, "INSTANCE_STORAGE", None)]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app, base_url="http://127.0.0.1")
        self.clock = [1_000_000]
        clock = patch.object(main, "now_ms", side_effect=self.tick)
        clock.start()
        self.addCleanup(clock.stop)
        self.canvas_id = "c7test"
        main.save_canvas({"id": self.canvas_id, "kind": "smart", "title": "原名", "icon": "sparkles",
                          "nodes": [{"id": "n1"}], "connections": [], "logs": []})

    def tick(self):
        self.clock[0] += 10
        return self.clock[0]

    def get(self):
        response = self.client.get(f"/api/canvases/{self.canvas_id}")
        self.assertEqual(response.status_code, 200)
        return response.json()["canvas"]

    def meta(self, **patch_body):
        response = self.client.post(f"/api/canvases/{self.canvas_id}/meta", json=patch_body)
        self.assertEqual(response.status_code, 200, response.text)

    def put(self, **body):
        return self.client.put(f"/api/canvases/{self.canvas_id}", json=body)

    def test_meta_rename_keeps_updated_at_and_records_meta_time(self):
        before = self.get()
        self.meta(title="列表里改的名字")
        after = self.get()
        self.assertEqual(after["title"], "列表里改的名字")
        self.assertEqual(after["updated_at"], before["updated_at"])
        self.assertGreater(after["meta_updated_at"], before["updated_at"])

    def test_other_meta_fields_do_not_mark_title_icon(self):
        self.meta(pinned=True, color="", owner="me")
        self.assertNotIn("meta_updated_at", self.get())
        self.meta(title="原名")  # 标题没变也不记
        self.assertNotIn("meta_updated_at", self.get())

    def test_stale_editor_put_keeps_renamed_title_and_icon_but_saves_nodes(self):
        before = self.get()
        self.meta(title="列表里改的名字", icon="star")
        response = self.put(title=before["title"], icon=before["icon"], nodes=[{"id": "n1"}, {"id": "n2"}],
                            base_updated_at=before["updated_at"])
        self.assertEqual(response.status_code, 200, response.text)
        after = self.get()
        self.assertEqual(after["title"], "列表里改的名字")
        self.assertEqual(after["icon"], "star")
        self.assertEqual([node["id"] for node in after["nodes"]], ["n1", "n2"])
        self.assertGreater(after["updated_at"], before["updated_at"])

    def test_put_based_on_post_rename_state_can_still_set_title(self):
        before = self.get()
        self.meta(title="列表里改的名字")
        self.assertEqual(self.put(nodes=[{"id": "n1"}], base_updated_at=before["updated_at"]).status_code, 200)
        current = self.get()
        self.assertGreaterEqual(current["updated_at"], current["meta_updated_at"])
        response = self.put(title="编辑器里明确改的名字", base_updated_at=current["updated_at"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.get()["title"], "编辑器里明确改的名字")

    def test_put_without_base_is_an_explicit_rename(self):
        self.meta(title="列表里改的名字")
        response = self.put(title="旧客户端的显式改名", nodes=[{"id": "n1"}])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.get()["title"], "旧客户端的显式改名")

    def test_put_without_title_keeps_title(self):
        self.meta(title="列表里改的名字")
        current = self.get()
        self.assertEqual(self.put(nodes=[{"id": "n1"}], base_updated_at=current["updated_at"]).status_code, 200)
        self.assertEqual(self.get()["title"], "列表里改的名字")

    def test_canvas_without_meta_time_behaves_as_before(self):
        before = self.get()
        response = self.put(title="新名字", nodes=[{"id": "n1"}], base_updated_at=before["updated_at"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.get()["title"], "新名字")


def function_body(source, name):
    match = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", source)
    assert match, name
    depth = 0
    for index in range(source.index("{", match.end()), len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[match.start():index + 1]
    raise AssertionError(name)


class EditorsStopSendingTitleTests(unittest.TestCase):
    classic = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")
    smart = (ROOT / "static/js/smart-canvas.js").read_text(encoding="utf-8")

    def test_classic_autosave_body_has_no_title_or_icon(self):
        body = function_body(self.classic, "buildCanvasSaveBody")
        self.assertNotRegex(body, r"\btitle\s*:")
        self.assertNotRegex(body, r"\bicon\s*:")
        self.assertIn("base_updated_at", body)

    def test_smart_autosave_body_has_no_title_or_icon(self):
        start = self.smart.index("const body = JSON.stringify({\n        nodes:storageCanvas.nodes")
        body = self.smart[start:self.smart.index("});", start)]
        self.assertNotRegex(body, r"\btitle\s*:")
        self.assertNotRegex(body, r"\bicon\s*:")

    def test_classic_sidebar_rename_and_icon_use_meta(self):
        for name in ("setCanvasTitle", "setCanvasIcon"):
            body = function_body(self.classic, name)
            self.assertIn("patchCanvasMeta(", body, name)
            self.assertNotIn("'PUT'", body, name)

    def test_editors_pick_up_title_from_meta_poll(self):
        self.assertIn("applyRemoteCanvasTitleIcon(meta)", function_body(self.classic, "checkRemoteCanvasVersion"))
        self.assertIn("applyRemoteSmartTitleIcon(meta)", function_body(self.smart, "startCanvasMetaPoll"))

    def test_classic_merge_takes_title_and_icon_from_server(self):
        body = function_body(self.classic, "mergeRemoteCanvasIntoLocal")
        self.assertIn("['title', 'icon', 'owner'", body)
        self.assertNotIn("canvas.title = merged.title", body)


if __name__ == "__main__":
    unittest.main()
