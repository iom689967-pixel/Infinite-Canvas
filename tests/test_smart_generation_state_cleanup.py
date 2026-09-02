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


class SmartGenerationStateCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        functions = "\n".join(function_source(name) for name in (
            "nodeGenerationHistoryItems",
            "nodeGenerationAttempt",
            "applyNodeGenerationAttempt",
            "completeNodeGenerationAttempt",
            "clearNodeGenerationTerminalState",
            "finishNodeGenerationAttempt",
            "nodeGenerationRunningOverlayHtml",
        ))
        cls.terminal = run_node(f"""
let tick = 1000;
let selectedImage = {{nodeId:'', index:-1}};
const activeSmartGenerationRuns = new Map();
const cancellingSmartGenerationIds = new Set();
const smartNodeRunTokens = new Map();
function nowMs(){{ return ++tick; }}
function clearSmartNodeBusyState(node){{
  smartNodeRunTokens.delete(node.id);
  node.running = false;
  node.pending = 0;
  node.queued = false;
  delete node.jimengPending;
  delete node.pendingTasks;
  return node;
}}
function markSmartNodeComplete(node, meta=null){{
  clearSmartNodeBusyState(node);
  node.runFinishedAt = Number(node.runFinishedAt || 0) || nowMs();
  if(!node.runStartedAt) node.runStartedAt = meta?.createdAt || node.runFinishedAt;
}}
function normalizeNodeGenerationOutputs(outputs){{ return (outputs || []).map(item => typeof item === 'string' ? {{url:item}} : {{...item}}); }}
function cleanHistoryImages(items){{ const seen = new Set(); return (items || []).filter(item => item?.url && !seen.has(item.url) && seen.add(item.url)); }}
function mediaKindForUrls(){{ return 'image'; }}
function nodeGenerationLayoutSnapshot(node){{ return {{width:node.w, height:node.h, scale:node.scale}}; }}
function cascadeOutputTitle(kind, count){{ return count > 1 ? 'Group' : 'Image'; }}
function nodeGenerationMetaFromAttempt(attempt){{ return {{createdAt:attempt.createdAt}}; }}
function attachRunMeta(){{}}
function smartPendingStatusLabel(){{ return ''; }}
function escapeHtml(value){{ return String(value || ''); }}
function escapeAttr(value){{ return String(value || ''); }}
{functions}
function busyNode(id, status='running'){{
  const attempt = {{id:`${{id}}-attempt`, status, createdAt:1, completedAt:0, updatedAt:1, outputKind:'image', outputs:[]}};
  return {{id, pending:1, running:true, queued:true, pendingTasks:[{{taskId:`${{id}}-task`, generationId:attempt.id}}], activeGenerationId:attempt.id, generationHistory:[attempt], images:[{{url:'/old.png'}}], currentGenerationId:'old'}};
}}
const success = busyNode('success');
activeSmartGenerationRuns.set(success.activeGenerationId, {{}});
completeNodeGenerationAttempt(success, [{{url:'/new.png'}}], {{generationId:success.activeGenerationId, kind:'image'}});
const failed = busyNode('failed');
activeSmartGenerationRuns.set(failed.activeGenerationId, {{}});
finishNodeGenerationAttempt(failed, 'failed', 'boom', failed.activeGenerationId);
const cancelled = busyNode('cancelled');
activeSmartGenerationRuns.set(cancelled.activeGenerationId, {{}});
finishNodeGenerationAttempt(cancelled, 'cancelled', 'stop', cancelled.activeGenerationId);
function state(node){{ return {{
  status:node.generationHistory.at(-1).status,
  active:Object.prototype.hasOwnProperty.call(node, 'activeGenerationId') ? node.activeGenerationId : null,
  pending:node.pending,
  running:node.running,
  queued:node.queued,
  tasks:Object.prototype.hasOwnProperty.call(node, 'pendingTasks'),
  image:node.images[0].url,
  current:node.currentGenerationId,
  overlay:nodeGenerationRunningOverlayHtml(node),
  activeRun:activeSmartGenerationRuns.has(node.generationHistory.at(-1).id)
}}; }}
console.log(JSON.stringify({{success:state(success), failed:state(failed), cancelled:state(cancelled)}}));
""")
        reconcile_functions = "\n".join(function_source(name) for name in (
            "nodeGenerationHistoryItems",
            "nodeGenerationAttempt",
            "smartPendingTasks",
            "smartTaskHasTerminalFailure",
            "clearNodeGenerationTerminalState",
            "finishNodeGenerationAttempt",
            "reconcileNodeGenerationHistory",
        ))
        cls.reloaded = run_node(f"""
let tick = 2000;
const activeSmartGenerationRuns = new Map();
const smartNodeRunTokens = new Map();
function nowMs(){{ return ++tick; }}
function clearSmartNodeBusyState(node){{ node.pending=0; node.running=false; node.queued=false; delete node.pendingTasks; return node; }}
function normalizeNodeGenerationOutputs(outputs){{ return (outputs || []).map(item => ({{...item}})); }}
function smartNodeHasDisplayResult(node){{ return Boolean((node.images || []).some(image => image?.url)); }}
{reconcile_functions}
const successAttempt = {{id:'success-attempt', status:'success', createdAt:1, completedAt:2, outputs:[{{url:'/adopted.png'}}], taskIds:['success-task'], references:[]}};
const success = {{id:'success', images:[{{url:'/adopted.png'}}], generationHistory:[successAttempt], currentGenerationId:successAttempt.id, activeGenerationId:successAttempt.id, pending:1, running:false, queued:false, pendingTasks:[{{taskId:'success-task', generationId:successAttempt.id, status:'queued'}}]}};
const failedAttempt = {{id:'failed-attempt', status:'running', createdAt:3, completedAt:0, outputs:[], taskIds:['failed-task'], references:[]}};
const failed = {{id:'failed', images:[{{url:'/old.png'}}], generationHistory:[failedAttempt], activeGenerationId:failedAttempt.id, pending:1, running:false, queued:false, pendingTasks:[{{taskId:'failed-task', generationId:failedAttempt.id, status:'fail', error:'terminal upstream failure'}}]}};
const resumedAttempt = {{id:'resumed-attempt', status:'running', createdAt:4, completedAt:0, outputs:[], taskIds:['resumed-task'], references:[]}};
const resumed = {{id:'resumed', images:[{{url:'/old.png'}}], generationHistory:[resumedAttempt], pending:1, running:false, queued:false, pendingTasks:[{{taskId:'resumed-task', generationId:resumedAttempt.id, status:'generating'}}]}};
const successChanged = reconcileNodeGenerationHistory(success);
const failedChanged = reconcileNodeGenerationHistory(failed);
const resumedChanged = reconcileNodeGenerationHistory(resumed);
function state(node, changed){{ return {{changed, active:Object.prototype.hasOwnProperty.call(node, 'activeGenerationId') ? node.activeGenerationId : null, pending:node.pending, tasks:Object.prototype.hasOwnProperty.call(node, 'pendingTasks'), status:node.generationHistory.at(-1).status, image:node.images[0].url}}; }}
console.log(JSON.stringify({{success:state(success, successChanged), failed:state(failed, failedChanged), resumed:state(resumed, resumedChanged)}}));
""")

    def test_01_success_removes_overlay(self):
        self.assertEqual(self.terminal["success"]["overlay"], "")

    def test_02_failed_removes_overlay(self):
        self.assertEqual(self.terminal["failed"]["overlay"], "")

    def test_03_cancelled_removes_overlay(self):
        self.assertEqual(self.terminal["cancelled"]["overlay"], "")

    def test_04_success_keeps_adopted_result(self):
        self.assertEqual(self.terminal["success"]["image"], "/new.png")
        self.assertEqual(self.terminal["success"]["current"], "success-attempt")

    def test_05_terminal_states_clear_active_generation(self):
        for state in self.terminal.values():
            self.assertIsNone(state["active"])
            self.assertFalse(state["activeRun"])

    def test_06_terminal_states_clear_pending_and_local_task_refs(self):
        for state in self.terminal.values():
            self.assertEqual(state["pending"], 0)
            self.assertFalse(state["running"])
            self.assertFalse(state["queued"])
            self.assertFalse(state["tasks"])

    def test_07_late_callback_cannot_restore_terminal_attempt(self):
        finalizer = function_source("finalizeSmartPendingTask")
        self.assertIn("generationAttempt.status !== 'running'", finalizer)
        self.assertLess(
            finalizer.index("generationAttempt.status !== 'running'"),
            finalizer.index("node.pendingTasks ="),
        )

    def test_08_cancel_a_then_run_b_rejects_a_callback(self):
        finalizer = function_source("finalizeSmartPendingTask")
        complete = function_source("completeNodeGenerationAttempt")
        self.assertIn("node.activeGenerationId !== generationAttempt.id", finalizer)
        self.assertIn("node.activeGenerationId !== attempt.id", complete)

    def test_09_reload_reconciles_success_and_terminal_failure_to_idle(self):
        merge = function_source("applyMergedServerCanvas")
        self.assertIn("nodes.map(reconcileNodeGenerationHistory)", merge)
        for state in (self.reloaded["success"], self.reloaded["failed"]):
            self.assertTrue(state["changed"])
            self.assertIsNone(state["active"])
            self.assertEqual(state["pending"], 0)
            self.assertFalse(state["tasks"])
        self.assertEqual(self.reloaded["success"]["status"], "success")
        self.assertEqual(self.reloaded["success"]["image"], "/adopted.png")
        self.assertEqual(self.reloaded["failed"]["status"], "failed")
        self.assertEqual(self.reloaded["failed"]["image"], "/old.png")
        self.assertTrue(self.reloaded["resumed"]["changed"])
        self.assertEqual(self.reloaded["resumed"]["active"], "resumed-attempt")
        self.assertEqual(self.reloaded["resumed"]["pending"], 1)
        self.assertTrue(self.reloaded["resumed"]["tasks"])
        self.assertEqual(self.reloaded["resumed"]["status"], "running")

    def test_10_only_nonterminal_upstream_failures_keep_manual_recovery(self):
        poll = function_source("pollSmartCanvasTask")
        helper = function_source("smartTaskHasTerminalFailure")
        self.assertIn("fail", helper)
        self.assertIn("!smartTaskHasTerminalFailure({status:task.upstream_status})", poll)
        self.assertLess(
            poll.index("!smartTaskHasTerminalFailure({status:task.upstream_status})"),
            poll.index("throw new ImageTaskRecoverSignal"),
        )


if __name__ == "__main__":
    unittest.main()
