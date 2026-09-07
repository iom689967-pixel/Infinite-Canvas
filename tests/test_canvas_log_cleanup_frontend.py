import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLASSIC_HTML = (ROOT / "static/canvas.html").read_text(encoding="utf-8")
SMART_HTML = (ROOT / "static/smart-canvas.html").read_text(encoding="utf-8")
CLASSIC_JS = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")
SMART_JS = (ROOT / "static/js/smart-canvas.js").read_text(encoding="utf-8")
SHARED_JS = (ROOT / "static/js/canvas-log-cleanup.js").read_text(encoding="utf-8")
CLASSIC_CSS = (ROOT / "static/css/canvas.css").read_text(encoding="utf-8")
SMART_CSS = (ROOT / "static/css/smart-canvas.css").read_text(encoding="utf-8")
I18N = (ROOT / "static/js/i18n/canvas.js").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")


def function_source(source, name):
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(", source)
    if not match:
        raise AssertionError(f"missing function: {name}")
    paren = source.find("(", match.start())
    paren_depth = 0
    params_end = -1
    for index in range(paren, len(source)):
        if source[index] == "(":
            paren_depth += 1
        elif source[index] == ")":
            paren_depth -= 1
            if paren_depth == 0:
                params_end = index
                break
    if params_end < 0:
        raise AssertionError(f"unterminated params: {name}")
    brace = source.find("{", params_end)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start():index + 1]
    raise AssertionError(f"unterminated function: {name}")


