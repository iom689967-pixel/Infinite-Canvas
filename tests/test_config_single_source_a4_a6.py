"""A4 + A6：画布只从 api_providers 取平台和模型；配置变更只走一个通知通道，loadConfig 单飞并带版本号。"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main

ROOT = Path(__file__).resolve().parents[1]
CLASSIC = (ROOT / "static/js/canvas.js").read_text(encoding="utf-8")
SMART = (ROOT / "static/js/smart-canvas.js").read_text(encoding="utf-8")


def function_body(source, name):
    match = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", source)
    assert match, name
    depth = 0
    for index in range(source.index("{", match.end()), len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[match.start():index + 1]
    raise AssertionError(name)


class ConfigEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, base_url="http://127.0.0.1")
        self.providers = [{"id": "p1", "name": "P1", "enabled": True, "image_models": ["m1"], "chat_models": []}]
        patcher = patch.object(main, "public_api_providers", side_effect=lambda: json.loads(json.dumps(self.providers)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self):
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_config_version_is_stable_and_tracks_provider_changes(self):
        first, second = self.config(), self.config()
        self.assertRegex(first["config_version"], r"^[0-9a-f]{16}$")
        self.assertEqual(first["config_version"], second["config_version"])
        self.providers[0]["image_models"].append("m2")
        self.assertNotEqual(self.config()["config_version"], first["config_version"])
        self.providers.pop()
        self.assertNotEqual(self.config()["config_version"], first["config_version"])

    def test_legacy_base_url_is_gone(self):
        self.assertNotIn("base_url", self.config())

    def test_no_global_model_lists_remain(self):
        # 第六批：所有页面都只用 api_providers，全局模型列表字段全部删除（/api/models 不变）
        # 第七批：没有页面读取的 has_api_key、has_ms_key 一并删除
        self.assertEqual(set(self.config()), {"config_version", "comfy_instances", "api_providers"})


class StandalonePagesUseOnlyProvidersTests(unittest.TestCase):
    LEGACY = re.compile(r"\b(?:cfg|config)\.(?:image_models?|chat_models?|video_models|ms_chat_models|base_url)\b")

    def test_online_and_chat_pages_do_not_read_global_lists(self):
        for name in ("online.html", "gpt-chat.html"):
            source = (ROOT / "static" / name).read_text(encoding="utf-8")
            self.assertEqual(self.LEGACY.findall(source), [], name)

    def test_no_phantom_fallback_models(self):
        online = (ROOT / "static/online.html").read_text(encoding="utf-8")
        chat = (ROOT / "static/gpt-chat.html").read_text(encoding="utf-8")
        self.assertNotIn("models.gpt", online)
        self.assertNotIn("[models.gpt]", online)
        self.assertNotIn("name:'ModelScope', chat_models:config.ms_chat_models", chat)
        self.assertNotIn("chat_model: 'gpt-5.5'", chat)

    def test_version_depends_only_on_content(self):
        a = main.canvas_config_version([{"id": "x", "image_models": ["a"]}], ["http://h:1"])
        b = main.canvas_config_version([{"image_models": ["a"], "id": "x"}], ["http://h:1"])
        self.assertEqual(a, b)
        self.assertNotEqual(a, main.canvas_config_version([{"id": "x", "image_models": ["a"]}], []))

    def test_instance_mode_config_gets_the_same_version_algorithm(self):
        source = (ROOT / "instance_http.py").read_text(encoding="utf-8")
        self.assertIn('if path == "/api/config":', source)
        self.assertIn('"config_version"', source)
        self.assertNotIn("from main import", source)  # main 以 __main__ 运行时再导入会重复启动整个应用


class ClassicCanvasSingleSourceTests(unittest.TestCase):
    def test_no_legacy_global_model_lists(self):
        for name in ("imageModels", "chatModels", "videoModels", "msChatModels", "DEFAULT_VIDEO_MODELS",
                     "localImageModels", "localChatModels", "hasManagedChatModels", "hasManagedImageModels",
                     "loadLocalModelLists"):
            self.assertIsNone(re.search(r"\b" + name + r"\b", CLASSIC), name)
        for key in ("canvas_chat_models_ordered", "canvas_image_models_ordered", "canvas_custom_image_models"):
            self.assertNotIn(key, CLASSIC)

    def test_load_config_reads_only_providers_and_comfy_instances(self):
        body = function_body(CLASSIC, "loadConfigOnce")
        used = set(re.findall(r"cfg\.([a-z_]+)", body))
        self.assertEqual(used, {"config_version", "comfy_instances", "api_providers"})

    def test_chat_models_come_only_from_providers(self):
        body = function_body(CLASSIC, "allChatModels")
        self.assertIn("chatApiProviders()", body)
        self.assertNotIn("local", body)
        self.assertNotIn("'gpt-4o-mini'", function_body(CLASSIC, "resolveChatModel"))

    def test_window_message_no_longer_reloads_config(self):
        start = CLASSIC.index("window.addEventListener('message', event => {")
        listener = CLASSIC[start:CLASSIC.index("});", start)]
        self.assertNotIn("providers-changed", listener)


class SmartCanvasSingleSourceTests(unittest.TestCase):
    def test_no_hardcoded_video_model_list(self):
        self.assertNotIn("DEFAULT_VIDEO_MODELS", SMART)
        self.assertIn("provider?.video_models || []", function_body(SMART, "volcengineVideoModels"))

    def test_window_message_no_longer_reloads_config(self):
        start = SMART.index("window.addEventListener('message', event => {")
        listener = SMART[start:SMART.index("});", start)]
        self.assertNotIn("providers-changed", listener)

    def test_refresh_skips_rerender_when_unchanged(self):
        self.assertIn("if(!lastConfigLoadChanged) return;", function_body(SMART, "refreshSmartConfigFromSettings"))


class SingleNotificationChannelTests(unittest.TestCase):
    def test_settings_pages_only_use_broadcast_channel(self):
        body = function_body((ROOT / "static/js/api-settings.js").read_text(encoding="utf-8"), "broadcastStudioApiChange")
        self.assertIn("BroadcastChannel('studio-api')", body)
        self.assertNotIn("postMessage(message, '*')", body)
        comfy = (ROOT / "static/js/comfyui-settings.js").read_text(encoding="utf-8")
        self.assertNotIn("postMessage({ type: 'comfy-instances-changed' }, '*')", comfy)

    def test_shell_no_longer_forwards(self):
        shell = (ROOT / "static/index.html").read_text(encoding="utf-8")
        self.assertNotIn("forwardStudioApiChange", shell)


@unittest.skipUnless(shutil.which("node"), "node is required")
class ClassicLoadConfigSingleFlightTests(unittest.TestCase):
    """在 node 里实际运行 canvas.js 的 loadConfig 代码段，替换掉网络请求。"""

    def run_js(self, scenario):
        start = CLASSIC.index("let configLoadInFlight = null;")
        end = CLASSIC.index("// A6：配置变更只走一个通知通道")
        harness = """
