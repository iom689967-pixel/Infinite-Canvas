import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART_PATH = ROOT / "static/js/smart-canvas.js"
SMART_HTML_PATH = ROOT / "static/smart-canvas.html"
SMART = SMART_PATH.read_text(encoding="utf-8")
SMART_HTML = SMART_HTML_PATH.read_text(encoding="utf-8")


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


def quick_connect_registry_source():
    match = re.search(
        r"const QUICK_CONNECT_NODE_REGISTRY\s*=\s*Object\.freeze\(\[(.*?)\]\);",
        SMART,
        re.S,
    )
    if not match:
        raise AssertionError("missing QUICK_CONNECT_NODE_REGISTRY")
    return match.group(1)


class SmartQuickConnectPromptContractTests(unittest.TestCase):
    def test_01_registry_replaces_text_with_prompt_copy_and_main_menu_icon(self):
        registry = quick_connect_registry_source()
        self.assertIn("type:'prompt'", registry)
        self.assertIn("nodeType:'smart-prompt'", registry)
        self.assertIn("label:'提示词'", registry)
        self.assertIn("description:'手写或用 LLM 生成文本'", registry)
        self.assertIn("icon:'text-cursor-input'", registry)
        self.assertNotIn("type:'text'", registry)
        self.assertNotIn("label:'文本'", registry)
        prompt_card = re.search(
            r'data-create-type="prompt".*?data-lucide="([^"]+)"',
            SMART_HTML,
            re.S,
        )
        self.assertIsNotNone(prompt_card)
        self.assertEqual(prompt_card.group(1), "text-cursor-input")

    def test_02_quick_connect_factory_creates_a_full_smart_prompt(self):
        functions = "\n".join(
            function_source(name) for name in ("createPromptNode", "createPromptNodeAt")
        )
        harness = f"""
let nodes = [];
let selectedId = '';
let undoCount = 0;
let renderCount = 0;
let saveCount = 0;
function uid(prefix){{ return `${{prefix}}-1`; }}
function pushUndo(){{ undoCount += 1; }}
function resolveChatProviderId(){{ return 'provider-1'; }}
function resolveChatModel(value, provider){{ return `${{provider}}-model`; }}
function render(){{ renderCount += 1; }}
function scheduleSave(){{ saveCount += 1; }}
{functions}
const node = createPromptNodeAt({{x:500, y:300}}, {{select:true, skipUndo:true, deferRender:true, anchorPort:'in'}});
console.log(JSON.stringify({{node, selectedId, undoCount, renderCount, saveCount}}));
"""
        completed = subprocess.run(
            ["node", "-e", harness],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["node"]["type"], "smart-prompt")
        self.assertEqual((result["node"]["x"], result["node"]["y"]), (500, 180))
        self.assertEqual(result["node"]["promptSeparator"], ";")
        self.assertIn("llmEnabled", result["node"])
        self.assertEqual(result["selectedId"], result["node"]["id"])
        self.assertEqual((result["undoCount"], result["renderCount"], result["saveCount"]), (0, 0, 0))

    def test_03_quick_prompt_uses_the_existing_complete_prompt_renderer(self):
        body = function_source("promptNodeBodyHtml")
        router = function_source("nodeBodyHtml")
        for label in ("模板库", "分隔符", "LLM", "展开编辑"):
            self.assertIn(label, body)
        self.assertIn("if(node.type === 'smart-prompt') return promptNodeBodyHtml(node)", router)

    def test_04_main_menu_and_quick_connect_share_create_prompt_node(self):
        registry = quick_connect_registry_source()
        quick_factory = function_source("createPromptNodeAt")
        main_menu = function_source("createNodeFromMenu")
        self.assertIn("createPromptNodeAt(point, options)", registry)
        self.assertIn("return createPromptNode(", quick_factory)
        self.assertIn("createPromptNode(p.x - 158, p.y - 97)", main_menu)
        self.assertNotIn("type:'smart-prompt'", quick_factory)

    def test_05_image_to_prompt_uses_normal_edge_and_existing_multimodal_resolver(self):
        creator = function_source("createQuickConnectedNode")
        resolver = function_source("promptNodeInputMediaForLLM")
        input_images = function_source("inputImagesFor")
        outputs = function_source("outputImagesForNode")
        images = function_source("imagesForNode")
        runner = function_source("runPromptLLMNode")
        self.assertIn("connectInputNode(", creator)
        self.assertIn("inputImagesFor(node)", resolver)
        self.assertIn("outputImagesForNode(input", input_images)
        self.assertIn("return imagesForNode(node).filter", outputs)
        self.assertIn("node?.images", images)
        self.assertIn("promptNodeInputMediaForLLM(node)", runner)
        self.assertNotIn("generationHistory", "\n".join((resolver, input_images, outputs, images, runner)))

    def test_06_prompt_and_legacy_text_sources_remain_compatible(self):
        validator = function_source("canConnectSmartInputNodes")
        text_reader = function_source("textForNode")
        self.assertIn("if(to.type !== 'smart-loop') return true", validator)
        self.assertIn("node.type === 'smart-prompt'", text_reader)
        self.assertIn("node.type === 'smart-text'", text_reader)

    def test_07_multi_selection_keeps_batch_validation_and_atomic_edge_creation(self):
        registry = quick_connect_registry_source()
        accepts = function_source("quickConnectEntryAcceptsDrag")
        creator = function_source("createQuickConnectedNode")
        batch = function_source("connectSmartInputNodeBatch")
        self.assertIn("nodeType:'smart-prompt'", registry)
        self.assertIn("canConnectSmartInputNodeBatch(drag.sourceIds, previewTarget)", accepts)
        self.assertIn("connectSmartInputNodeBatch(aggregateSourceIds, newNode.id", creator)
        self.assertLess(batch.index("canConnectSmartInputNodeBatch"), batch.index("for(const sourceId of ids)"))
        self.assertIn("canvas.connections = connectionsBefore", batch)

    def test_08_old_smart_text_is_not_migrated_or_removed(self):
        normalizer = function_source("normalizeLegacySmartNode")
        renderer = function_source("nodeBodyHtml")
        text_factory = function_source("createTextNode")
        self.assertNotIn("smart-text", normalizer)
        self.assertIn("if(node.type === 'smart-text') return textNodeBodyHtml(node)", renderer)
        self.assertIn("type:'smart-text'", text_factory)
        self.assertIn("function createTextNodeAt", SMART)

    def test_09_quick_connect_prompt_and_edges_remain_one_undo_transaction(self):
        prompt_factory = function_source("createPromptNode")
        creator = function_source("createQuickConnectedNode")
        self.assertIn("if(!options.skipUndo) pushUndo()", prompt_factory)
        self.assertIn("options.deferRender !== true", prompt_factory)
        self.assertIn("skipUndo:true, deferRender:true", creator)
        self.assertNotIn("pushUndo()", creator)
        self.assertEqual(creator.count("commitPendingUndo()"), 1)
        self.assertIn("discardPendingUndo()", creator)

    def test_10_other_quick_connect_entries_and_schema_are_unchanged(self):
        registry = quick_connect_registry_source()
        self.assertIn("type:'image-generation'", registry)
        self.assertIn("createGenerationNode('image', point, options)", registry)
        self.assertIn("type:'video-generation'", registry)
        self.assertIn("createGenerationNode('video', point, options)", registry)
        for forbidden in ("quickPrompt", "promptFromQuickConnect", "smartTextLLM"):
            self.assertNotIn(forbidden, SMART)


if __name__ == "__main__":
    unittest.main()
