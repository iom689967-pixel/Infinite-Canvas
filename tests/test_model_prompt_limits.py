import asyncio
import contextlib
import io
import json
import socket
import subprocess
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

import main
from providers.kie.client import KieAPIError
from providers.kie.models import build_create_payload
from providers.prompt_limits import (
    ModelPromptLimitError, public_prompt_capabilities, validate_model_prompt,
)

ROOT = Path(__file__).resolve().parents[1]
SHARED = (ROOT / "static/js/model-prompt-limits.js").read_text()
CASES = [(char, length) for char in ("x", "中", "😀")
         for length in (19_999, 20_000, 20_001, 20_083)]


class ModelPromptLimitsTests(unittest.TestCase):
    def test_both_canvas_submission_functions_use_final_composed_text(self):
        import re
        def function(source, name):
            # These top-level functions end before the next top-level declaration.
            start = re.search(r"(?:async )?function " + name + r"\(", source).start()
            tail = source[start:]
            end = re.search(r"\n(?:async )?function ", tail)
            return tail[:end.start()] if end else tail
        smart = (ROOT / "static/js/smart-canvas.js").read_text()
        normal = (ROOT / "static/js/canvas.js").read_text()
        mapping = json.dumps(public_prompt_capabilities())
        script = SHARED + "\nconst mapping=" + mapping + ";\n" + r'''
let providerRequests=0;
async function fetch(url){
    if(url==='/api/model-prompt-limits') return {ok:true,json:async()=>mapping};
    providerRequests++; throw new Error('Unexpected submission');
}
const settings={provider_id:'kie',model:'gpt-image-2'};
function imageRefsOnly(refs){return refs;}
async function cascadeFetch(){providerRequests++;throw new Error('Unexpected submission');}
'''
        script += function(smart, "runApiGeneration") + "\n" + function(normal, "createCanvasImageTask")
        script += r'''
(async()=>{
    // Each section fits, but Global + Shot + Edit exceeds the selected model limit.
    const prompt=['x'.repeat(10000),'中'.repeat(10000),'z'.repeat(79)].join('\n\n');
    const errors=[];
    for(const refs of [[],Array.from({length:5},()=>({url:'https://mock.invalid/ref'}))]){
        try{await runApiGeneration(prompt,refs,settings);}catch(e){errors.push(e.message);}
        try{await createCanvasImageTask({...settings,prompt,reference_images:refs});}catch(e){errors.push(e.message);}
    }
    console.log(JSON.stringify({providerRequests,errors}));
})();
'''
        result = json.loads(subprocess.check_output(["node", "-e", script], text=True))
        self.assertEqual(result["providerRequests"], 0)
        self.assertEqual(len(result["errors"]), 4)
        self.assertTrue(all("20,083" in error and "超出 83" in error for error in result["errors"]))

    def test_final_adapter_prompt_boundaries_and_routes(self):
        for model in ("gpt-image-2", "nano-banana-pro"):
            for count in (0, 5):
                refs = [f"https://mock.invalid/{i}" for i in range(count)]
                for char, length in CASES:
                    with self.subTest(model=model, refs=count, char=char, length=length):
                        text = " \n" + char * length + "\n "
                        if length > 20_000:
                            with self.assertRaises(ModelPromptLimitError) as caught:
                                build_create_payload(model, text, refs)
                            self.assertEqual(caught.exception.details["current_length"], length)
                        else:
                            payload, _ = build_create_payload(model, text, refs)
                            self.assertEqual(payload["input"]["prompt"], char * length)

    def test_unknown_models_and_other_providers_are_not_assigned_limits(self):
        for provider, model in (("other", "gpt-image-2"), ("kie", "video-unknown"), ("kie", "nano-banana")):
            validate_model_prompt(provider, model, "x" * 100_000)
        self.assertNotIn("nano-banana", public_prompt_capabilities()["kie"]["models"])

    def test_100k_editor_library_and_generic_schema_preserved(self):
        for length in (50_000, 100_000, 100_001):
            for model, fields in (
                (main.OnlineImageRequest, {"prompt": "x" * length}),
                (main.CanvasLLMRequest, {"message": "x" * length}),
                (main.CanvasVideoRequest, {"prompt": "x" * length}),
                (main.PromptLibraryItemRequest, {"positive": "x" * length, "negative": "x" * length}),
            ):
                with self.subTest(model=model.__name__, length=length):
                    if length <= 100_000:
                        model(**fields)
                    else:
                        with self.assertRaises(ValidationError):
                            model(**fields)

    def test_shared_frontend_unicode_counts_errors_and_safe_history(self):
        script = SHARED + "\nconst mapping = " + json.dumps(public_prompt_capabilities()) + ";\n"
        script += "const cases = " + json.dumps(CASES) + ";\n" + r'''
const results=[];
for(const model of ['gpt-image-2','nano-banana-pro']) for(const count of [0,5]) for(const [char,n] of cases){
    const payload={provider_id:'kie',model,prompt:' '+String.fromCharCode(10)+char.repeat(n)+String.fromCharCode(10)+' ',reference_images:Array.from({length:count},()=>({url:'https://mock.invalid/ref'}))};
    try {ModelPromptLimits.check(mapping,payload); results.push({n,allowed:true,count:ModelPromptLimits.length(payload.prompt)});}
    catch(e){
        const entry={prompt:char.repeat(n),promptHtml:'private',references:[{url:'private'}],settings:{private:'secret'}};
        ModelPromptLimits.sanitizeHistory(entry,e.message);
        results.push({n,allowed:false,count:e.modelPromptValidation.current_length,entry,message:e.message});
    }
}
console.log(JSON.stringify(results));
'''
        results = json.loads(subprocess.check_output(["node", "-e", script], text=True))
        for row in results:
            self.assertEqual(row["count"], row["n"])
            self.assertEqual(row["allowed"], row["n"] <= 20_000)
            if not row["allowed"]:
                self.assertEqual(row["entry"]["prompt"], "")
                self.assertEqual(row["entry"]["references"], [])
                self.assertEqual(row["entry"]["error"], "Prompt too long for selected model")
                self.assertIn(f"超出 {row['n'] - 20_000:,} 字符", row["message"])


class ModelPromptLimitsNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_prompt_never_prepares_references_or_creates_task(self):
        prepare = AsyncMock(return_value=([], []))
        create = AsyncMock(return_value=("mock-task", {}))
        client = type("MockClient", (), {"create_task": create})()
        with patch.object(socket.socket, "connect", side_effect=AssertionError("No network")), patch.object(
            main, "kie_public_reference_urls", prepare
        ), patch.object(main, "KieClient", return_value=client), patch.object(
            main, "provider_env_key_value", return_value="mock-only"
        ), patch.object(main, "poll_kie_task", new=AsyncMock(return_value={"resultUrls": ["https://mock.invalid/result"]})), contextlib.redirect_stdout(io.StringIO()):
            for model in ("gpt-image-2", "nano-banana-pro"):
                for count in (0, 5):
                    refs = [{"url": f"https://mock.invalid/{i}"} for i in range(count)]
                    prepare.return_value = ([r["url"] for r in refs], [])
                    for char, length in CASES:
                        with self.subTest(model=model, count=count, length=length, char=char):
                            prepare.reset_mock(); create.reset_mock()
                            if length > 20_000:
                                with self.assertRaises(ModelPromptLimitError):
                                    await main.generate_kie_provider_image(char * length, model, refs, {})
                                prepare.assert_not_called(); create.assert_not_called()
                            else:
                                await main.generate_kie_provider_image(char * length, model, refs, {})
                                prepare.assert_awaited_once(); create.assert_awaited_once()

    async def test_real_routes_block_before_local_task_or_provider_work(self):
        before = dict(main.CANVAS_TASKS)
        transport = httpx.ASGITransport(app=main.app)
        with patch.object(socket.socket, "connect", side_effect=AssertionError("No network")), patch.object(
            main, "kie_public_reference_urls", new=AsyncMock()
        ) as prepare, patch.object(main, "KieClient") as client:
            async with httpx.AsyncClient(transport=transport, base_url="http://owner.test") as http:
                for route in ("/api/online-image", "/api/canvas-image-tasks"):
                    for model in ("gpt-image-2", "nano-banana-pro"):
                        for count in (0, 5):
                            response = await http.post(route, json={"provider_id":"kie", "model":model,
                                "prompt":"中" * 20_083, "reference_images":[{"url":"https://mock.invalid/ref"}] * count})
                            self.assertEqual(response.status_code, 400)
                            self.assertEqual(response.json()["validation"]["current_length"], 20_083)
                            self.assertNotIn("中" * 100, response.text)
            prepare.assert_not_called(); client.assert_not_called()
        self.assertEqual(main.CANVAS_TASKS, before)

    async def test_known_upstream_length_error_survives_to_ui(self):
        client = type("MockClient", (), {"create_task": AsyncMock(side_effect=KieAPIError(
            "The text length cannot exceed the maximum limit", code=500))})()
        with patch.object(socket.socket, "connect", side_effect=AssertionError("No network")), patch.object(
            main, "kie_public_reference_urls", new=AsyncMock(return_value=([], []))
        ), patch.object(main, "provider_env_key_value", return_value="mock-only"), patch.object(
            main, "KieClient", return_value=client
        ), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ModelPromptLimitError) as caught:
                await main.generate_kie_provider_image("x" * 1000, "gpt-image-2", [], {})
        details = caught.exception.details
        script = SHARED + "\nconst e = ModelPromptLimits.fromData(" + json.dumps({"validation": details}) + ");console.log(JSON.stringify({message:e.message,validation:e.modelPromptValidation}));"
        output = json.loads(subprocess.check_output(["node", "-e", script], text=True))
        self.assertIn("文本长度超过模型限制", output["message"])
        self.assertNotIn("没有返回图片", output["message"])
        self.assertNotIn("HTTP", output["message"])

    async def test_unknown_fallback_does_not_invent_a_limit(self):
        error = ModelPromptLimitError("kie", "future-model", "x", upstream=True)
        self.assertIsNone(error.details["limit"])
        self.assertNotIn("20,000", str(error))


if __name__ == "__main__":
    unittest.main()
