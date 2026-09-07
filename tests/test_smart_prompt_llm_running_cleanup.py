import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART = (ROOT / "static/js/smart-canvas.js").read_text(encoding="utf-8")


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


class SmartPromptLLMRunningCleanupTests(unittest.TestCase):
    def run_case(self, case):
        runner = function_source("runPromptLLMNode")
        error_helpers = "\n".join(
            function_source(name) for name in ("apiErrorMessage", "responseErrorMessage")
        )
        harness = f"""
const testCase = {json.dumps(case)};
let nodes = [{{id:'prompt-1', type:'smart-prompt', text:'draft', llmProvider:'gemini', llmModel:'gemini-3.8-flash'}}];
let renderStates = [];
let saveCount = 0;
let toasts = [];
function promptNodeLLMInputText(){{ return 'reply OK only'; }}
function resolveChatProviderId(){{ return 'gemini'; }}
function resolveChatModel(){{ return 'gemini-3.8-flash'; }}
function promptNodeInputMediaForLLM(){{ return []; }}
function imageRefsOnly(){{ return []; }}
function videoRefsOnly(){{ return []; }}
function render(){{ renderStates.push(nodes[0].running); }}
function scheduleSave(){{ saveCount += 1; }}
function toast(message){{ toasts.push(message); }}
function tr(key){{ return key; }}
globalThis.fetch = async () => {{
    if(testCase === 'success') return {{ok:true, json:async () => ({{text:'OK'}})}};
    if(testCase === 'timeout') return {{ok:false, clone:() => ({{json:async () => ({{detail:'Canvas LLM 请求上游超时（120 秒）'}})}}), text:async () => ''}};
    if(testCase === 'http-error') return {{ok:false, clone:() => ({{json:async () => ({{detail:'upstream 502'}})}}), text:async () => ''}};
    throw new Error('network reset');
}};
{error_helpers}
{runner}
(async () => {{
    await runPromptLLMNode('prompt-1');
    console.log(JSON.stringify({{node:nodes[0], renderStates, saveCount, toasts}}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
        completed = subprocess.run(
            ["node", "-e", harness],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(completed.stdout)

    def test_success_updates_text_and_clears_running(self):
        result = self.run_case("success")
        self.assertFalse(result["node"]["running"])
        self.assertEqual(result["node"]["text"], "OK")
        self.assertEqual(result["saveCount"], 1)
        self.assertEqual(result["toasts"], [])
        self.assertEqual(result["renderStates"], [True, False])

    def test_gemini_timeout_clears_running_and_surfaces_error(self):
        result = self.run_case("timeout")
        self.assertFalse(result["node"]["running"])
        self.assertEqual(result["node"]["text"], "draft")
        self.assertIn("超时", result["toasts"][0])
        self.assertEqual(result["renderStates"], [True, False])

    def test_http_error_clears_running_and_surfaces_error(self):
        result = self.run_case("http-error")
        self.assertFalse(result["node"]["running"])
        self.assertEqual(result["node"]["text"], "draft")
        self.assertIn("502", result["toasts"][0])
        self.assertEqual(result["renderStates"], [True, False])


if __name__ == "__main__":
    unittest.main()