let apiProviders = [], apiConfigLoaded = false, comfyWorkflowsLoaded = false, comfyBackendCount = 1;
let comfyWorkflows = [], runningHubWorkflowCache = {}, models = {gpt:'', nano:''};
const calls = []; let version = 'v1'; let pendingResolvers = [];
function tr(k){ return k; } function setStatus(){}
function uniqueModels(list){ return [...new Set(list)]; }
function imageApiProviders(){ return apiProviders; }
async function ensureRunningHubWorkflow(){}
function fetchJsonOrThrow(url){
  calls.push(url);
  if(url === '/api/workflows') return Promise.resolve({workflows: []});
  return new Promise(resolve => pendingResolvers.push(() => resolve({config_version: version, api_providers: [{id:'p', image_models:['nano-x','gpt-x']}]})));
}
const flush = async () => { while(pendingResolvers.length){ pendingResolvers.shift()(); await new Promise(r => setTimeout(r, 0)); } await new Promise(r => setTimeout(r, 0)); };
""" + CLASSIC[start:end] + scenario
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_concurrent_calls_share_one_request_and_one_rerun(self):
        out = self.run_js("""
(async () => {
  const results = [loadConfig(), loadConfig(), loadConfig(), loadConfig(), loadConfig()];
  for(let i = 0; i < 5; i++) await flush();
  const values = await Promise.all(results);
  console.log(JSON.stringify({values, configCalls: calls.filter(u => u === '/api/config').length, changed: lastConfigLoadChanged, aliases: models}));
})();
""")
        self.assertEqual(out["values"], [True] * 5)
        self.assertEqual(out["configCalls"], 2)  # 一个进行中 + 结束后补跑一次
        self.assertEqual(out["aliases"], {"gpt": "gpt-x", "nano": "nano-x"})

    def test_unchanged_version_reports_no_change(self):
        out = self.run_js("""
(async () => {
  let p = loadConfig(); await flush(); await p; const first = lastConfigLoadChanged;
  p = loadConfig(); await flush(); await p; const second = lastConfigLoadChanged;
  version = 'v2';
  p = loadConfig(); await flush(); await p; const third = lastConfigLoadChanged;
  console.log(JSON.stringify({first, second, third}));
})();
""")
        self.assertEqual(out, {"first": True, "second": False, "third": True})


if __name__ == "__main__":
    unittest.main()
