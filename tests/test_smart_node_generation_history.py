import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART_PATH = ROOT / "static/js/smart-canvas.js"
SMART = SMART_PATH.read_text(encoding="utf-8")
SMART_CSS = (ROOT / "static/css/smart-canvas.css").read_text(encoding="utf-8")


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


class SmartNodeGenerationHistoryContractTests(unittest.TestCase):
    def test_01_rerun_uses_the_same_image_node(self):
        run = function_source("runGeneration")
        self.assertIn("const inPlaceGenerationRun = logKind === 'image'", run)
        self.assertIn("node.type === 'smart-image-generation' || nodeGenerationHistoryCapable(node)", run)
        self.assertIn("!inPlaceGenerationRun", run)
        self.assertIn("createNodeGenerationAttempt(node, meta, settings, 'image')", run)
        self.assertIn("if(shouldCreateBranchOutput) branchNode = createPendingOutputFromSource", run)

    def test_02_history_is_optional_node_data_saved_by_existing_canvas_path(self):
        storage = function_source("canvasForStorage")
        save = function_source("saveCanvas")
        normalize = function_source("normalizeLegacySmartNode")
        self.assertIn("JSON.parse(JSON.stringify(canvas || {}))", storage)
        self.assertIn("canvas.nodes = nodes", save)
        self.assertNotIn("generationHistory", normalize)
        self.assertIn("generationHistory", function_source("createNodeGenerationAttempt"))
        self.assertIn("currentGenerationId", SMART)
        self.assertIn("activeGenerationId", SMART)

    def test_03_one_attempt_keeps_one_stable_id_through_all_statuses(self):
        creator = function_source("createNodeGenerationAttempt")
        complete = function_source("completeNodeGenerationAttempt")
        finish = function_source("finishNodeGenerationAttempt")
        self.assertIn("id:uid('generation')", creator)
        self.assertIn("node.generationHistory = [...nodeGenerationHistoryItems(node), attempt]", creator)
        self.assertIn("const attempt = nodeGenerationAttempt", complete)
        self.assertIn("attempt.status = 'success'", complete)
        self.assertNotIn("push(", complete)
        self.assertIn("attempt.status = status === 'cancelled' ? 'cancelled' : 'failed'", finish)
        self.assertNotIn("push(", finish)

    def test_04_running_does_not_replace_current_and_failure_keeps_old_image(self):
        functions = "\n".join(function_source(name) for name in (
            "liveNodeGenerationState",
            "nodeGenerationHistoryItems",
            "createNodeGenerationAttempt",
            "nodeGenerationAttempt",
            "applyNodeGenerationAttempt",
            "completeNodeGenerationAttempt",
            "clearNodeGenerationTerminalState",
            "finishNodeGenerationAttempt",
        ))
        harness = f"""
let tick = 1000;
let selectedImage = {{nodeId:'', index:-1}};
const activeSmartGenerationRuns = new Map();
const cancellingSmartGenerationIds = new Set();
const smartNodeRunTokens = new Map();
function nowMs(){{ return ++tick; }}
function uid(){{ return `generation-${{tick}}`; }}
function nodeGenerationReferenceSnapshot(refs){{ return (refs || []).map(x => ({{...x}})); }}
function nodeGenerationSettingsMeta(settings){{ return {{settings:{{...settings}}, providerId:settings.provider_id || '', model:settings.model || '', ratio:settings.ratio || '', resolution:settings.resolution || ''}}; }}
function nodeGenerationLayoutSnapshot(node){{ return {{width:node.w, height:node.h, scale:node.scale}}; }}
function normalizeNodeGenerationOutputs(outputs){{ return (outputs || []).map((item, i) => typeof item === 'string' ? {{url:item, name:`output-${{i}}.png`, kind:'image'}} : {{...item}}); }}
function cleanHistoryImages(items){{ const seen = new Set(); return (items || []).filter(item => item?.url && !seen.has(item.url) && seen.add(item.url)); }}
function mediaKindForUrls(){{ return 'image'; }}
function cascadeOutputTitle(kind, count){{ return count > 1 ? 'Group' : 'Image'; }}
function nodeGenerationMetaFromAttempt(attempt){{ return {{prompt:attempt.prompt, displayPrompt:attempt.displayPrompt, settings:attempt.settings, createdAt:attempt.createdAt}}; }}
function attachRunMeta(node, meta){{ node.runPrompt = meta.displayPrompt; node.runSettings = meta.settings; }}
function clearSmartNodeBusyState(node){{ smartNodeRunTokens.delete(node.id); node.pending = 0; node.running = false; node.queued = false; delete node.pendingTasks; return node; }}
function markSmartNodeComplete(node){{ clearSmartNodeBusyState(node); node.runFinishedAt = nowMs(); }}
{functions}
const oldItem = {{id:'v1', status:'success', createdAt:1, completedAt:2, outputKind:'image', outputs:[{{url:'/old.png'}}]}};
const node = {{id:'node-1', w:320, h:260, scale:1, images:[{{url:'/old.png'}}], generationHistory:[oldItem], currentGenerationId:'v1'}};
const failed = createNodeGenerationAttempt(node, {{prompt:'try 2', displayPrompt:'try 2', inputRefs:[]}}, {{provider_id:'p', model:'m'}}, 'image');
const during = {{current:node.currentGenerationId, image:node.images[0].url, active:node.activeGenerationId, history:node.generationHistory.length}};
finishNodeGenerationAttempt(node, 'failed', 'boom', failed.id);
const afterFailure = {{current:node.currentGenerationId, image:node.images[0].url, status:failed.status}};
const success = createNodeGenerationAttempt(node, {{prompt:'try 3', displayPrompt:'try 3', inputRefs:[]}}, {{provider_id:'p', model:'m'}}, 'image');
completeNodeGenerationAttempt(node, [{{url:'/new.png'}}], {{generationId:success.id, kind:'image'}});
const afterSuccess = {{current:node.currentGenerationId, image:node.images[0].url, status:success.status, history:node.generationHistory.length, w:node.w, h:node.h}};
applyNodeGenerationAttempt(node, oldItem, {{layout:nodeGenerationLayoutSnapshot(node)}});
const adopted = {{current:node.currentGenerationId, image:node.images[0].url, history:node.generationHistory.length}};
console.log(JSON.stringify({{during, afterFailure, afterSuccess, adopted}}));
"""
        completed = subprocess.run(["node", "-e", harness], cwd=ROOT, text=True, capture_output=True, check=True)
        result = json.loads(completed.stdout)
        self.assertEqual(result["during"], {"current": "v1", "image": "/old.png", "active": "generation-1000", "history": 2})
        self.assertEqual(result["afterFailure"], {"current": "v1", "image": "/old.png", "status": "failed"})
        self.assertEqual(result["afterSuccess"]["image"], "/new.png")
        self.assertEqual(result["afterSuccess"]["status"], "success")
        self.assertEqual(result["afterSuccess"]["history"], 3)
        self.assertEqual((result["afterSuccess"]["w"], result["afterSuccess"]["h"]), (320, 260))
        self.assertEqual(result["adopted"], {"current": "v1", "image": "/old.png", "history": 3})

    def test_05_api_tasks_are_bound_back_to_the_same_attempt(self):
        run = function_source("runGeneration")
        binder = function_source("bindNodeGenerationAttemptTasks")
        finalizer = function_source("finalizeSmartPendingTask")
        self.assertIn("generationId:generationAttempt?.id || ''", run)
        self.assertIn("attempt.taskIds = ids", binder)
        self.assertIn("nodeGenerationAttemptForTask(node, taskId)", finalizer)
        self.assertIn("generationAttempt.outputs =", finalizer)
        self.assertIn("completeNodeGenerationAttempt", finalizer)

    def test_06_success_switch_is_atomic_and_current_images_remain_the_compatibility_output(self):
        complete = function_source("completeNodeGenerationAttempt")
        apply = function_source("applyNodeGenerationAttempt")
        images = function_source("imagesForNode")
        self.assertLess(complete.index("attempt.outputs = merged"), complete.index("attempt.status = 'success'"))
        self.assertLess(complete.index("attempt.status = 'success'"), complete.index("applyNodeGenerationAttempt"))
        self.assertIn("node.images = outputs", apply)
        self.assertIn("node.currentGenerationId = attempt.id", apply)
        self.assertIn("node?.images", images)

    def test_07_adopt_is_local_undoable_and_does_not_run_or_upload(self):
        adopt = function_source("adoptNodeGenerationHistory")
        self.assertIn("pushUndo()", adopt)
        self.assertIn("applyNodeGenerationAttempt(node, attempt", adopt)
        self.assertIn("scheduleSave()", adopt)
        for forbidden in ("fetch(", "runGeneration", "upload", "createNode"):
            self.assertNotIn(forbidden, adopt)

    def test_08_legacy_item_uses_only_metadata_saved_on_the_old_node(self):
        legacy = function_source("legacyNodeGenerationHistoryItem")
        self.assertIn("node.runSettings", legacy)
        self.assertIn("node.runInputRefs || node.runPromptRefs", legacy)
        self.assertIn("node.runModelPrompt || node.runPrompt", legacy)
        self.assertNotIn("smartSettingsForNode", legacy)
        self.assertNotIn("cloneSmartSettings(settings)", legacy)

    def test_09_history_toolbar_panel_and_loading_overlay_are_present(self):
        toolbar = function_source("smartNodeToolbarHtml")
        panel = function_source("renderNodeGenerationHistoryPanel")
        self.assertIn("key:'history'", toolbar)
        self.assertIn("生成历史", toolbar)
        self.assertIn("采纳到当前节点", panel)
        self.assertIn("currentGenerationId", panel)
        self.assertIn("node-generation-running-overlay", function_source("nodeGenerationRunningOverlayHtml"))
        self.assertIn(".node-generation-history-backdrop", SMART_CSS)
        self.assertIn(".node-generation-running .node-body", SMART_CSS)

    def test_10_jimeng_failure_and_canvas_merge_keep_history_semantics(self):
        jimeng = function_source("applyJimengQueryResult")
        recover = function_source("querySmartImageTaskNow")
        merge = function_source("mergeNodeGenerationState")
        self.assertIn("finishNodeGenerationAttempt(node, 'failed'", jimeng)
        self.assertIn("finishNodeGenerationAttempt(node, 'failed'", recover)
        self.assertIn("mergeNodeGenerationHistory", merge)
        self.assertIn("currentGenerationChangedAt", merge)
        self.assertIn("normalizeNodeGenerationOutputs(current.outputs", merge)


if __name__ == "__main__":
    unittest.main()
