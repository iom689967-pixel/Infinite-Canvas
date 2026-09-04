import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART_PATH = ROOT / "static/js/smart-canvas.js"
SMART = SMART_PATH.read_text(encoding="utf-8")


def function_source(name):
    match = re.search(rf"function\s+{re.escape(name)}\s*\(", SMART)
    if not match:
        raise AssertionError(f"missing function: {name}")
    brace = SMART.find("{", match.end())
    depth = 0
    for index in range(brace, len(SMART)):
        if SMART[index] == "{":
            depth += 1
        elif SMART[index] == "}":
            depth -= 1
            if depth == 0:
                return SMART[match.start():index + 1]
    raise AssertionError(f"unterminated function: {name}")


def run_node(source):
    completed = subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


class SmartNodeDragLazyUndoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        activate = function_source("activateNodeDrag")
        cls.runtime = run_node(
            f"""
let dragState = null;
let pendingUndoSnapshot = null;
let captureCount = 0;
let dragClassAdds = 0;
const document = {{
  body:{{
    classList:{{
      add(name){{ if(name === 'smart-node-drag') dragClassAdds += 1; }}
    }}
  }}
}};
function capturePendingUndo(){{ captureCount += 1; pendingUndoSnapshot = {{captureCount}}; }}
{activate}
const clickState = {{startX:10, startY:20, activationThreshold:0, activated:true, undoCaptured:false}};
dragState = clickState;
const click = {{activated:activateNodeDrag({{clientX:10, clientY:20}}), captureCount}};

const dragStateValue = {{startX:10, startY:20, activationThreshold:0, activated:true, undoCaptured:false}};
dragState = dragStateValue;
const firstMove = activateNodeDrag({{clientX:11, clientY:20}});
const firstMoveCaptures = captureCount;
const secondMove = activateNodeDrag({{clientX:30, clientY:40}});
const regularDrag = {{firstMove, secondMove, captures:firstMoveCaptures - click.captureCount, totalCaptures:captureCount - click.captureCount, undoCaptured:dragStateValue.undoCaptured}};

const beforeThreshold = captureCount;
const thresholdState = {{startX:0, startY:0, activationThreshold:4, activated:false, undoCaptured:false}};
dragState = thresholdState;
const underThreshold = activateNodeDrag({{clientX:3, clientY:0}});
const thresholdCaptureAfterSmallMove = captureCount - beforeThreshold;
const atThreshold = activateNodeDrag({{clientX:4, clientY:0}});
const threshold = {{underThreshold, atThreshold, captures:captureCount - beforeThreshold, afterSmallMove:thresholdCaptureAfterSmallMove, activated:thresholdState.activated}};

const beforeDetached = captureCount;
dragState = {{startX:5, startY:5, thumbDetached:true}};
const detached = {{activated:activateNodeDrag({{clientX:5, clientY:5}}), captures:captureCount - beforeDetached}};
console.log(JSON.stringify({{click, regularDrag, threshold, detached, dragClassAdds}}));
"""
        )

    def test_01_pointerdown_builds_candidate_without_snapshot(self):
        binder = function_source("bindNodeEvents")
        start = binder.index("const beginNodeDrag = e =>")
        end = binder.index("el.querySelectorAll('.node-port')", start)
        begin_drag = binder[start:end]
        self.assertIn("undoCaptured:false", begin_drag)
        self.assertNotIn("capturePendingUndo()", begin_drag)

    def test_02_click_without_movement_captures_nothing(self):
        self.assertFalse(self.runtime["click"]["activated"])
        self.assertEqual(self.runtime["click"]["captureCount"], 0)

    def test_03_first_effective_move_captures_once(self):
        state = self.runtime["regularDrag"]
        self.assertTrue(state["firstMove"])
        self.assertTrue(state["secondMove"])
        self.assertEqual(state["captures"], 1)
        self.assertEqual(state["totalCaptures"], 1)
        self.assertTrue(state["undoCaptured"])

    def test_04_text_drag_keeps_existing_activation_threshold(self):
        state = self.runtime["threshold"]
        self.assertFalse(state["underThreshold"])
        self.assertEqual(state["afterSmallMove"], 0)
        self.assertTrue(state["atThreshold"])
        self.assertEqual(state["captures"], 1)
        self.assertTrue(state["activated"])

    def test_05_snapshot_precedes_any_coordinate_write(self):
        move_start = SMART.index("window.onmousemove = e =>")
        move_end = SMART.index("\n};", move_start)
        mousemove = SMART[move_start:move_end]
        self.assertLess(mousemove.index("activateNodeDrag(e)"), mousemove.index("n.x = item.ox + moveDx"))
        activate = function_source("activateNodeDrag")
        self.assertLess(activate.index("capturePendingUndo()"), activate.index("dragState.undoCaptured = true"))

    def test_06_no_movement_mouseup_skips_terminal_drop_logic(self):
        mouseup_start = SMART.index("window.onmouseup = e =>")
        mouseup_end = SMART.index("\n};", mouseup_start)
        mouseup = SMART[mouseup_start:mouseup_end]
        guard = "if(!dragState.thumbDetached && !dragState.undoCaptured)"
        self.assertIn(guard, mouseup)
        self.assertLess(mouseup.index(guard), mouseup.index("document.elementFromPoint"))
        guard_body = mouseup[mouseup.index(guard):mouseup.index("let stateChanged = false")]
        self.assertNotIn("scheduleSave", guard_body)
        self.assertNotIn("render", guard_body)

    def test_07_detached_thumbnail_does_not_capture_again(self):
        self.assertTrue(self.runtime["detached"]["activated"])
        self.assertEqual(self.runtime["detached"]["captures"], 0)

    def test_08_lazy_capture_does_not_touch_render_save_or_edges(self):
        activate = function_source("activateNodeDrag")
        self.assertNotIn("render", activate)
        self.assertNotIn("scheduleSave", activate)
        self.assertNotIn("renderConnections", activate)
        self.assertNotIn("renderMinimap", activate)

    def test_09_undo_snapshot_payload_is_unchanged(self):
        snapshot = function_source("snapshotForUndo")
        self.assertIn("nodes: JSON.parse(JSON.stringify(nodes))", snapshot)
        self.assertIn("connections: JSON.parse(JSON.stringify(canvas?.connections || []))", snapshot)
        self.assertIn("if(stateChanged) commitPendingUndo()", SMART)


if __name__ == "__main__":
    unittest.main()
