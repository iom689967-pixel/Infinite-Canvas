"""422 校验错误：不返回 500、不回显请求内容；前端只把 detail 写进提示和日志。"""
import json
import re
import subprocess
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import main

ROOT = Path(__file__).resolve().parents[1]
JSON_HEADERS = {"Content-Type": "application/json"}


class SanitizeValidationErrorsTests(unittest.TestCase):
    def test_strings_bytes_and_objects_are_omitted_scalars_kept(self):
        errors = main.sanitize_validation_errors([
            {"type": "string_too_long", "loc": ("body", "prompt"), "msg": "too long", "input": "secret" * 10, "ctx": {"max_length": 5}},
            {"type": "model_attributes_type", "loc": ("body",), "msg": "bad", "input": b'{"api_key":"sk-x"}'},
            {"type": "list_type", "loc": ("body", "refs"), "msg": "bad", "input": {"a": 1}},
            {"type": "string_type", "loc": ("body", "prompt"), "msg": "bad", "input": 123},
            {"type": "missing", "loc": ("body", "model"), "msg": "Field required", "input": None},
        ])
        dumped = json.dumps(errors)
        self.assertNotIn("secret", dumped)
        self.assertNotIn("sk-x", dumped)
        self.assertEqual(errors[0]["input_omitted"], {"type": "str", "length": 60})
        self.assertEqual(errors[1]["input_omitted"], {"type": "bytes", "length": 18})
        self.assertEqual(errors[2]["input_omitted"], {"type": "dict", "length": 1})
        self.assertEqual(errors[3]["input"], 123)
        self.assertIsNone(errors[4]["input"])
        self.assertEqual(errors[0]["loc"], ["body", "prompt"])

    def test_exception_objects_in_ctx_become_strings(self):
        errors = main.sanitize_validation_errors([
            {"type": "value_error", "loc": ("body", "messages"), "msg": "Value error, too long", "ctx": {"error": ValueError("too long")}},
        ])
        self.assertEqual(errors[0]["ctx"], {"error": "too long"})
        json.dumps(errors)  # 不再抛出 TypeError

    def test_friendly_messages(self):
        self.assertIn("不是有效的 JSON（第 9 个字符附近）", main.friendly_validation_error([{"type": "json_invalid", "loc": ["body", 9], "msg": "JSON decode error"}]))
        self.assertIn("请求体必须是 JSON 对象", main.friendly_validation_error([{"type": "model_attributes_type", "loc": ["body"], "msg": "x"}]))
        self.assertIn("格式不正确：最多 3 个", main.friendly_validation_error([{"type": "value_error", "loc": ["body", "messages"], "msg": "Value error, 最多 3 个", "ctx": {"error": "最多 3 个"}}]))


class ValidationResponseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app, raise_server_exceptions=False)

    def post(self, path, **kwargs):
        response = self.client.post(path, **kwargs)
        self.assertEqual(response.status_code, 422, response.text[:300])
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        return response

    def test_non_json_and_wrong_content_type_return_422_not_500(self):
        for kwargs in (
            {"content": b"hello", "headers": JSON_HEADERS},
            {"content": b'{"title":', "headers": JSON_HEADERS},
            {"content": b'{"title":"x"}', "headers": {"Content-Type": "text/plain"}},
            {"content": b'{"title":"x"}'},
            {"json": [1, 2]},
        ):
            with self.subTest(kwargs=kwargs):
                detail = self.post("/api/canvases", **kwargs).json()["detail"]
                self.assertTrue(detail.startswith("请求体"), detail)

    def test_custom_validator_error_returns_422_not_500(self):
        content = "S" * (main.MAX_LLM_TEXT_CHARS + 1)
        response = self.post("/api/canvas-llm", json={"provider_id": "kie", "model": "m", "messages": [{"role": "user", "content": content}]})
        self.assertNotIn("SSSSSSSSSS", response.text)
        self.assertIn(str(main.MAX_LLM_TEXT_CHARS), response.json()["detail"])

    def test_long_prompt_is_not_echoed(self):
        prompt = "SECRET-PROMPT-" + "x" * (main.ONLINE_IMAGE_PROMPT_MAX_LENGTH + 10)
        response = self.post("/api/canvas-image-tasks", json={"provider_id": "kie", "model": "gpt-image-2", "prompt": prompt})
        self.assertNotIn("SECRET-PROMPT", response.text)
        self.assertLess(len(response.text), 2000)
        error = response.json()["errors"][0]
        self.assertEqual(error["input_omitted"], {"type": "str", "length": len(prompt)})
        self.assertIn("提示词过长", response.json()["detail"])


