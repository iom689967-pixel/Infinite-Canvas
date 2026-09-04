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


class SmartGenerationPollingIncrementalUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        functions = "\n".join(function_source(name) for name in (
            "smartPendingTasks",
            "smartPendingRuntimeStatus",
            "smartPendingStatusLabel",
            "nodeGenerationHistoryItems",
            "nodeGenerationAttempt",
            "nodeGenerationAttemptForTask",
            "smartGenerationRuntimeState",
            "updateGenerationRuntimeUI",
            "updateSmartPendingTaskStatus",
        ))
        cls.runtime = run_node(f"""
let nodes = [];
let renderCount = 0;
let edgeRenderCount = 0;
let saveCount = 0;
let uiUpdateCount = 0;
let tick = 1000;
const cancellingSmartGenerationIds = new Set();
function render(){{ renderCount += 1; }}
function renderConnections(){{ edgeRenderCount += 1; }}
function scheduleSave(){{ saveCount += 1; }}
function nowMs(){{ return ++tick; }}
const CSS = {{escape:value => String(value)}};
function classList(initial=[]){{
  const values = new Set(initial);
  return {{
    toggle(name, enabled){{ enabled ? values.add(name) : values.delete(name); }},
    contains(name){{ return values.has(name); }}
  }};
}}
const label = {{textContent:'initial'}};
const detail = {{textContent:'initial'}};
const cancel = {{textContent:'cancel', disabled:false}};
const timer = {{hidden:false, classList:classList()}};
const overlay = {{hidden:false, dataset:{{}}, querySelector(){{ return null; }}}};
const nodeEl = {{
  dataset:{{}},
  classList:classList(['node-generation-running']),
  querySelector(selector){{
    if(selector === '[data-generation-runtime]') return overlay;
    if(selector === '[data-generation-runtime-detail]') return detail;
    if(selector === '[data-generation-runtime-cancel]') return cancel;
    if(selector === '[data-run-timer]') return timer;
    return null;
  }},
  querySelectorAll(selector){{ return selector === '[data-generation-runtime-label]' ? [label] : []; }}
}};
const world = {{querySelector(){{ return nodeEl; }}}};
{functions}
const runtimeUpdate = updateGenerationRuntimeUI;
updateGenerationRuntimeUI = (...args) => {{ uiUpdateCount += 1; return runtimeUpdate(...args); }};
function makeNode(status='pending'){{
  const attempt = {{id:'attempt-a', status:'running', updatedAt:1, upstreamTaskIds:[]}};
  return {{
    id:'node-1',
    type:'smart-image-generation',
    activeGenerationId:attempt.id,
    generationHistory:[attempt],
    pending:1,
    running:false,
    runTimerHidden:false,
    pendingTasks:[{{taskId:'task-a', generationId:attempt.id, status}}],
    images:[{{url:'/adopted.png'}}]
  }};
}}
const node = makeNode();
nodes = [node];
function step(status, upstreamTaskId=''){{
  const before = {{renderCount, edgeRenderCount, saveCount, uiUpdateCount}};
  const result = updateSmartPendingTaskStatus('task-a', status, upstreamTaskId);
  return {{
    result,
    render:renderCount - before.renderCount,
    edges:edgeRenderCount - before.edgeRenderCount,
    saves:saveCount - before.saveCount,
    ui:uiUpdateCount - before.uiUpdateCount,
    status:node.pendingTasks[0].status,
    domStatus:nodeEl.dataset.generationRuntimeStatus,
    generationId:nodeEl.dataset.generationId
  }};
}}
const queued = step('queued');
const waiting = step('waiting');
const generating = step('generating');
const upstream = step('generating', 'upstream-1');
const repeated = step('generating', 'upstream-1');

const stale = makeNode('waiting');
const replacement = JSON.parse(JSON.stringify(stale));
nodes = [replacement];
const beforeStale = {{renderCount, edgeRenderCount, saveCount, uiUpdateCount}};
updateSmartPendingTaskStatus('task-a', 'generating');
const staleReplacement = {{
  oldStatus:stale.pendingTasks[0].status,
  liveStatus:replacement.pendingTasks[0].status,
  render:renderCount - beforeStale.renderCount,
  edges:edgeRenderCount - beforeStale.edgeRenderCount,
  saves:saveCount - beforeStale.saveCount,
  ui:uiUpdateCount - beforeStale.uiUpdateCount
}};

const attemptA = {{id:'attempt-a', status:'cancelled', upstreamTaskIds:[]}};
const attemptB = {{id:'attempt-b', status:'running', upstreamTaskIds:[]}};
const runB = {{
  id:'node-1',
  activeGenerationId:'attempt-b',
  generationHistory:[attemptA, attemptB],
  pending:1,
  pendingTasks:[
    {{taskId:'task-a', generationId:'attempt-a', status:'queued'}},
    {{taskId:'task-b', generationId:'attempt-b', status:'waiting'}}
  ],
  images:[{{url:'/adopted.png'}}]
}};
nodes = [runB];
nodeEl.dataset.generationId = 'attempt-b';
nodeEl.dataset.generationRuntimeStatus = 'waiting';
const beforeLateA = {{renderCount, edgeRenderCount, saveCount, uiUpdateCount}};
const lateAResult = updateSmartPendingTaskStatus('task-a', 'generating', 'late-upstream-a');
const directLatePatch = updateGenerationRuntimeUI('node-1', {{generationId:'attempt-a'}});
const lateA = {{
  result:lateAResult,
  directLatePatch,
  taskAStatus:runB.pendingTasks[0].status,
  taskBStatus:runB.pendingTasks[1].status,
  domGenerationId:nodeEl.dataset.generationId,
  domStatus:nodeEl.dataset.generationRuntimeStatus,
  render:renderCount - beforeLateA.renderCount,
  edges:edgeRenderCount - beforeLateA.edgeRenderCount,
  saves:saveCount - beforeLateA.saveCount,
  ui:uiUpdateCount - beforeLateA.uiUpdateCount
}};

nodes = [node];
node.generationHistory[0].status = 'cancelled';
delete node.activeGenerationId;
node.pending = 0;
delete node.pendingTasks;
runtimeUpdate(node.id, {{generationId:'attempt-a'}});
const cancelledDom = {{
  overlayHidden:overlay.hidden,
  runningClass:nodeEl.classList.contains('node-generation-running'),
  pendingClass:nodeEl.classList.contains('node-pending')
}};
console.log(JSON.stringify({{
  queued,
  waiting,
  generating,
  upstream,
  repeated,
  staleReplacement,
  lateA,
  cancelledDom,
  label:label.textContent
}}));
""")

    def test_01_intermediate_statuses_patch_only_the_target_dom(self):
        for key, expected_status in (
            ("queued", "queued"),
            ("waiting", "waiting"),
            ("generating", "generating"),
        ):
            state = self.runtime[key]
            self.assertTrue(state["result"]["changed"])
            self.assertEqual(state["status"], expected_status)
            self.assertEqual(state["domStatus"], expected_status)
            self.assertEqual(state["generationId"], "attempt-a")
            self.assertEqual(state["render"], 0)
            self.assertEqual(state["edges"], 0)
            self.assertEqual(state["saves"], 0)
            self.assertEqual(state["ui"], 1)

    def test_02_first_upstream_id_saves_once_without_full_render(self):
        state = self.runtime["upstream"]
        self.assertTrue(state["result"]["saveRequired"])
        self.assertEqual(state["saves"], 1)
        self.assertEqual(state["render"], 0)
        self.assertEqual(state["edges"], 0)
        self.assertEqual(state["ui"], 1)

    def test_03_duplicate_poll_is_a_noop(self):
        state = self.runtime["repeated"]
        self.assertFalse(state["result"]["changed"])
        self.assertFalse(state["result"]["saveRequired"])
        self.assertEqual(state["render"], 0)
        self.assertEqual(state["edges"], 0)
        self.assertEqual(state["saves"], 0)
        self.assertEqual(state["ui"], 0)

    def test_04_poll_resolves_the_live_node_after_object_replacement(self):
        state = self.runtime["staleReplacement"]
        self.assertEqual(state["oldStatus"], "waiting")
        self.assertEqual(state["liveStatus"], "generating")
        self.assertEqual(state["render"], 0)
        self.assertEqual(state["edges"], 0)
        self.assertEqual(state["saves"], 0)
        self.assertEqual(state["ui"], 1)

    def test_05_late_attempt_a_cannot_patch_running_attempt_b(self):
        state = self.runtime["lateA"]
        self.assertFalse(state["result"]["changed"])
        self.assertFalse(state["directLatePatch"])
        self.assertEqual(state["taskAStatus"], "queued")
        self.assertEqual(state["taskBStatus"], "waiting")
        self.assertEqual(state["domGenerationId"], "attempt-b")
        self.assertEqual(state["domStatus"], "waiting")
        self.assertEqual(state["render"], 0)
        self.assertEqual(state["edges"], 0)
        self.assertEqual(state["saves"], 0)

    def test_06_cancelled_runtime_clears_overlay_without_render(self):
        state = self.runtime["cancelledDom"]
        self.assertTrue(state["overlayHidden"])
        self.assertFalse(state["runningClass"])
        self.assertFalse(state["pendingClass"])

    def test_07_polling_contract_has_no_full_render_or_edge_render(self):
        source = function_source("updateSmartPendingTaskStatus")
        self.assertIn("updateGenerationRuntimeUI", source)
        self.assertNotIn("render()", source)
        self.assertNotIn("renderConnections", source)
        self.assertIn("if(saveRequired) scheduleSave()", source)

    def test_08_generation_lifecycle_defers_terminal_render_to_owners(self):
        resume = function_source("resumeSmartPendingNode")
        run = function_source("runGeneration")
        focus = function_source("runFocusEdit")
        cancel = function_source("cancelSmartImageGeneration")
        cooldown = function_source("coolNodeRunningState")
        self.assertIn("terminalRenderRequired", resume)
        self.assertIn("!logContext.deferTerminalRender", resume)
        self.assertIn("deferTerminalRender:Boolean(generationAttempt)", run)
        self.assertIn("deferTerminalRender:true", focus)
        self.assertNotIn("render()", cancel)
        self.assertIn("const liveNode = nodes.find", cancel)
        self.assertNotIn("render()", cooldown)
        self.assertIn("updateGenerationRuntimeUI", cooldown)


if __name__ == "__main__":
    unittest.main()
