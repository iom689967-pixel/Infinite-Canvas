"""C7：在列表里改的名字（POST /meta）不能被编辑器的保存改回去。

- /meta 改标题或图标时不推进 updated_at（列表排序不变）；
- 第六批起 PUT 完全忽略标题和图标（只能走 /meta），并且 base_updated_at 必填且大于 0；
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

    def test_meta_rename_keeps_updated_at(self):
        before = self.get()
        self.meta(title="列表里改的名字", icon="star")
        after = self.get()
        self.assertEqual((after["title"], after["icon"]), ("列表里改的名字", "star"))
        self.assertEqual(after["updated_at"], before["updated_at"])
        self.assertNotIn("meta_updated_at", after)  # C7 的时间比较已删除，不再记录

    def test_put_ignores_title_and_icon_but_saves_everything_else(self):
        before = self.get()
        response = self.put(title="编辑器里的旧名字", icon="old", nodes=[{"id": "n1"}, {"id": "n2"}],
                            base_updated_at=before["updated_at"])
        self.assertEqual(response.status_code, 200, response.text)
        after = self.get()
        self.assertEqual((after["title"], after["icon"]), ("原名", "sparkles"))
        self.assertEqual([node["id"] for node in after["nodes"]], ["n1", "n2"])

    def test_stale_editor_put_cannot_revert_list_rename(self):
        before = self.get()
        self.meta(title="列表里改的名字")
        # 编辑器打开时的基线、改名前的标题：照常保存节点，标题不受影响
        self.assertEqual(self.put(title=before["title"], nodes=[{"id": "n9"}], base_updated_at=before["updated_at"]).status_code, 200)
        current = self.get()
        # 基线晚于改名也一样：PUT 根本不改标题
        self.assertEqual(self.put(title="又一个旧名字", nodes=[{"id": "n9"}], base_updated_at=current["updated_at"]).status_code, 200)
        self.assertEqual(self.get()["title"], "列表里改的名字")

    def test_base_is_required_and_positive(self):
        for body in ({"title": "x", "nodes": []}, {"nodes": [], "base_updated_at": 0}, {"nodes": [], "base_updated_at": -5}):
            response = self.put(**body)
            self.assertEqual(response.status_code, 422, body)
            self.assertIn("基线", response.json()["detail"])
        self.assertEqual(self.get()["title"], "原名")
        self.assertEqual(self.get()["nodes"], [{"id": "n1"}])

    def test_leftover_meta_time_is_removed_on_save(self):
        path = self.dir / f"{self.canvas_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["meta_updated_at"] = data["updated_at"] + 999
        path.write_text(json.dumps(data), encoding="utf-8")
        current = self.get()
        self.assertEqual(self.put(nodes=[{"id": "n1"}], base_updated_at=current["updated_at"]).status_code, 200)
        self.assertNotIn("meta_updated_at", json.loads(path.read_text(encoding="utf-8")))

    def test_legacy_canvas_without_updated_at_can_still_be_saved(self):
        path = self.dir / f"{self.canvas_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("updated_at")
        path.write_text(json.dumps(data), encoding="utf-8")
        current = self.get()
        self.assertGreater(current["updated_at"], 0)
        self.assertEqual(self.put(nodes=[{"id": "n5"}], base_updated_at=current["updated_at"]).status_code, 200)
        self.assertEqual(self.get()["nodes"], [{"id": "n5"}])


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
