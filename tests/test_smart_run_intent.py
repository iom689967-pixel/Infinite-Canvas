import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART_PATH = ROOT / "static/js/smart-canvas.js"
SMART = SMART_PATH.read_text(encoding="utf-8")
SMART_HTML = (ROOT / "static/smart-canvas.html").read_text(encoding="utf-8")
SMART_CSS = (ROOT / "static/css/smart-canvas.css").read_text(encoding="utf-8")
SMART_I18N = (ROOT / "static/js/i18n/smart-canvas.js").read_text(encoding="utf-8")
SMART_I18N_LOADER = (ROOT / "static/js/i18n.js").read_text(encoding="utf-8")


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


def reference_harness(extra=""):
    functions = "\n".join(function_source(name) for name in (
        "inputRefKey",
        "blockedInputRefKeys",
        "manualReferenceImagesFor",
        "defaultReferenceImagesFor",
        "uniqueReferenceImages",
        "originalPromptTextFromParts",
        "buildPromptRequest",
    ))
    script = f"""
const SMART_REFERENCE_IMAGE_MAX = 20;
let settings = {{engine:'api'}};
let smartLoopContext = null;
let promptParts = [
  {{type:'text', text:'make another image '}},
  {{type:'image', url:'/mention.png', name:'Mention', nodeId:'mention-node', imageIndex:0, kind:'image'}}
];
function smartImageUsesWorkflowInput(){{ return false; }}
function selfReferenceImagesForNode(node){{ return (node.images || []).map((img, index) => ({{...img, nodeId:node.id, imageIndex:index}})); }}
function inputImagesFor(node){{ return node.incoming || []; }}
function workflowInputImagesFor(node){{ return node.workflowIncoming || []; }}
function smartCanvasReferenceImagesFor(node){{ return node.canvasRefs || []; }}
function collectPromptParts(){{ return promptParts.map(item => ({{...item}})); }}
function inputPromptTextFor(){{ return ''; }}
function isSmartGroupNode(){{ return false; }}
function textForNode(){{ return ''; }}
function mediaKindForItem(item){{ return item?.kind || 'image'; }}
function tr(key){{ return key === 'smart.refMapHeader' ? 'Reference map' : 'Request'; }}
{functions}
const node = {{
  id:'target',
  images:[{{url:'/adopted-v2.png', name:'Current', kind:'image'}}],
  generationHistory:[
    {{id:'v1', status:'success', outputs:[{{url:'/history-v1.png'}}]}},
    {{id:'v2', status:'success', outputs:[{{url:'/adopted-v2.png'}}]}}
  ],
  currentGenerationId:'v2',
  incoming:[{{url:'/incoming.png', name:'Incoming', nodeId:'upstream', imageIndex:0, kind:'image'}}],
  manualInputRefs:[{{url:'/manual.png', name:'Manual', kind:'image'}}],
  canvasRefs:[{{url:'/canvas.png', name:'Canvas', nodeId:'canvas-source', imageIndex:0, kind:'image'}}]
}};
{extra}
"""
    completed = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, check=True)
    return json.loads(completed.stdout)