class CanvasLogCleanupFrontendTests(unittest.TestCase):
    def test_shared_helper_is_loaded_before_both_canvas_engines(self):
        helper = "/static/js/canvas-log-cleanup.js"
        self.assertIn(helper, CLASSIC_HTML)
        self.assertIn(helper, SMART_HTML)
        self.assertLess(CLASSIC_HTML.index(helper), CLASSIC_HTML.index("/static/js/canvas.js"))
        self.assertLess(SMART_HTML.index(helper), SMART_HTML.index("/static/js/smart-canvas.js"))

    def test_shared_request_builds_record_and_cleanup_payloads_with_version(self):
        script = f"""
global.window = global;
const calls = [];
global.fetch = async (url, options) => {{
  calls.push({{url, options}});
  return {{ok:true, status:200, json:async () => ({{canvas:{{id:'audit'}}}})}};
}};
eval({json.dumps(SHARED_JS)});
(async () => {{
  await CanvasLogCleanup.request({{canvasId:'audit', logId:'record', deleteMedia:false, baseUpdatedAt:101}});
  await CanvasLogCleanup.request({{canvasId:'audit', logId:'cleanup', deleteMedia:true, baseUpdatedAt:202}});
  console.log(JSON.stringify(calls.map(call => ({{
    url:call.url,
    method:call.options.method,
    body:JSON.parse(call.options.body),
  }}))));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        calls = json.loads(completed.stdout)
        self.assertEqual(calls[0]["url"], "/api/canvases/audit/logs/delete")
        self.assertEqual(calls[0]["method"], "POST")
        self.assertEqual(calls[0]["body"], {
            "log_id": "record",
            "delete_unreferenced_media": False,
            "reset_referencing_nodes": False,
            "base_updated_at": 101,
        })
        self.assertEqual(calls[1]["body"], {
            "log_id": "cleanup",
            "delete_unreferenced_media": True,
            "reset_referencing_nodes": True,
            "base_updated_at": 202,
        })

    def test_classic_canvas_has_two_actions_and_authoritative_sync(self):
        render = function_source(CLASSIC_JS, "renderCanvasLog")
        delete = function_source(CLASSIC_JS, "deleteCanvasLogEntry")
        self.assertIn('data-log-delete="record"', render)
        self.assertIn('data-log-delete="media"', render)
        self.assertIn("CanvasLogCleanup.request", delete)
        self.assertIn("baseUpdatedAt:Number(lastCanvasUpdatedAt || canvas.updated_at || 0)", delete)
        self.assertIn("if(response.status === 409)", delete)
        self.assertIn("applyRemoteCanvasData(remote)", delete)
        self.assertIn("applyRemoteCanvasData(data.canvas)", delete)
        self.assertNotIn("canvas.logs =", delete)
        self.assertLess(delete.index("if(response.status === 409)"), delete.index("if(!response.ok)"))

    def test_smart_canvas_has_two_actions_and_authoritative_sync(self):
        render = function_source(SMART_JS, "renderSmartCanvasLog")
        delete = function_source(SMART_JS, "deleteSmartCanvasLogEntry")
        apply = function_source(SMART_JS, "applySmartCanvasLogServerCanvas")
        self.assertIn('data-log-delete="record"', render)
        self.assertIn('data-log-delete="media"', render)
        self.assertIn("CanvasLogCleanup.request", delete)
        self.assertIn("baseUpdatedAt:Number(canvas.updated_at || 0)", delete)
        self.assertIn("if(response.status === 409)", delete)
        self.assertIn("reloadSmartCanvasAfterLogConflict(data)", delete)
        self.assertIn("applySmartCanvasLogServerCanvas(data.canvas)", delete)
        self.assertNotIn("canvas.logs =", delete)
        self.assertIn("serverCanvas.nodes", apply)
        self.assertIn("serverCanvas.connections", apply)
        self.assertIn("renderNodeGenerationHistoryPanel()", apply)
        self.assertNotIn("applyMergedServerCanvas", apply)

    def test_busy_and_failure_paths_do_not_optimistically_remove_logs(self):
        classic = function_source(CLASSIC_JS, "deleteCanvasLogEntry")
        smart = function_source(SMART_JS, "deleteSmartCanvasLogEntry")
        for source in (classic, smart):
            self.assertIn("logDeleting", source)
            self.assertIn("finally", source)
            self.assertIn("logDeleteFailed", source)
            self.assertNotIn("filter(item => item.id !== logId)", source)
        self.assertIn('aria-busy="true"', CLASSIC_JS)
        self.assertIn('aria-busy="true"', SMART_JS)

    def test_skipped_references_and_server_reset_are_reported(self):
        classic = function_source(CLASSIC_JS, "canvasLogDeleteSummary")
        smart = function_source(SMART_JS, "smartCanvasLogDeleteSummary")
        for source in (classic, smart):
            self.assertIn("removed_files", source)
            self.assertIn("reset_node_ids", source)
            self.assertIn("skipped_referenced", source)
            self.assertIn("canvas.logMediaReferenced", source)

    def test_required_i18n_and_minimal_action_styles_exist(self):
        keys = (
            "canvas.deleteLog",
            "canvas.deleteLogRecordOnly",
            "canvas.deleteLogAndMedia",
            "canvas.deleteLogConfirm",
            "canvas.deleteLogMediaConfirm",
            "canvas.logDeleting",
            "canvas.logDeleted",
            "canvas.logDeleteFailed",
            "canvas.logMediaReferenced",
            "canvas.logStale",
        )
        for key in keys:
            self.assertIn(f'"{key}"', I18N)
        self.assertIn('"common.cancel"', (ROOT / "static/js/i18n/common.js").read_text(encoding="utf-8"))
        for css in (CLASSIC_CSS, SMART_CSS):
            self.assertIn(".log-actions", css)
            self.assertIn(".log-actions button:disabled", css)
            self.assertIn(".log-item.is-deleting", css)

    def test_runtime_source_writers_remain_absent(self):
        self.assertNotIn("sync_static_html_versions", MAIN)
        self.assertNotIn("sync_runninghub_provider_workflows_to_static_template", MAIN)
        self.assertNotIn("mutate_static_runninghub_provider", MAIN)


if __name__ == "__main__":
    unittest.main()