def run_node(script):
    return json.loads(subprocess.check_output(["node", "-e", script], text=True, cwd=ROOT))


class ApiErrorsFrontendTests(unittest.TestCase):
    def test_api_errors_module(self):
        result = run_node(r"""
const E = require('./static/js/api-errors.js');
const long = 'x'.repeat(5000);
const out = {
  detailFromJson: E.fromText(JSON.stringify({detail:'提示词过长', errors:[{input_omitted:{type:'str',length:9}}]})),
  conflict: E.fromText(JSON.stringify({detail:{message:'画布已被其他页面修改', canvas:{nodes:[]}}})),
  listDetail: E.fromText(JSON.stringify({detail:[{loc:['body','prompt'], msg:'too long'}]})),
  plain: E.fromText('upstream timeout'),
  html: E.fromText('<!DOCTYPE html><html><body>502</body></html>', '请求失败'),
  longPlain: E.fromText(long).length,
  empty: E.fromText('', '兜底'),
  logFromError: E.forLog(new Error(JSON.stringify({detail:'Kie 任务失败', errors:[1,2,3]}))),
  logFromString: E.forLog('普通错误'),
  logEmpty: E.forLog(''),
  logLong: E.forLog(long).length,
  max: E.LOG_ERROR_MAX_CHARS,
};
(async () => {
  out.fromResponse = await E.fromResponse({text: async () => JSON.stringify({detail:'只要这一句', errors:[{input_omitted:{}}]})});
  out.fromBrokenResponse = await E.fromResponse({text: async () => { throw new Error('closed'); }}, '兜底');
  process.stdout.write(JSON.stringify(out));
})();
""")
        self.assertEqual(result["detailFromJson"], "提示词过长")
        self.assertEqual(result["conflict"], "画布已被其他页面修改")
        self.assertEqual(result["listDetail"], "prompt: too long")
        self.assertEqual(result["plain"], "upstream timeout")
        self.assertEqual(result["html"], "请求失败")
        self.assertLess(result["longPlain"], 600)
        self.assertEqual(result["empty"], "兜底")
        self.assertEqual(result["logFromError"], "Kie 任务失败")
        self.assertEqual(result["logFromString"], "普通错误")
        self.assertEqual(result["logEmpty"], "")
        self.assertLess(result["logLong"], result["max"] + 100)
        self.assertEqual(result["fromResponse"], "只要这一句")
        self.assertEqual(result["fromBrokenResponse"], "兜底")

    def test_canvases_no_longer_turn_raw_response_text_into_errors(self):
        for name in ("canvas.js", "smart-canvas.js"):
            source = (ROOT / "static/js" / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertIsNone(re.search(r"Error\(\(?await \w+\.text\(\)", source))
                self.assertNotIn("({detail:await r.text()", source)
                self.assertIn("error:ApiErrors.forLog(error)", source)
                self.assertNotIn("error:error ? String(error) : ''", source)

    def test_canvas_pages_load_api_errors_before_page_script(self):
        for page, script in (("canvas.html", "canvas.js"), ("smart-canvas.html", "smart-canvas.js")):
            html = (ROOT / "static" / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertLess(html.index("/static/js/api-errors.js"), html.index(f"/static/js/{script}?"))


class InstanceStaticAllowlistTests(unittest.TestCase):
    """独立实例只放行白名单里的静态文件：白名单页面引用的本地脚本和样式也必须在白名单里。"""

    def test_allowlisted_pages_reference_only_allowlisted_assets(self):
        from instance_access import WORKBENCH_STATIC
        missing = {}
        for page in sorted(p for p in WORKBENCH_STATIC if p.endswith(".html")):
            html = (ROOT / page.lstrip("/")).read_text(encoding="utf-8")
            refs = set(re.findall(r"""(?:src|href)=["'](/static/[^"'?#]+\.(?:js|css))""", html))
            absent = sorted(ref for ref in refs if ref not in WORKBENCH_STATIC)
            if absent:
                missing[page] = absent
        self.assertEqual(missing, {})


if __name__ == "__main__":
    unittest.main()
