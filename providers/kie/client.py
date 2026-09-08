"""Minimal server-side client for Kie Market image jobs."""

import json
import urllib.parse

import httpx

from .models import KIE_BASE_URL


class KieAPIError(RuntimeError):
    def __init__(self, message, *, status_code=502, code=None, task_id="", raw=None):
        super().__init__(str(message or "Kie API 请求失败"))
        self.status_code = int(status_code or 502)
        self.code = code
        self.task_id = str(task_id or "")
        self.raw = raw if isinstance(raw, dict) else {}


def _response_message(payload, fallback="Kie API 请求失败"):
    if not isinstance(payload, dict):
        return fallback
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(
        data.get("failMsg")
        or payload.get("msg")
        or payload.get("message")
        or fallback
    )


class KieClient:
    def __init__(self, api_key, *, base_url=KIE_BASE_URL, http_client=None):
        api_key = str(api_key or "").strip()
        if not api_key:
            raise KieAPIError("未配置 KIE_API_KEY，请在 API/.env 中填写", status_code=400)
        self.api_key = api_key
        self.base_url = str(base_url or KIE_BASE_URL).strip().rstrip("/")
        self._http_client = http_client
        self.quiet = bool(getattr(http_client, 'quiet', False))
        self.last_http_status = None
        self.last_request_url = ""

    def headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request_json(self, method, path, **kwargs):
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=20.0, read=90.0, write=120.0, pool=20.0),
            follow_redirects=True,
        )
        request_url = f"{self.base_url}{path}"
        self.last_request_url = request_url
        try:
            response = await client.request(method, request_url, headers=self.headers(), **kwargs)
            self.last_http_status = response.status_code
        except httpx.HTTPError as exc:
            raise KieAPIError(f"Kie 网络请求失败：{exc}", status_code=502,
                              code='timeout' if isinstance(exc, httpx.TimeoutException) else 'network') from exc
        finally:
            if owns_client:
                await client.aclose()
        try:
            payload = response.json()
        except ValueError as exc:
            raise KieAPIError(
                f"Kie 返回了无法解析的响应（HTTP {response.status_code}）",
                status_code=502,
            ) from exc
        code = payload.get("code") if isinstance(payload, dict) else None
        if response.status_code >= 400 or code not in (None, 200, "200"):
            status = response.status_code if response.status_code >= 400 else 502
            raise KieAPIError(
                _response_message(payload, f"Kie API 错误（HTTP {response.status_code}）"),
                status_code=status,
                code=code,
                raw=payload,
            )
        return payload

    async def create_task(self, payload):
        raw = await self._request_json("POST", "/api/v1/jobs/createTask", json=payload)
        data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else {}
        task_id = str(data.get("taskId") or "").strip()
        if not task_id:
            raise KieAPIError("Kie 创建任务成功但没有返回 data.taskId", raw=raw)
        if not self.quiet:
            print(json.dumps({
                "event": "kie_create_task",
                "httpStatus": self.last_http_status,
                "requestUrl": self.last_request_url,
                "taskId": task_id,
            }, ensure_ascii=False), flush=True)
        return task_id, raw

    async def query_task(self, task_id):
        task_id = str(task_id or "").strip()
        if not task_id:
            raise KieAPIError("Kie taskId 不能为空", status_code=400)
        query = urllib.parse.urlencode({"taskId": task_id})
        raw = await self._request_json("GET", f"/api/v1/jobs/recordInfo?{query}")
        if not self.quiet:
            print(json.dumps({
                "event": "kie_query_task",
                "httpStatus": self.last_http_status,
                "requestUrl": self.last_request_url,
                "taskId": task_id,
            }, ensure_ascii=False), flush=True)
        return raw
