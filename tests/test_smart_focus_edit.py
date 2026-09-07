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
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(", SMART)
    if not match:
        raise AssertionError(f"missing function: {name}")
    paren = SMART.find("(", match.start())
    depth = 0
    params_end = -1
    for index in range(paren, len(SMART)):
        if SMART[index] == "(":
            depth += 1
        elif SMART[index] == ")":
            depth -= 1
            if depth == 0:
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


class SmartFocusEditContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        functions = "\n".join(function_source(name) for name in (
            "focusEditReferenceSnapshot",
            "liveNodeGenerationState",
            "nodeGenerationHistoryItems",
            "createNodeGenerationAttempt",
            "nodeGenerationAttempt",
            "nodeGenerationMetaFromAttempt",
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
function nodeGenerationReferenceSnapshot(refs){{ return (refs || []).map(item => ({{...item}})); }}
function nodeGenerationSettingsMeta(settings){{ return {{settings:{{...settings}}, providerId:settings.provider_id || '', model:settings.model || '', ratio:settings.ratio || '', resolution:settings.resolution || ''}}; }}
function nodeGenerationLayoutSnapshot(node){{ return {{width:node.w, height:node.h, scale:node.scale}}; }}
function normalizeNodeGenerationOutputs(outputs){{ return (outputs || []).map((item, index) => typeof item === 'string' ? {{url:item, name:`output-${{index}}.png`, kind:'image'}} : {{...item}}); }}
function cleanHistoryImages(items){{ const seen = new Set(); return (items || []).filter(item => item?.url && !seen.has(item.url) && seen.add(item.url)); }}
function mediaKindForUrls(){{ return 'image'; }}
function cascadeOutputTitle(kind, count){{ return count > 1 ? 'Group' : 'Image'; }}
function cloneSmartSettings(settings){{ return {{...(settings || {{}})}}; }}
function attachRunMeta(node, meta){{ node.runPrompt = meta.displayPrompt; node.runSettings = meta.settings; }}
function clearSmartNodeBusyState(node){{ smartNodeRunTokens.delete(node.id); node.pending = 0; node.running = false; node.queued = false; delete node.pendingTasks; return node; }}
function markSmartNodeComplete(node){{ clearSmartNodeBusyState(node); node.runFinishedAt = nowMs(); }}
{functions}
const original = {{id:'v1', status:'success', createdAt:1, completedAt:2, outputKind:'image', outputs:[{{url:'/old.png'}}]}};
const node = {{id:'target', w:320, h:220, scale:1, images:[{{url:'/old.png'}}], generationHistory:[original], currentGenerationId:'v1'}};
const ref = {{kind:'focus-element-reference', sourceNodeId:'source', sourceImageUrl:'/source.png', label:'奶油白上衣', point:{{x:.4,y:.3}}, region:{{x:.2,y:.1,width:.4,height:.4,approximate:true}}}};
const success = createNodeGenerationAttempt(node, {{mode:'focus-edit', userInstruction:'只改上衣', referenceElement:ref, inputRefs:[{{url:'/old.png'}},{{url:'/source.png'}}], prompt:'edit', displayPrompt:'只改上衣'}}, {{provider_id:'p', model:'m'}}, 'image');
completeNodeGenerationAttempt(node, [{{url:'/focus.png'}}], {{generationId:success.id, kind:'image'}});
const successResult = {{status:success.status, image:node.images[0].url, current:node.currentGenerationId, history:node.generationHistory.length, mode:success.mode, label:success.referenceElement.label}};
const failed = createNodeGenerationAttempt(node, {{mode:'focus-edit', userInstruction:'失败测试', referenceElement:ref, inputRefs:[]}}, {{provider_id:'p', model:'m'}}, 'image');
finishNodeGenerationAttempt(node, 'failed', 'boom', failed.id);
const failedResult = {{status:failed.status, image:node.images[0].url, current:node.currentGenerationId}};
const cancelled = createNodeGenerationAttempt(node, {{mode:'focus-edit', userInstruction:'取消测试', referenceElement:ref, inputRefs:[]}}, {{provider_id:'p', model:'m'}}, 'image');
finishNodeGenerationAttempt(node, 'cancelled', 'cancel', cancelled.id);
const cancelledResult = {{status:cancelled.status, image:node.images[0].url, current:node.currentGenerationId}};
console.log(JSON.stringify({{successResult, failedResult, cancelledResult}}));
"""
        completed = subprocess.run(["node", "-e", harness], cwd=ROOT, text=True, capture_output=True, check=True)
        cls.history_result = json.loads(completed.stdout)

    def test_01_image_toolbar_has_focus_edit_action(self):
        toolbar = function_source("smartNodeToolbarHtml")
        action = function_source("runSmartNodeToolbarAction")
        self.assertIn("key:'focus-edit'", toolbar)
        self.assertIn("label:'焦点编辑'", toolbar)
        self.assertIn("startFocusEdit", action)

    def test_02_enter_mode_keeps_target_node_id_and_selection(self):
        start = function_source("startFocusEdit")
        self.assertIn("focusEditSession.targetNodeId = node.id", start)
        self.assertIn("focusEditSession.status = FOCUS_EDIT_PICKING", start)
        self.assertIn("selectedId = node.id", start)

    def test_03_source_node_and_current_image_are_taken_from_clicked_holder(self):
        handler = function_source("handleFocusEditImageClick")
        self.assertIn("sourceNodeId", handler)
        self.assertIn("focusEditImageForNode(sourceNode, sourceImageIndex)", handler)
        self.assertIn("sourceImageUrl:smartOriginalMediaUrl(sourceImage)", handler)

    def test_04_normalized_coordinate_handles_contain_cover_and_letterbox(self):
        functions = "\n".join(function_source(name) for name in (
            "focusObjectPositionFraction",
            "normalizedImagePointFromClient",
            "approximateFocusRegion",
        ))
        harness = f"""
{functions}
function image(fit, naturalWidth, naturalHeight){{
  return {{naturalWidth,naturalHeight,ownerDocument:{{defaultView:{{getComputedStyle:()=>({{objectFit:fit,objectPosition:'50% 50%'}})}}}},getBoundingClientRect:()=>({{left:0,top:0,right:100,bottom:100,width:100,height:100}})}};
}}
const contain = normalizedImagePointFromClient(image('contain',200,100),50,50);
const letterbox = normalizedImagePointFromClient(image('contain',200,100),50,10);
const coverLeft = normalizedImagePointFromClient(image('cover',200,100),0,50);
const coverCenter = normalizedImagePointFromClient(image('cover',200,100),50,50);
const region = approximateFocusRegion(contain);
console.log(JSON.stringify({{contain,letterbox,coverLeft,coverCenter,region}}));
"""
        result = json.loads(subprocess.run(["node", "-e", harness], cwd=ROOT, text=True, capture_output=True, check=True).stdout)
        self.assertAlmostEqual(result["contain"]["x"], .5)
        self.assertAlmostEqual(result["contain"]["y"], .5)
        self.assertIsNone(result["letterbox"])
        self.assertAlmostEqual(result["coverLeft"]["x"], .25)
        self.assertAlmostEqual(result["coverCenter"]["x"], .5)
        self.assertTrue(result["region"]["approximate"])

    def test_05_recognition_loading_success_and_failure_states_are_explicit(self):
        handler = function_source("handleFocusEditImageClick")
        self.assertIn("FOCUS_EDIT_RECOGNIZING", handler)
        self.assertIn("focusEditSession.candidates = candidates", handler)
        self.assertIn("focusEditSession.recognitionError", handler)
        self.assertIn("FOCUS_EDIT_PICKING", handler)

    def test_06_candidate_parser_accepts_two_to_five_items(self):
        parser = function_source("parseFocusEditCandidates")
        harness = f"""
{parser}
const good = parseFocusEditCandidates('{{"candidates":[{{"label":"上衣"}},{{"label":"领口"}},{{"label":"袖子"}}]}}');
let bad = '';
try {{ parseFocusEditCandidates('[{{"label":"只有一个"}}]'); }} catch(error) {{ bad = error.message; }}
console.log(JSON.stringify({{good,bad}}));
"""
        result = json.loads(subprocess.run(["node", "-e", harness], cwd=ROOT, text=True, capture_output=True, check=True).stdout)
        self.assertEqual([item["label"] for item in result["good"]], ["上衣", "领口", "袖子"])
        self.assertIn("足够", result["bad"])

    def test_07_selected_reference_is_structured_not_just_a_label(self):
        pick = function_source("selectFocusEditCandidate")
        for field in ("sourceNodeId", "sourceImageUrl", "label", "point", "region", "crop", "previewUrl"):
            self.assertIn(field, pick)
        self.assertIn("kind:'focus-element-reference'", pick)

    def test_08_panel_shows_target_reference_source_and_instruction(self):
        panel = function_source("focusEditUiHtml")
        for text in ("目标图", "参考元素", "修改说明", "执行焦点编辑"):
            self.assertIn(text, panel)
        self.assertIn("reference.previewUrl", panel)

    def test_09_reference_can_be_removed_and_reselected(self):
        clear = function_source("clearFocusEditReference")
        self.assertIn("focusEditSession.referenceElement = null", clear)
        self.assertIn("FOCUS_EDIT_PICKING", clear)
        self.assertIn("data-focus-reselect", function_source("focusEditUiHtml"))

    def test_10_escape_exits_focus_mode_before_other_canvas_shortcuts(self):
        keydown = SMART[SMART.index("window.addEventListener('keydown'"):]
        self.assertIn("e.key === 'Escape' && focusEditActive()", keydown)
        self.assertIn("exitFocusEdit()", keydown)

    def test_11_execution_never_creates_a_sibling_result_node(self):
        run = function_source("runFocusEdit")
        self.assertNotIn("createNode(", run)
        self.assertNotIn("createPendingOutputFromSource", run)
        self.assertIn("completeNodeGenerationAttempt(target", run)

    def test_12_success_uses_existing_generation_history_and_adopts_in_place(self):
        result = self.history_result["successResult"]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["image"], "/focus.png")
        self.assertEqual(result["current"], "generation-1000")
        self.assertEqual(result["history"], 2)
        self.assertEqual(result["mode"], "focus-edit")

    def test_13_failed_attempt_keeps_current_adopted_image(self):
        result = self.history_result["failedResult"]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["image"], "/focus.png")
        self.assertEqual(result["current"], "generation-1000")

    def test_14_cancelled_attempt_keeps_current_adopted_image(self):
        result = self.history_result["cancelledResult"]
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["image"], "/focus.png")
        self.assertEqual(result["current"], "generation-1000")

    def test_15_history_saves_focus_reference_snapshot_and_instruction(self):
        creator = function_source("createNodeGenerationAttempt")
        history_panel = function_source("renderNodeGenerationHistoryPanel")
        self.assertIn("mode:String(meta?.mode || '')", creator)
        self.assertIn("userInstruction:String(meta?.userInstruction || '')", creator)
        self.assertIn("referenceElement", creator)
        self.assertIn("nodeGenerationHistoryFocusElementHtml", history_panel)

    def test_16_old_nodes_need_no_focus_fields_or_migration(self):
        normalize = function_source("normalizeLegacySmartNode")
        storage = function_source("canvasForStorage")
        self.assertNotIn("focusEdit", normalize)
        self.assertIn("JSON.parse(JSON.stringify(canvas || {}))", storage)
        self.assertIn("freshFocusEditSession", SMART)

    def test_17_source_history_node_uses_current_adopted_node_images(self):
        resolver = function_source("imagesForNode")
        source = function_source("focusEditImageForNode")
        self.assertIn("node?.images", resolver)
        self.assertIn("imagesForNode(node)", source)
        self.assertNotIn("generationHistory", source)

    def test_18_exit_restores_normal_canvas_interactions(self):
        exit_source = function_source("exitFocusEdit")
        render_source = function_source("render")
        self.assertIn("freshFocusEditSession()", exit_source)
        self.assertIn("smart-focus-edit-active", exit_source)
        self.assertIn("render()", exit_source)
        self.assertIn("if(focusEditActive()) return", SMART)
        self.assertIn("renderFocusEditUi();\n    return;", render_source)

    def test_19_request_contains_target_visual_reference_visual_and_instruction(self):
        request = function_source("buildFocusEditRequest")
        self.assertIn("role:'target_image'", request)
        self.assertIn("role:'reference_element'", request)
        self.assertIn("用户修改说明", request)
        self.assertIn("reference.sourceImageUrl", request)

    def test_20_runtime_reuses_existing_llm_task_upload_and_theme_layers(self):
        recognition = function_source("recognizeFocusEditCandidates")
        run = function_source("runFocusEdit")
        self.assertIn("/api/canvas-llm", recognition)
        self.assertIn("runApiGeneration", run)
        self.assertIn("runComfyEdit", run)
        self.assertIn("runModelscopeGeneration", run)
        self.assertIn("uploadCroppedBlob", function_source("uploadFocusEditCrop"))
        self.assertIn(".focus-edit-panel", SMART_CSS)
        self.assertIn(".theme-dark .focus-edit-panel", SMART_CSS)

    def test_21_cancel_immediately_finishes_the_active_focus_attempt(self):
        cancel = function_source("cancelFocusEditRun")
        self.assertIn("finishNodeGenerationAttempt(target, 'cancelled'", cancel)
        self.assertIn("focusEditSession.runToken = null", cancel)
        self.assertIn("target.pending = 0", cancel)
        self.assertIn("delete target.pendingTasks", cancel)


if __name__ == "__main__":
    unittest.main()
