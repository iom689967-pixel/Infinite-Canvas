import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART_PATH = ROOT / "static/js/smart-canvas.js"
SMART = SMART_PATH.read_text(encoding="utf-8")
SMART_CSS = (ROOT / "static/css/smart-canvas.css").read_text(encoding="utf-8")
CLASSIC = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")


def function_source(name):
    match = re.search(rf"function\s+{re.escape(name)}\s*\(", SMART)
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
    if params_end < 0:
        raise AssertionError(f"unterminated params: {name}")
    brace = SMART.find("{", params_end)
    depth = 0
    for index in range(brace, len(SMART)):
        char = SMART[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return SMART[match.start():index + 1]
    raise AssertionError(f"unterminated function: {name}")


class SmartMultiSelectionQuickConnectContractTests(unittest.TestCase):
    def test_01_bounds_are_transient_union_and_only_exist_for_multi_selection(self):
        bounds = function_source("aggregateSelectionBounds")
        overlay = function_source("syncAggregateSelectionOverlay")
        self.assertIn("if(selected.length < 2) return null", bounds)
        self.assertIn("selected.map(nodeRect)", bounds)
        self.assertIn("Math.min(...rects.map(rect => rect.x))", bounds)
        self.assertIn("Math.max(...rects.map(rect => rect.x + rect.width))", bounds)
        self.assertIn("overlay?.remove()", overlay)
        self.assertIn("multi-selection-bounds", overlay)

    def test_02_pointerdown_snapshots_ids_and_uses_one_aggregate_start(self):
        binder = function_source("bindAggregateSelectionHandle")
        self.assertIn("handle.addEventListener('pointerdown'", binder)
        self.assertIn("sourceIds:bounds.sourceIds.slice()", binder)
        self.assertIn("startWorld:{...bounds.anchor}", binder)
        self.assertIn("currentWorld:{...bounds.anchor}", binder)
        self.assertIn("capturePendingUndo()", binder)
        self.assertIn("installSmartConnectionPointerCapture()", binder)

    def test_03_temporary_line_starts_at_aggregate_bounds_not_each_node(self):
        visual = function_source("updatePortDragVisual")
        menu_line = function_source("quickConnectTemporaryConnectionSvg")
        self.assertIn("portDragState.startWorld?.x", visual)
        self.assertIn("portDragState.startWorld?.y", visual)
        self.assertIn("pending.drag.startWorld", menu_line)
        self.assertNotIn("sourceIds.map", visual)

    def test_04_quick_connect_registry_is_reused_and_filtered_for_all_sources(self):
        accepts = function_source("quickConnectEntryAcceptsDrag")
        opener = function_source("openQuickConnectMenu")
        self.assertIn("canConnectSmartInputNodeBatch(drag.sourceIds, previewTarget)", accepts)
        self.assertIn("button.hidden = !quickConnectEntryAcceptsDrag", opener)
        self.assertIn("QUICK_CONNECT_NODE_REGISTRY", SMART)

    def test_05_new_and_existing_targets_share_the_same_batch_edge_path(self):
        creator = function_source("createQuickConnectedNode")
        drop = function_source("handlePortDrop")
        self.assertIn("entry.create(pending.worldPoint, {select:true, skipUndo:true, deferRender:true, anchorPort})", creator)
        self.assertIn("connectSmartInputNodeBatch(aggregateSourceIds, newNode.id", creator)
        self.assertIn("connectSmartInputNodeBatch(", drop)
        self.assertIn("connectInputNode(sourceId, target.id, anchors)", function_source("connectSmartInputNodeBatch"))

    def test_06_batch_validation_is_all_or_nothing_and_checks_cycles(self):
        validator = function_source("canConnectSmartInputNodeBatch")
        batch = function_source("connectSmartInputNodeBatch")
        single = function_source("canConnectSmartInputNodes")
        self.assertIn("sources.every(source => canConnectSmartInputNodes(source, target))", validator)
        self.assertLess(batch.index("canConnectSmartInputNodeBatch"), batch.index("for(const sourceId of ids)"))
        self.assertIn("canvas.connections = connectionsBefore", batch)
        self.assertIn("Object.assign(target, targetBefore)", batch)
        self.assertIn("wouldCreateSmartCanvasReferenceCycle(from.id, to.id)", single)

    def test_07_real_batch_functions_create_normal_edges_or_zero_new_edges(self):
        names = (
            "addConnection",
            "canConnectSmartInputNodes",
            "smartInputBatchSourceNodes",
            "canConnectSmartInputNodeBatch",
            "connectSmartInputNodeBatch",
            "connectInputNode",
            "wouldCreateSmartCanvasReferenceCycle",
        )
        functions = "\n".join(function_source(name) for name in names)
        harness = f"""
let nodes = [];
let canvas = {{connections:[]}};
function isSmartGroupNode(){{ return false; }}
function imagesForNode(){{ return []; }}
function promptTextItemsForNode(){{ return []; }}
function isSmartImageNode(node){{ return node?.type === 'smart-image' || node?.type === 'smart-image-generation'; }}
function fitSmartLoopNode(){{}}
{functions}
const results = {{}};
nodes = [
  {{id:'text-a', type:'smart-text'}},
  {{id:'text-b', type:'smart-text'}},
  {{id:'image-a', type:'smart-image', images:[{{url:'/a.png'}}]}},
  {{id:'image-b', type:'smart-image-generation', images:[{{url:'/b.png'}}]}},
  {{id:'target', type:'smart-image-generation'}}
];
canvas = {{connections:[]}};
results.valid = connectSmartInputNodeBatch(['text-a','text-b','image-a','image-b'], 'target');
results.validConnections = canvas.connections;
results.validInputs = nodes.find(node => node.id === 'target').inputNodeIds;

nodes = [
  {{id:'text', type:'smart-text'}},
  {{id:'unsupported', type:'smart-audio'}},
  {{id:'loop-target', type:'smart-loop', imageInput:false, showPrompt:false}}
];
canvas = {{connections:[]}};
results.invalid = connectSmartInputNodeBatch(['text','unsupported'], 'loop-target');
results.invalidConnections = canvas.connections;

nodes = [
  {{id:'cycle-a', type:'smart-text'}},
  {{id:'cycle-b', type:'smart-image'}},
  {{id:'cycle-target', type:'smart-image-generation'}}
];
canvas = {{connections:[{{from:'cycle-target', to:'cycle-b', kind:'input'}}]}};
results.cycle = connectSmartInputNodeBatch(['cycle-a','cycle-b'], 'cycle-target');
results.cycleConnections = canvas.connections;
console.log(JSON.stringify(results));
"""
        completed = subprocess.run(
            ["node", "-e", harness],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        results = json.loads(completed.stdout)
        self.assertTrue(results["valid"])
        self.assertEqual(len(results["validConnections"]), 4)
        self.assertEqual({edge["kind"] for edge in results["validConnections"]}, {"input"})
        self.assertEqual(set(results["validInputs"]), {"text-a", "text-b", "image-a", "image-b"})
        self.assertFalse(results["invalid"])
        self.assertEqual(results["invalidConnections"], [])
        self.assertFalse(results["cycle"])
        self.assertEqual(results["cycleConnections"], [{"from": "cycle-target", "to": "cycle-b", "kind": "input"}])

    def test_08_one_pending_undo_transaction_covers_node_and_all_edges(self):
        creator = function_source("createQuickConnectedNode")
        drop = function_source("handlePortDrop")
        self.assertNotIn("pushUndo()", creator)
        self.assertEqual(creator.count("commitPendingUndo()"), 1)
        aggregate_drop = drop[drop.index("if(drag.aggregate)"):drop.index("const fromId =")]
        self.assertEqual(aggregate_drop.count("commitPendingUndo()"), 1)
        self.assertIn("discardPendingUndo()", aggregate_drop)

    def test_09_escape_pointercancel_invalid_drop_and_menu_close_discard(self):
        capture = function_source("installSmartConnectionPointerCapture")
        close = function_source("closeQuickConnectMenu")
        drop = function_source("handlePortDrop")
        self.assertIn("window.addEventListener('pointercancel', onPointerCancel, true)", capture)
        self.assertIn("event.key !== 'Escape'", capture)
        self.assertIn("discardPendingUndo()", close)
        self.assertIn("if(hit?.closest?.('.image-node'))", drop)

    def test_10_css_uses_light_bounds_and_one_interactive_handle(self):
        self.assertIn(".multi-selection-bounds {", SMART_CSS)
        self.assertIn("pointer-events:none", SMART_CSS[SMART_CSS.index(".multi-selection-bounds {"):])
        self.assertIn(".aggregate-output-handle", SMART_CSS)
        self.assertIn("pointer-events:auto", SMART_CSS[SMART_CSS.index(".multi-selection-bounds .aggregate-output-handle"):])
        self.assertIn("right:-9px", SMART_CSS)

    def test_11_no_parallel_graph_schema_or_normal_canvas_implementation(self):
        for forbidden in ("aggregateEdge", "batchEdge", "origin:'multi'", "type:'smart-group'"):
            batch_area = SMART[SMART.index("function aggregateSelectionBounds"):SMART.index("function smartConnectionSelectionKey")]
            self.assertNotIn(forbidden, batch_area)
        self.assertNotIn("multi-selection-bounds", CLASSIC)
        self.assertNotIn("connectSmartInputNodeBatch", CLASSIC)


if __name__ == "__main__":
    unittest.main()
