"""页面版本过期（后端已更新、页面未刷新）：过期页面不能保存画布；未保存的修改以草稿暂存、刷新后恢复。"""
import json
import os
import re
import shutil
import subprocess
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


class BuildIdTests(unittest.TestCase):
    def build_for(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, content in files.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            with patch.object(main, "PROGRAM_ROOT", str(root)), patch.object(main, "STATIC_DIR", str(root / "static")):
                return main.compute_client_build_id()

    def test_changes_only_when_code_content_changes(self):
        base = {"VERSION": "1", "main.py": "x", "static/js/a.js": "a", "static/canvas.html": "<html>",
                "providers/kie/client.py": "k", "static/css/s.css": "c"}
        first = self.build_for(base)
        self.assertEqual(first, self.build_for(dict(base)))
        for rel in ("static/js/a.js", "main.py", "providers/kie/client.py", "static/canvas.html", "VERSION", "static/css/s.css"):
            changed = dict(base, **{rel: base[rel] + "!"})
            self.assertNotEqual(first, self.build_for(changed), rel)
        # 不相关的文件（数据、备份文件）不影响构建号
        self.assertEqual(first, self.build_for(dict(base, **{"data/x.json": "{}", "static/js/a.js.bak": "zzz"})))

    def test_build_id_shape(self):
        self.assertRegex(main.CLIENT_BUILD_ID, r"^[0-9a-f]{16}$")


class OutdatedGuardApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (patch.object(main, "CANVAS_DIR", self.temp.name), patch.object(main, "INSTANCE_STORAGE", None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app, base_url="http://127.0.0.1")
        main.save_canvas({"id": "og", "kind": "smart", "title": "t", "nodes": [{"id": "n"}], "connections": [], "logs": []})

    def current(self):
        return self.client.get("/api/canvases/og").json()["canvas"]

    def put(self, headers=None):
        return self.client.put("/api/canvases/og", headers=headers or {},
                               json={"nodes": [{"id": "changed"}], "base_updated_at": self.current()["updated_at"]})

    def test_outdated_page_cannot_save(self):
        response = self.put({"X-Client-Build": "0000000000000000"})
        self.assertEqual(response.status_code, 412)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "client_outdated")
        self.assertEqual(detail["client_build"], main.CLIENT_BUILD_ID)
        self.assertIn("刷新", detail["message"])
        self.assertEqual(self.current()["nodes"], [{"id": "n"}])

    def test_current_page_and_header_less_callers_can_save(self):
        self.assertEqual(self.put({"X-Client-Build": main.CLIENT_BUILD_ID}).status_code, 200)
        self.assertEqual(self.put().status_code, 200)

    def test_meta_reports_current_build(self):
        self.assertEqual(self.client.get("/api/canvases/og/meta").json()["client_build"], main.CLIENT_BUILD_ID)

    def test_every_page_carries_the_build_meta(self):
        for page in ("canvas.html", "smart-canvas.html", "canvas-list.html", "online.html"):
            html = self.client.get(f"/static/{page}").text
            self.assertIn(f'<meta name="ic-client-build" content="{main.CLIENT_BUILD_ID}">', html, page)


@unittest.skipUnless(shutil.which("node"), "node is required")
class DraftPatchTests(unittest.TestCase):
    def run_js(self, body):
        script = "const M = require(%s);\n%s" % (json.dumps(str(ROOT / "static/js/canvas-merge.js")), body)
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_patch_records_only_changes_and_restores_onto_newer_server(self):
        out = self.run_js("""
const base = [{id:'a', x:1}, {id:'b', x:1}, {id:'c', x:1}, {id:'d', x:1}, {id:'e', x:1}];
// 本页：改 a、改 b、删 c、删 e、新增 n；连线新增 a->n，删 a->b
const local = [{id:'a', x:2}, {id:'b', x:5}, {id:'d', x:1}, {id:'n', x:9}];
const patch = M.buildDraftPatch({localNodes:local, baseNodes:base,
  localConnections:[{id:'c1', from:'a', to:'n'}], baseConnections:[{id:'c0', from:'a', to:'b'}]});
// 服务器在此期间：b 被别人改了 y、d 没动、e 被别人改了、a 被别人删了
const server = [{id:'b', x:1, y:7}, {id:'c', x:1}, {id:'d', x:1}, {id:'e', x:3}];
const merged = M.applyDraftPatch({serverNodes:server, serverConnections:[{id:'c0', from:'a', to:'b'}, {id:'c9', from:'b', to:'d'}],
  patch, mergeNode:(l, r, b) => M.threeWayMergeFields(l, r, b)});
console.log(JSON.stringify({count:M.draftPatchCount(patch), changed:patch.changed.map(c => c.id).sort(), removed:patch.removed.map(r => r.id).sort(),
  nodes:merged.nodes, connections:merged.connections.map(c => c.id), report:merged.report}));
""")
        self.assertEqual(out["changed"], ["a", "b", "n"])  # d 没改不记
        self.assertEqual(out["removed"], ["c", "e"])
        self.assertEqual(out["count"], 7)
        nodes = {n["id"]: n for n in out["nodes"]}
        self.assertNotIn("a", nodes)                       # 服务器已删除，不复活
        self.assertEqual(nodes["b"], {"id": "b", "x": 5, "y": 7})  # 两边都改：字段合并
        self.assertNotIn("c", nodes)                       # 服务器没动，删除生效
        self.assertEqual(nodes["e"], {"id": "e", "x": 3})  # 服务器改过，保留不删
        self.assertEqual(nodes["n"], {"id": "n", "x": 9})  # 新增
        self.assertEqual(out["connections"], ["c9"])      # c0 被删；c1 指向的 a 已不存在
        self.assertEqual(out["report"], {"applied": 2, "merged": 1, "skippedDeletedOnServer": 1, "keptChangedOnServer": 1, "connections": 2})

    def test_no_changes_means_empty_patch(self):
        out = self.run_js("""
const nodes = [{id:'a', x:1, images:[]}];
const patch = M.buildDraftPatch({localNodes:[{id:'a', images:[], x:1}], baseNodes:nodes, localConnections:[], baseConnections:[]});
console.log(JSON.stringify({count:M.draftPatchCount(patch)}));
""")
        self.assertEqual(out, {"count": 0})


class EditorWiringTests(unittest.TestCase):
    classic = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")
    smart = (ROOT / "static/js/smart-canvas.js").read_text(encoding="utf-8")

    def test_saves_send_build_header_and_stop_when_outdated(self):
        for source, save in ((self.classic, "saveCanvas"), (self.smart, "performSmartCanvasSave")):
            body = function_body(source, save)
            self.assertIn("StalePage.withBuildHeader(", body)
            self.assertIn("res.status === 412", body)
            self.assertIn("enterOutdatedMode()", body)
        self.assertIn("if(pageOutdated){ refreshOutdatedDraft(); return; }", function_body(self.classic, "saveCanvas"))
        self.assertIn("if(pageOutdated){ refreshOutdatedDraft(); return Promise.resolve(); }", function_body(self.smart, "saveCanvas"))
        for source in (self.classic, self.smart):
            # 关页面时的 keepalive 画布保存同样带构建号
            keepalive_puts = re.findall(r"fetch\(`/api/canvases/[^`]*`, \{method:'PUT'.*?keepalive:true\}", source)
            self.assertEqual(len(keepalive_puts), 1)
            self.assertIn("StalePage.withBuildHeader(", keepalive_puts[0])

    def test_meta_poll_detects_outdated_page_and_load_offers_restore(self):
        self.assertIn("StalePage.serverBuildDiffers(meta.client_build)", function_body(self.classic, "checkRemoteCanvasVersion"))
        self.assertIn("StalePage.serverBuildDiffers(meta.client_build)", function_body(self.smart, "startCanvasMetaPoll"))
        self.assertIn("offerOutdatedDraftRestore();", function_body(self.classic, "openCanvas"))
        self.assertIn("offerOutdatedDraftRestore();", function_body(self.smart, "loadCanvas"))

    def test_pages_load_the_module_and_instance_mode_allows_it(self):
        for page in ("canvas.html", "smart-canvas.html"):
            html = (ROOT / "static" / page).read_text(encoding="utf-8")
            self.assertLess(html.index("/static/js/stale-page.js"), html.index("/static/js/canvas-merge.js"))
        self.assertIn('"/static/js/stale-page.js"', (ROOT / "instance_access.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