class SmartRunIntentContractTests(unittest.TestCase):
    def test_01_default_reference_collection_requires_explicit_include_self(self):
        source = function_source("defaultReferenceImagesFor")
        self.assertIn("options={}", source)
        self.assertIn("Boolean(options?.includeSelf)", source)
        self.assertIn("includeSelf ? selfReferenceImagesForNode", source)

    def test_02_normal_regenerate_excludes_current_but_keeps_all_explicit_refs(self):
        result = reference_harness("""
const request = buildPromptRequest(node, null, false, smartLoopContext, {includeSelf:false});
console.log(JSON.stringify(request.refs.map(ref => ref.url)));
""")
        self.assertEqual(result, ["/incoming.png", "/manual.png", "/canvas.png", "/mention.png"])

    def test_03_continue_edit_includes_current_first_and_keeps_explicit_order(self):
        result = reference_harness("""
const request = buildPromptRequest(node, null, false, smartLoopContext, {includeSelf:true});
console.log(JSON.stringify(request.refs.map(ref => ref.url)));
""")
        self.assertEqual(result, ["/adopted-v2.png", "/incoming.png", "/manual.png", "/canvas.png", "/mention.png"])

    def test_04_history_versions_are_never_collected_and_adopt_only_changes_current_self(self):
        result = reference_harness("""
const before = buildPromptRequest(node, null, false, smartLoopContext, {includeSelf:false}).refs.map(ref => ref.url);
node.currentGenerationId = 'v1';
node.images = [{url:'/history-v1.png', name:'Adopted v1', kind:'image'}];
const normalAfterAdopt = buildPromptRequest(node, null, false, smartLoopContext, {includeSelf:false}).refs.map(ref => ref.url);
const continueAfterAdopt = buildPromptRequest(node, null, false, smartLoopContext, {includeSelf:true}).refs.map(ref => ref.url);
console.log(JSON.stringify({before, normalAfterAdopt, continueAfterAdopt}));
""")
        self.assertNotIn("/history-v1.png", result["before"])
        self.assertNotIn("/history-v1.png", result["normalAfterAdopt"])
        self.assertEqual(result["continueAfterAdopt"][0], "/history-v1.png")

    def test_05_normal_regenerate_preserves_all_twenty_explicit_reference_slots(self):
        result = reference_harness("""
promptParts = [{type:'text', text:'twenty refs'}];
node.incoming = Array.from({length:20}, (_, index) => ({url:`/explicit-${index + 1}.png`, nodeId:`n-${index + 1}`, imageIndex:0, kind:'image'}));
node.manualInputRefs = [];
node.canvasRefs = [];
const normal = buildPromptRequest(node, null, false, smartLoopContext, {includeSelf:false}).refs.map(ref => ref.url);
const continuing = buildPromptRequest(node, null, false, smartLoopContext, {includeSelf:true}).refs.map(ref => ref.url);
console.log(JSON.stringify({normal, continuing}));
""")
        self.assertEqual(len(result["normal"]), 20)
        self.assertEqual(result["normal"][0], "/explicit-1.png")
        self.assertEqual(result["normal"][-1], "/explicit-20.png")
        self.assertEqual(result["continuing"][0], "/adopted-v2.png")
        self.assertEqual(len(result["continuing"]), 21)

    def test_06_run_generation_defaults_false_and_passes_one_snapshot_to_builder(self):
        run = function_source("runGeneration")
        self.assertIn("async function runGeneration(options={})", run)
        self.assertIn("Boolean(options?.includeSelf)", run)
        self.assertIn("smartRunSelfRequirement(node, initialRunSettings).required", run)
        self.assertIn("buildPromptRequest(node, null, true, smartLoopContext, {includeSelf})", run)

    def test_07_outpaint_and_explicit_comfy_edit_paths_keep_self(self):
        policy = function_source("smartRunSelfRequirement")
        cascade = function_source("runCascadeStepIntoNode")
        smart_cascade = function_source("runSmartCascade")
        focus = function_source("buildFocusEditRequest")
        self.assertIn("validOutpaintSize(node)", policy)
        self.assertIn("['enhance','edit'].includes", policy)
        self.assertIn("MS_GEN_MODELS[sourceSettings.msgenModel || 'zimage']?.supportsImage", policy)
        self.assertIn("selfReferenceImagesForNode(sourceNode", cascade)
        self.assertIn("{includeSelf:true}", cascade)
        self.assertIn("defaultReferenceImagesFor(graph.root, true, ctx, {includeSelf:true})", smart_cascade)
        self.assertIn("role:'target_image'", focus)
        self.assertIn("role:'reference_element'", focus)

    def test_08_split_run_ui_is_explicit_and_input_label_matches_payload(self):
        self.assertIn('id="runIntentControl"', SMART_HTML)
        self.assertIn('data-run-intent="regenerate"', SMART_HTML)
        self.assertIn('data-run-intent="continue-edit"', SMART_HTML)
        self.assertIn("使用当前显式参考图重新生成，不引用当前结果", SMART_HTML)
        self.assertIn("将当前采用结果作为参考图继续生成", SMART_HTML)
        self.assertIn("当前结果 / 继续编辑输入", SMART_I18N)
        self.assertIn("2026.09.01.run-intent.1", SMART_I18N_LOADER)
        self.assertIn("/static/js/i18n.js?v=2026.09.01.run-intent.1", SMART_HTML)
        self.assertIn(".run-intent-menu", SMART_CSS)

    def test_09_intent_is_transient_and_does_not_change_canvas_serialization(self):
        storage = function_source("canvasForStorage")
        self.assertNotIn("smartRunIntent", storage)
        self.assertNotIn("includeSelf", storage)
        self.assertIn("let smartRunIntent = SMART_RUN_INTENT_REGENERATE", SMART)

    def test_10_generation_history_and_adopted_output_contract_remain_unchanged(self):
        apply = function_source("applyNodeGenerationAttempt")
        complete = function_source("completeNodeGenerationAttempt")
        self.assertIn("node.images = outputs", apply)
        self.assertIn("node.currentGenerationId = attempt.id", apply)
        self.assertIn("attempt.status = 'success'", complete)
        self.assertNotIn("includeSelf", apply)
        self.assertNotIn("includeSelf", complete)


if __name__ == "__main__":
    unittest.main()
