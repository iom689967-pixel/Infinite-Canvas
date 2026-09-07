import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMART = (ROOT / "static/js/smart-canvas.js").read_text(encoding="utf-8")
COMMON_I18N = (ROOT / "static/js/i18n/common.js").read_text(encoding="utf-8")
SMART_I18N = (ROOT / "static/js/i18n/smart-canvas.js").read_text(encoding="utf-8")


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
        functions = "\n".join(
            function_source(name)
            for name in (
                "apiErrorMessage",
                "responseErrorMessage",
                "promptLLMRequestId",
                "isPromptLLMAbortError",
                "notifyPromptLLMCancel",
                "cancelPromptLLMNode",
                "runPromptLLMNode",
            )
        )
        harness = f"""
const testCase = {json.dumps(case)};
const activePromptLLMRuns = new Map();
let nodes = [
  {{
    id:'prompt-a', type:'smart-prompt', text:'draft-a', input:'reply A',
    llmInstruction:'instruction-a', llmSystemEnabled:true, llmSystemPrompt:'system-a',
    llmProvider:'gemini', llmModel:'gemini-3.8-flash', upstream:['source-a']
  }},
  {{
    id:'prompt-b', type:'smart-prompt', text:'draft-b', input:'reply B',
    llmInstruction:'instruction-b', llmSystemEnabled:true, llmSystemPrompt:'system-b',
    llmProvider:'openai-compatible', llmModel:'mock-chat', upstream:['source-b']
  }}
];
let connections = [{{id:'edge-a', from:'source-a', to:'prompt-a'}}, {{id:'edge-b', from:'source-b', to:'prompt-b'}}];
let renderStates = [];
let saveCount = 0;
let toasts = [];
let requestPlans = [];
let canvasRequests = [];
let cancelRequests = [];

function deferredPlan(options={{}}){{
  return {{ignoreAbort:options.ignoreAbort === true, resolve:null, reject:null}};
}}
function response(status, payload){{
  return {{
    ok:status >= 200 && status < 300,
    status,
    json:async () => payload,
    clone:() => ({{json:async () => payload}}),
    text:async () => JSON.stringify(payload)
  }};
}}
function promptNodeLLMInputText(node){{ return node.input; }}
function resolveChatProviderId(value){{ return value; }}
function resolveChatModel(provider, value){{ return value; }}
function promptNodeInputMediaForLLM(){{ return []; }}
function imageRefsOnly(){{ return []; }}
function videoRefsOnly(){{ return []; }}
function render(){{ renderStates.push(nodes.map(node => Boolean(node.running))); }}
function scheduleSave(){{ saveCount += 1; }}
function toast(message){{ toasts.push(message); }}
function tr(key){{ return key; }}
globalThis.fetch = (url, options={{}}) => {{
  const body = options.body ? JSON.parse(options.body) : null;
  if(url === '/api/canvas-llm/cancel'){{
    cancelRequests.push({{body, keepalive:options.keepalive === true}});
    return Promise.resolve(response(200, {{ok:true}}));
  }}
  const plan = requestPlans.shift();
  if(!plan) return Promise.reject(new Error('missing request plan'));
  canvasRequests.push({{body, signal:options.signal, plan}});
  return new Promise((resolve, reject) => {{
    plan.resolve = value => resolve(value);
    plan.reject = reject;
    if(!plan.ignoreAbort){{
      options.signal.addEventListener('abort', () => {{
        const error = new Error('aborted');
        error.name = 'AbortError';
        reject(error);
      }}, {{once:true}});
    }}
  }});
}};
{functions}
function preserved(node){{
  return {{
    input:node.input,
    llmInstruction:node.llmInstruction,
    llmSystemEnabled:node.llmSystemEnabled,
    llmSystemPrompt:node.llmSystemPrompt,
    llmProvider:node.llmProvider,
    llmModel:node.llmModel,
    upstream:node.upstream,
    connections
  }};
}}

(async () => {{
  let intermediate = null;
  let before = null;
  if(testCase === 'success'){{
    const plan = deferredPlan(); requestPlans.push(plan);
    const run = runPromptLLMNode('prompt-a');
    plan.resolve(response(200, {{text:'OK'}}));
    await run;
  }} else if(testCase === 'timeout' || testCase === 'http-error'){{
    const plan = deferredPlan(); requestPlans.push(plan);
    const run = runPromptLLMNode('prompt-a');
    plan.resolve(testCase === 'timeout'
      ? response(504, {{detail:'server timeout text'}})
      : response(502, {{detail:'upstream 502'}}));
    await run;
  }} else if(testCase === 'stop'){{
    before = preserved(nodes[0]);
    const plan = deferredPlan(); requestPlans.push(plan);
    const run = runPromptLLMNode('prompt-a');
    cancelPromptLLMNode('prompt-a');
    await run;
    await Promise.resolve();
  }} else if(testCase === 'stale-before-b'){{
    const planA = deferredPlan({{ignoreAbort:true}}); requestPlans.push(planA);
    const runA = runPromptLLMNode('prompt-a');
    cancelPromptLLMNode('prompt-a', {{silent:true}});
    const planB = deferredPlan(); requestPlans.push(planB);
    const runB = runPromptLLMNode('prompt-a');
    planA.resolve(response(200, {{text:'OLD'}}));
    await runA;
    intermediate = {{running:nodes[0].running, text:nodes[0].text}};
    planB.resolve(response(200, {{text:'NEW'}}));
    await runB;
  }} else if(testCase === 'stale-after-b'){{
    const planA = deferredPlan({{ignoreAbort:true}}); requestPlans.push(planA);
    const runA = runPromptLLMNode('prompt-a');
    cancelPromptLLMNode('prompt-a', {{silent:true}});
    const planB = deferredPlan(); requestPlans.push(planB);
    const runB = runPromptLLMNode('prompt-a');
    planB.resolve(response(200, {{text:'NEW'}}));
    await runB;
    planA.resolve(response(200, {{text:'OLD'}}));
    await runA;
  }} else if(testCase === 'multi-node'){{
    before = {{a:preserved(nodes[0]), b:preserved(nodes[1])}};
    const planA = deferredPlan(); const planB = deferredPlan(); requestPlans.push(planA, planB);
    const runA = runPromptLLMNode('prompt-a');
    const runB = runPromptLLMNode('prompt-b');
    cancelPromptLLMNode('prompt-a', {{silent:true}});
    planB.resolve(response(200, {{text:'B-DONE'}}));
    await Promise.all([runA, runB]);
  }} else {{
    throw new Error(`unknown test case: ${{testCase}}`);
  }}
  console.log(JSON.stringify({{
    nodes, renderStates, saveCount, toasts, intermediate, before,
    connections, activeCount:activePromptLLMRuns.size,
    canvasRequests:canvasRequests.map(item => ({{
      body:item.body, aborted:item.signal.aborted
    }})),
    cancelRequests
  }}));
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
        self.assertFalse(result["nodes"][0]["running"])
        self.assertEqual(result["nodes"][0]["text"], "OK")
        self.assertEqual(result["saveCount"], 1)
        self.assertEqual(result["toasts"], [])
        self.assertEqual(result["activeCount"], 0)

    def test_timeout_clears_running_and_uses_localized_error(self):
        result = self.run_case("timeout")
        self.assertFalse(result["nodes"][0]["running"])
        self.assertEqual(result["nodes"][0]["text"], "draft-a")
        self.assertEqual(result["toasts"], ["smart.promptLlmTimeout"])

    def test_http_error_clears_running_and_surfaces_detail(self):
        result = self.run_case("http-error")
        self.assertFalse(result["nodes"][0]["running"])
        self.assertEqual(result["nodes"][0]["text"], "draft-a")
        self.assertIn("502", result["toasts"][0])

    def test_stop_aborts_fetch_notifies_backend_and_preserves_node_inputs(self):
        result = self.run_case("stop")
        self.assertFalse(result["nodes"][0]["running"])
        self.assertTrue(result["canvasRequests"][0]["aborted"])
        self.assertEqual(result["activeCount"], 0)
        self.assertEqual(result["toasts"], ["smart.promptLlmStopped"])
        self.assertEqual(
            result["canvasRequests"][0]["body"]["request_id"],
            result["cancelRequests"][0]["body"]["request_id"],
        )
        self.assertEqual(result["before"], {
            "input": result["nodes"][0]["input"],
            "llmInstruction": result["nodes"][0]["llmInstruction"],
            "llmSystemEnabled": result["nodes"][0]["llmSystemEnabled"],
            "llmSystemPrompt": result["nodes"][0]["llmSystemPrompt"],
            "llmProvider": result["nodes"][0]["llmProvider"],
            "llmModel": result["nodes"][0]["llmModel"],
            "upstream": result["nodes"][0]["upstream"],
            "connections": result["connections"],
        })

    def test_old_finally_cannot_clear_a_newer_run(self):
        result = self.run_case("stale-before-b")
        self.assertEqual(result["intermediate"], {"running": True, "text": "draft-a"})
        self.assertEqual(result["nodes"][0]["text"], "NEW")
        self.assertFalse(result["nodes"][0]["running"])
        self.assertEqual(result["saveCount"], 1)

    def test_old_result_cannot_overwrite_completed_newer_result(self):
        result = self.run_case("stale-after-b")
        self.assertEqual(result["nodes"][0]["text"], "NEW")
        self.assertFalse(result["nodes"][0]["running"])
        self.assertEqual(result["saveCount"], 1)
        ids = [item["body"]["request_id"] for item in result["canvasRequests"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_cancelling_one_node_does_not_affect_another(self):
        result = self.run_case("multi-node")
        self.assertEqual(result["nodes"][0]["text"], "draft-a")
        self.assertEqual(result["nodes"][1]["text"], "B-DONE")
        self.assertFalse(result["nodes"][0]["running"])
        self.assertFalse(result["nodes"][1]["running"])
        self.assertTrue(result["canvasRequests"][0]["aborted"])
        self.assertFalse(result["canvasRequests"][1]["aborted"])
        self.assertEqual(result["connections"], result["before"]["a"]["connections"])

    def test_run_button_toggles_to_enabled_stop_button(self):
        prompt_body = function_source("promptNodeBodyHtml")
        binder = function_source("bindPromptNodeControls")
        self.assertIn("escapeHtml(tr('common.run'))", prompt_body)
        self.assertIn("node.running ? 'is-stop' : ''", prompt_body)
        self.assertIn("node.running ? 'square' : 'play'", prompt_body)
        self.assertIn("node.running ? escapeHtml(tr('common.stop'))", prompt_body)
        self.assertNotRegex(prompt_body, r"prompt-node-run[^>]+disabled")
        self.assertIn("cancelPromptLLMNode(node.id)", binder)

    def test_pagehide_cancels_all_prompt_llm_runs(self):
        self.assertRegex(
            SMART,
            r"window\.addEventListener\('pagehide',[\s\S]+?activePromptLLMRuns\.keys\(\)[\s\S]+?cancelPromptLLMNode",
        )
        self.assertIn("keepalive:true", SMART)

    def test_stop_and_timeout_translation_keys_exist(self):
        self.assertIn('"common.stop": { zh: "停止", en: "Stop" }', COMMON_I18N)
        self.assertIn('"smart.promptLlmStopped": { zh: "LLM 已停止", en: "LLM stopped" }', SMART_I18N)
        self.assertIn('"smart.promptLlmTimeout": { zh: "LLM 请求已超时", en: "LLM request timed out" }', SMART_I18N)


if __name__ == "__main__":
    unittest.main()
