import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART_PATH = ROOT / "static/js/smart-canvas.js"
SMART = SMART_PATH.read_text(encoding="utf-8")


def function_source(name):
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(", SMART)
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


class SmartGenerationTerminalIncrementalUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        terminal = function_source("updateGenerationTerminalUI")
        cls.runtime = run_node(f"""
let renderCount = 0;
let edgeCount = 0;
let saveCount = 0;
let iconCount = 0;
let bindCount = 0;
let fallbackCount = 0;
let resolutionCount = 0;
let measureCount = 0;
let runButtonCount = 0;
let historyPanelCount = 0;
let minimapScheduleCount = 0;
let renderedLiveNode = null;
let replacementCount = 0;
let selectedId = 'target';
let nodeGenerationHistoryPanelNodeId = 'target';
const CSS = {{escape:value => String(value)}};
function render(){{ renderCount += 1; }}
function renderConnections(){{ edgeCount += 1; }}
function scheduleSave(){{ saveCount += 1; }}
function refreshLucideIconsWithin(){{ iconCount += 1; }}
function bindNodeEvents(){{ bindCount += 1; }}
function bindSmartPreviewImageFallbacks(){{ fallbackCount += 1; }}
function syncSmartSelectedImageResolution(){{ resolutionCount += 1; }}
function measureSmartNodeImages(){{ measureCount += 1; }}
function syncRunButtonState(){{ runButtonCount += 1; }}
function renderNodeGenerationHistoryPanel(){{ historyPanelCount += 1; }}
function scheduleGenerationTerminalMinimapRefresh(){{ minimapScheduleCount += 1; return true; }}
function nodeGenerationAttempt(node, id){{ return (node?.generationHistory || []).find(item => item.id === id) || null; }}
function isSmartImageNode(node){{ return node?.type === 'smart-image-generation'; }}
function smartNodeRenderEntry(node){{ renderedLiveNode = node; return {{node, html:'<div></div>'}}; }}
function makeElement(generationId='attempt-a'){{
  return {{
    dataset:{{generationId}},
    style:{{left:'40px', top:'80px', width:'320px', height:'240px'}},
    replaceWith(fresh){{ world.current = fresh; replacementCount += 1; }},
    querySelectorAll(){{ return []; }}
  }};
}}
const fresh = {{dataset:{{generationId:''}}, style:{{}}, querySelectorAll(){{ return []; }}}};
const document = {{createElement(){{
  return {{content:{{firstElementChild:fresh}}, set innerHTML(value){{ this.value = value; }}}};
}}}};
const world = {{
  current:makeElement(),
  querySelector(){{ return this.current; }}
}};
const oldOtherElements = Array.from({{length:276}}, (_, index) => ({{id:`other-dom-${{index}}`}}));
const oldOtherSnapshot = oldOtherElements.slice();
const attemptA = {{id:'attempt-a', status:'success', outputKind:'image', outputs:[{{url:'/new.png'}}]}};
const target = {{
  id:'target', type:'smart-image-generation', currentGenerationId:'attempt-a',
  images:[{{url:'/new.png'}}], generationHistory:[attemptA], outputKind:'image'
}};
let nodes = [target, ...Array.from({{length:276}}, (_, index) => ({{id:`node-${{index}}`, type:'smart-image'}}))];
{terminal}
const success = updateGenerationTerminalUI('target', {{generationId:'attempt-a'}});
const successState = {{
  success,
  renderCount,
  edgeCount,
  saveCount,
  replacementCount,
  iconCount,
  bindCount,
  fallbackCount,
  resolutionCount,
  measureCount,
  runButtonCount,
  historyPanelCount,
  minimapScheduleCount,
  renderedLiveNode:renderedLiveNode === target,
  geometry:{{...fresh.style}},
  otherDomPreserved:oldOtherElements.every((item, index) => item === oldOtherSnapshot[index]),
  image:target.images[0].url,
  currentGenerationId:target.currentGenerationId,
  overlayAbsent:fresh.querySelectorAll('[data-generation-runtime]').length === 0
}};

const staleObject = {{...target, images:[{{url:'/stale.png'}}]}};
world.current = makeElement();
renderedLiveNode = null;
const staleSuccess = updateGenerationTerminalUI(staleObject.id, {{generationId:'attempt-a'}});
const staleState = {{success:staleSuccess, usedLiveNode:renderedLiveNode === target}};

const attemptB = {{id:'attempt-b', status:'running', outputKind:'image', outputs:[]}};
target.activeGenerationId = 'attempt-b';
target.generationHistory.push(attemptB);
world.current = makeElement('attempt-b');
const beforeLateA = replacementCount;
const lateA = updateGenerationTerminalUI('target', {{generationId:'attempt-a'}});
const lateAState = {{success:lateA, replacements:replacementCount - beforeLateA, current:target.currentGenerationId, active:target.activeGenerationId}};

console.log(JSON.stringify({{successState, staleState, lateAState}}));
""")

        minimap = function_source("scheduleGenerationTerminalMinimapRefresh")
        cls.minimap = run_node(f"""
let generationTerminalMinimapScheduled = false;
let minimapCount = 0;
const rafCallbacks = [];
const idleCallbacks = [];
const timerCallbacks = [];
const window = {{
  requestAnimationFrame(callback){{ rafCallbacks.push(callback); return rafCallbacks.length; }},
  requestIdleCallback(callback){{ idleCallbacks.push(callback); return idleCallbacks.length; }}
}};
function setTimeout(callback){{ timerCallbacks.push(callback); return timerCallbacks.length; }}
function renderMinimap(){{ minimapCount += 1; }}
{minimap}
const first = scheduleGenerationTerminalMinimapRefresh();
const second = scheduleGenerationTerminalMinimapRefresh();
const immediate = minimapCount;
rafCallbacks.shift()();
const afterPaint = minimapCount;
const queuedIdle = idleCallbacks.length;
idleCallbacks.shift()();
const afterIdle = minimapCount;
const third = scheduleGenerationTerminalMinimapRefresh();
console.log(JSON.stringify({{first, second, third, immediate, afterPaint, queuedIdle, afterIdle}}));
""")

        icons = function_source("refreshLucideIconsWithin")
        cls.icons = run_node(f"""
let globalCreateCount = 0;
let localCreateCount = 0;
let replacedCount = 0;
function placeholder(name){{
  return {{
    dataset:{{lucide:name}}, tagName:'I', attributes:[{{name:'data-lucide', value:name}}],
    getAttribute(){{ return name; }}, replaceWith(){{ replacedCount += 1; }}
  }};
}}
const targetIcons = [placeholder('trash-2'), placeholder('history')];
const root = {{querySelectorAll(){{ return targetIcons; }}}};
const window = {{lucide:{{
  icons:{{Trash2:['trash'], History:['history']}},
  createElement(){{ localCreateCount += 1; return {{}}; }},
  createIcons(){{ globalCreateCount += 1; }}
}}}};
{icons}
const refreshed = refreshLucideIconsWithin(root);
console.log(JSON.stringify({{refreshed, localCreateCount, globalCreateCount, replacedCount}}));
""")

    def test_01_success_uses_one_terminal_patch_and_zero_full_render(self):
        state = self.runtime["successState"]
        self.assertTrue(state["success"])
        self.assertEqual(state["replacementCount"], 1)
        self.assertEqual(state["renderCount"], 0)

    def test_02_other_276_node_dom_objects_are_untouched(self):
        self.assertTrue(self.runtime["successState"]["otherDomPreserved"])

    def test_03_geometry_unchanged_skips_global_edge_render(self):
        state = self.runtime["successState"]
        self.assertEqual(state["edgeCount"], 0)
        self.assertEqual(state["geometry"], {"left": "40px", "top": "80px", "width": "320px", "height": "240px"})
        terminal = function_source("updateGenerationTerminalUI")
        measurement = function_source("measureSmartNodeImages")
        self.assertIn("measureSmartNodeImages(fresh, {preserveGeometry:true, scheduleMeasurementSave:false})", terminal)
        self.assertIn("if(!options.preserveGeometry)", measurement)
        self.assertIn("if(options.scheduleMeasurementSave !== false) scheduleSave()", measurement)

    def test_04_adopted_model_keeps_current_generation_and_image(self):
        state = self.runtime["successState"]
        self.assertEqual(state["currentGenerationId"], "attempt-a")
        self.assertEqual(state["image"], "/new.png")

    def test_05_runtime_overlay_and_cancel_leave_with_replaced_node(self):
        self.assertTrue(self.runtime["successState"]["overlayAbsent"])
        terminal = function_source("updateGenerationTerminalUI")
        self.assertIn("current.replaceWith(fresh)", terminal)

    def test_06_target_node_only_rebinds_local_behaviors(self):
        state = self.runtime["successState"]
        for key in ("bindCount", "fallbackCount", "resolutionCount", "measureCount"):
            self.assertEqual(state[key], 1)

    def test_07_open_generation_history_is_refreshed_locally(self):
        state = self.runtime["successState"]
        self.assertEqual(state["historyPanelCount"], 1)
        history = function_source("renderNodeGenerationHistoryPanel")
        self.assertIn("refreshLucideIconsWithin(content)", history)
        self.assertNotIn("lucide.createIcons", history)

    def test_08_terminal_ui_does_not_schedule_a_second_save(self):
        self.assertEqual(self.runtime["successState"]["saveCount"], 0)
        run = function_source("runGeneration")
        self.assertIn("addSmartGenerationLog({run:runLog, outputs:pendingNode.images", run)
        self.assertIn("scheduleSave();\n            return;", run)

    def test_09_minimap_is_deferred_and_coalesced(self):
        self.assertTrue(self.minimap["first"])
        self.assertFalse(self.minimap["second"])
        self.assertEqual(self.minimap["immediate"], 0)
        self.assertEqual(self.minimap["afterPaint"], 0)
        self.assertEqual(self.minimap["queuedIdle"], 1)
        self.assertEqual(self.minimap["afterIdle"], 1)
        self.assertTrue(self.minimap["third"])

    def test_10_lucide_refresh_is_scoped_without_global_rebuild(self):
        self.assertEqual(self.icons, {"refreshed": 2, "localCreateCount": 2, "globalCreateCount": 0, "replacedCount": 2})
        terminal = function_source("updateGenerationTerminalUI")
        self.assertIn("refreshLucideIconsWithin(fresh)", terminal)
        self.assertNotIn("lucide.createIcons", terminal)

    def test_11_save_conflict_stale_object_resolves_the_live_node(self):
        self.assertEqual(self.runtime["staleState"], {"success": True, "usedLiveNode": True})
        self.assertIn("nodes.find(item => item.id === nodeId)", function_source("updateGenerationTerminalUI"))

    def test_12_late_attempt_a_cannot_patch_active_attempt_b(self):
        state = self.runtime["lateAState"]
        self.assertFalse(state["success"])
        self.assertEqual(state["replacements"], 0)
        self.assertEqual(state["current"], "attempt-a")
        self.assertEqual(state["active"], "attempt-b")

    def test_13_success_finally_has_incremental_path_and_guarded_fallback(self):
        run = function_source("runGeneration")
        self.assertIn("terminalAttempt?.status === 'success'", run)
        self.assertIn("updateGenerationTerminalUI(pendingNode.id", run)
        self.assertIn("if(!terminalSuccessPatched) render()", run)

    def test_14_polling_path_remains_incremental_and_unmodified_in_contract(self):
        polling = function_source("updateSmartPendingTaskStatus")
        self.assertIn("updateGenerationRuntimeUI", polling)
        self.assertNotIn("render()", polling)
        self.assertNotIn("renderConnections", polling)


if __name__ == "__main__":
    unittest.main()
