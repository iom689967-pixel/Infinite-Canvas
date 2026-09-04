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


class SmartSelectionIncrementalUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names = (
            "normalizeSmartSelectionUiState",
            "smartSelectionSetsEqual",
            "smartSelectionUiStatesEqual",
            "syncSmartConnectionSelectionUi",
            "rememberSmartSelectionUiState",
            "updateSmartSelectionUI",
        )
        functions = "\n".join(function_source(name) for name in names)
        cls.runtime = run_node(
            f"""
function classList(initial=[]){{
  const values = new Set(initial);
  return {{
    values,
    toggle(name, force){{ const enabled = force === undefined ? !values.has(name) : Boolean(force); if(enabled) values.add(name); else values.delete(name); return enabled; }},
    contains(name){{ return values.has(name); }}
  }};
}}
function makeNode(id, src){{
  const thumb = {{
    dataset:{{imageIndex:'0'}},
    classList:classList(),
    src
  }};
  return {{
    dataset:{{id}},
    classList:classList(),
    thumb,
    querySelectorAll(selector){{ return selector === '.thumb-item,.image-wrap' ? [thumb] : []; }}
  }};
}}
const nodeElements = new Map([
  ['a', makeNode('a', '/a.png')],
  ['b', makeNode('b', '/b.png')],
  ['c', makeNode('c', '/c.png')]
]);
const edgePath = {{dataset:{{connIndex:'0'}}, classList:classList()}};
const world = {{
  classList:classList(),
  querySelectorAll(selector){{ return selector === 'svg.connection-layer path.conn-line[data-conn-index]' ? [edgePath] : []; }}
}};
const smartArrangeBtn = {{classList:classList()}};
const canvas = {{connections:[{{from:'a', to:'b', kind:'flow'}}]}};
let smartSelectionUiNodeIds = new Set();
let smartSelectionUiImage = {{nodeId:'', index:-1}};
let smartSelectionUiConnectionKeys = new Set();
let touchedCalls = [];
let composerCount = 0;
let overlayCount = 0;
let runButtonCount = 0;
let renderCount = 0;
let edgeRenderCount = 0;
let minimapCount = 0;
let globalLucideCount = 0;
let measureCount = 0;
let saveCount = 0;
function smartConnectionSelectionKey(connection){{ return JSON.stringify([connection.from, connection.to, connection.kind || 'flow']); }}
function smartNodeElementsByIds(ids){{ const list = [...ids]; touchedCalls.push(list); return list.map(id => nodeElements.get(id)).filter(Boolean); }}
function syncRunButtonState(){{ runButtonCount += 1; }}
function syncAggregateSelectionOverlay(){{ overlayCount += 1; }}
function updateComposer(){{ composerCount += 1; }}
function render(){{ renderCount += 1; }}
function renderConnections(){{ edgeRenderCount += 1; }}
function renderMinimap(){{ minimapCount += 1; }}
function measureSmartNodeImages(){{ measureCount += 1; }}
function scheduleSave(){{ saveCount += 1; }}
const lucide = {{createIcons(){{ globalLucideCount += 1; }}}};
{functions}
function state(nodeIds=[], image={{nodeId:'', index:-1}}, connectionKeys=[]){{
  return {{nodeIds, image, connectionKeys:new Set(connectionKeys)}};
}}
const snapshots = {{}};
const empty = state();
const a = state(['a']);
snapshots.firstPatched = updateSmartSelectionUI(empty, a);
snapshots.first = {{
  aSelected:nodeElements.get('a').classList.contains('selected'),
  touched:touchedCalls.at(-1),
  composerCount,
  overlayCount
}};
const beforeSecond = {{touched:touchedCalls.length, composerCount, overlayCount}};
snapshots.secondPatched = updateSmartSelectionUI(a, a);
snapshots.second = {{
  touched:touchedCalls.length - beforeSecond.touched,
  composer:composerCount - beforeSecond.composerCount,
  overlay:overlayCount - beforeSecond.overlayCount
}};
const b = state(['b']);
updateSmartSelectionUI(a, b);
snapshots.switch = {{
  aSelected:nodeElements.get('a').classList.contains('selected'),
  bSelected:nodeElements.get('b').classList.contains('selected'),
  touched:touchedCalls.at(-1)
}};
updateSmartSelectionUI(b, empty);
snapshots.clear = {{
  bSelected:nodeElements.get('b').classList.contains('selected'),
  touched:touchedCalls.at(-1),
  multi:world.classList.contains('smart-multi-selected')
}};
const ab = state(['a','b']);
updateSmartSelectionUI(empty, ab);
const abc = state(['a','b','c']);
updateSmartSelectionUI(ab, abc);
snapshots.multi = {{
  touched:touchedCalls.at(-1),
  selected:[...nodeElements.values()].filter(el => el.classList.contains('selected')).map(el => el.dataset.id),
  multi:world.classList.contains('smart-multi-selected'),
  arrange:smartArrangeBtn.classList.contains('visible')
}};
const beforeImageSrc = nodeElements.get('a').thumb.src;
const imageA = state(['a'], {{nodeId:'a', index:0}});
updateSmartSelectionUI(abc, imageA);
snapshots.image = {{
  selected:nodeElements.get('a').thumb.classList.contains('image-selected'),
  srcUnchanged:nodeElements.get('a').thumb.src === beforeImageSrc
}};
const edgeKey = smartConnectionSelectionKey(canvas.connections[0]);
const withEdge = state(['a'], {{nodeId:'', index:-1}}, [edgeKey]);
updateSmartSelectionUI(imageA, withEdge);
snapshots.edgeSelected = edgePath.classList.contains('conn-selected');
updateSmartSelectionUI(withEdge, state(['a']));
snapshots.edgeCleared = !edgePath.classList.contains('conn-selected');
snapshots.global = {{renderCount, edgeRenderCount, minimapCount, globalLucideCount, measureCount, saveCount}};
snapshots.overlayCount = overlayCount;
snapshots.composerCount = composerCount;
console.log(JSON.stringify(snapshots));
"""
        )

    def test_01_first_select_uses_one_local_patch_and_zero_full_render(self):
        self.assertTrue(self.runtime["firstPatched"])
        self.assertTrue(self.runtime["first"]["aSelected"])
        self.assertEqual(self.runtime["first"]["touched"], ["a"])
        self.assertEqual(self.runtime["global"]["renderCount"], 0)

    def test_02_second_select_same_node_is_a_noop(self):
        self.assertFalse(self.runtime["secondPatched"])
        self.assertEqual(self.runtime["second"], {"touched": 0, "composer": 0, "overlay": 0})

    def test_03_switch_selection_only_touches_old_and_new_nodes(self):
        state = self.runtime["switch"]
        self.assertFalse(state["aSelected"])
        self.assertTrue(state["bSelected"])
        self.assertEqual(set(state["touched"]), {"a", "b"})

    def test_04_clear_selection_is_incremental(self):
        state = self.runtime["clear"]
        self.assertFalse(state["bSelected"])
        self.assertEqual(state["touched"], ["b"])
        self.assertFalse(state["multi"])

    def test_05_multi_select_only_patches_changed_nodes(self):
        state = self.runtime["multi"]
        self.assertEqual(state["touched"], ["c"])
        self.assertEqual(set(state["selected"]), {"a", "b", "c"})
        self.assertTrue(state["multi"])
        self.assertTrue(state["arrange"])
        binder = function_source("bindNodeEvents")
        self.assertIn("e.shiftKey || e.metaKey || e.ctrlKey", binder)

    def test_06_marquee_uses_incremental_selection_and_keeps_bounds(self):
        finish = function_source("finishSelection")
        self.assertIn("updateSmartSelectionUI(previousSelection)", finish)
        self.assertNotIn("render()", finish)
        self.assertIn("syncAggregateSelectionOverlay()", function_source("updateSmartSelectionUI"))

    def test_07_selection_never_redraws_edges(self):
        self.assertEqual(self.runtime["global"]["edgeRenderCount"], 0)
        update = function_source("updateSmartSelectionUI")
        self.assertNotIn("renderConnections", update)
        self.assertNotIn("scheduleConnectionLayerRefresh", update)

    def test_08_selection_never_refreshes_minimap(self):
        self.assertEqual(self.runtime["global"]["minimapCount"], 0)
        self.assertNotIn("renderMinimap", function_source("updateSmartSelectionUI"))

    def test_09_selection_and_composer_use_no_global_lucide_scan(self):
        self.assertEqual(self.runtime["global"]["globalLucideCount"], 0)
        self.assertNotIn("lucide.createIcons", function_source("updateSmartSelectionUI"))
        self.assertIn("refreshLucideIconsWithin(dynamicParams)", function_source("renderDynamicParams"))
        self.assertIn("refreshLucideIconsWithin(inputThumbsRow)", function_source("renderInputThumbsRow"))
        self.assertIn("refreshLucideIconsWithin(cascadeRunBtn)", function_source("syncCascadeRunButton"))

    def test_10_selection_does_not_save_canvas(self):
        self.assertEqual(self.runtime["global"]["saveCount"], 0)
        update = function_source("updateSmartSelectionUI")
        self.assertNotIn("scheduleSave", update)
        binder = function_source("bindNodeEvents")
        self.assertIn("hideRunTimerForNode(node, {save:false})", binder)

    def test_11_generation_selection_updates_composer_locally(self):
        self.assertGreater(self.runtime["composerCount"], 0)
        update = function_source("updateSmartSelectionUI")
        self.assertIn("updateComposer()", update)
        self.assertNotIn("render()", update)

    def test_12_image_selection_preserves_canvas_image_source(self):
        self.assertEqual(self.runtime["image"], {"selected": True, "srcUnchanged": True})
        update = function_source("updateSmartSelectionUI")
        self.assertNotIn("syncSmartSelectedImageResolution", update)
        self.assertNotIn("measureSmartNodeImages", update)

    def test_13_connection_selection_and_aggregate_ui_patch_in_place(self):
        self.assertTrue(self.runtime["edgeSelected"])
        self.assertTrue(self.runtime["edgeCleared"])
        self.assertGreater(self.runtime["overlayCount"], 0)
        connection = function_source("syncSmartConnectionSelectionUi")
        self.assertIn("path.classList.toggle('conn-selected', selected)", connection)
        self.assertNotIn("renderConnections", connection)

    def test_14_no_movement_mouseup_skips_save_and_edge_raf(self):
        mouseup_start = SMART.index("window.onmouseup = e =>")
        mouseup_end = SMART.index("\n};", mouseup_start) + 3
        mouseup = SMART[mouseup_start:mouseup_end]
        tail = mouseup[mouseup.rindex("dragState = null;"):]
        self.assertIn("if(stateChanged)", tail)
        self.assertIn("scheduleSave()", tail)
        self.assertIn("scheduleConnectionLayerRefresh()", tail)
        self.assertNotIn("render()", tail)
        binder = function_source("bindNodeEvents")
        click = binder[binder.index("el.onclick = e =>"):binder.index("if(nodeForControls?.type === 'smart-prompt'", binder.index("el.onclick = e =>"))]
        self.assertIn("if(!append && alreadySelected) return", click)
        self.assertNotIn("render()", click)


if __name__ == "__main__":
    unittest.main()
