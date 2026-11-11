"""预取 RunningHub 工作流时跳过没有保存参数的条目（以前每次打开画布都会对这类条目产生一个 404）。"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {name: (ROOT / f"static/js/{name}.js").read_text(encoding="utf-8") for name in ("canvas", "smart-canvas")}


def function_source(source, name):
    match = re.search(r"function\s+" + re.escape(name) + r"\s*\(", source)
    assert match, name
    depth = 0
    for index in range(source.index("{", match.end()), len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[match.start():index + 1]
    raise AssertionError(name)


class PrefetchFilterSourceTests(unittest.TestCase):
    def test_both_canvases_filter_before_prefetching(self):
        for name, source in SOURCES.items():
            self.assertIn("(rhProvider?.rh_workflows || []).filter(runningHubEntryHasSavedParams)", source, name)
            self.assertIn("function runningHubEntryHasSavedParams(entry)", source, name)

    def test_helpers_are_identical(self):
        self.assertEqual(function_source(SOURCES["canvas"], "runningHubEntryHasSavedParams"),
                         function_source(SOURCES["smart-canvas"], "runningHubEntryHasSavedParams"))


@unittest.skipUnless(shutil.which("node"), "node is required")
class PrefetchFilterBehaviourTests(unittest.TestCase):
    def test_matches_backend_payload_rule(self):
        helper = function_source(SOURCES["canvas"], "runningHubEntryHasSavedParams")
        cases = {
            "title_only": {"workflowId": "1", "title": "Minimax", "thumbnail": "data:..."},
            "empty_values": {"workflowId": "2", "fields": [], "workflowJson": {}, "raw": {}},
            "fields": {"workflowId": "3", "fields": [{"nodeId": "1"}]},
            "workflow_json": {"workflowId": "4", "workflowJson": {"1": {}}},
            "raw_only": {"workflowId": "5", "raw": {"prompt": "{}"}},
        }
        script = helper + f"\nconst cases = {json.dumps(cases)};\n" \
            "console.log(JSON.stringify(Object.fromEntries(Object.entries(cases).map(([k, v]) => [k, runningHubEntryHasSavedParams(v)]))));"
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, {"title_only": False, "empty_values": False, "fields": True, "workflow_json": True, "raw_only": True})


if __name__ == "__main__":
    unittest.main()
