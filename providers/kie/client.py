"""Minimal server-side client for Kie Market image jobs."""

import time
import urllib.parse

import httpx

from .models import KIE_BASE_URL
from .safe_log import log_kie_event, new_trace_id


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
    def __init__(self, api_key, *, base_url=KIE_BASE_URL, http_client=None,
                 trace_id="", provider_id="kie", model=""):
        api_key = str(api_key or "").strip()
        if not api_key:
            raise KieAPIError("未配置 KIE_API_KEY，请在 API/.env 中填写", status_code=400)
        self.api_key = api_key
        self.base_url = str(base_url or KIE_BASE_URL).strip().rstrip("/")
        self._http_client = http_client
        self.quiet = bool(getattr(http_client, 'quiet', False))
        self.last_http_status = None
        self.last_request_url = ""
        self.trace_id = str(trace_id or new_trace_id())
        self.provider_id = str(provider_id or "kie")
        self.model = str(model or "")

    def log_context(self):
        return {
            "trace_id": self.trace_id,
            "provider": self.provider_id,
            "model": self.model,
        }

    def headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request_json(self, method, path, *, log_stage="request", **kwargs):
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=20.0, read=90.0, write=120.0, pool=20.0),
            follow_redirects=True,
        )
        request_url = f"{self.base_url}{path}"
        self.last_request_url = request_url
        started_at = time.perf_counter()
        try:
            response = await client.request(method, request_url, headers=self.headers(), **kwargs)
            self.last_http_status = response.status_code
        except httpx.HTTPError as exc:
            category = "timeout" if isinstance(exc, httpx.TimeoutException) else "network"
            log_kie_event(
                "kie_request_failed", **self.log_context(), stage=log_stage,
                error_category=category,
                elapsed_ms=(time.perf_counter() - started_at) * 1000,
            )
            raise KieAPIError("Kie 上游等待超时" if category == "timeout" else "Kie 上游网络请求失败",
                              status_code=502, code=category) from exc
        finally:
            if owns_client:
                await client.aclose()
        try:
            payload = response.json()
        except ValueError as exc:
            log_kie_event(
                "kie_request_failed", **self.log_context(), stage=log_stage,
                http_status=response.status_code, error_category="invalid_response",
                elapsed_ms=(time.perf_counter() - started_at) * 1000,
            )
            raise KieAPIError(
                f"Kie 返回了无法解析的响应（HTTP {response.status_code}）",
                status_code=502,
            ) from exc
        code = payload.get("code") if isinstance(payload, dict) else None
        if response.status_code >= 400 or code not in (None, 200, "200"):
            status = response.status_code if response.status_code >= 400 else 502
            log_kie_event(
                "kie_request_failed", **self.log_context(), stage=log_stage,
                http_status=response.status_code, error_category="upstream",
                elapsed_ms=(time.perf_counter() - started_at) * 1000,
            )
            raise KieAPIError(
                _response_message(payload, f"Kie API 错误（HTTP {response.status_code}）"),
                status_code=status,
                code=code,
                raw=payload,
            )
        return payload

    async def create_task(self, payload):
        started_at = time.perf_counter()
        raw = await self._request_json("POST", "/api/v1/jobs/createTask", json=payload, log_stage="submit")
        data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else {}
        task_id = str(data.get("taskId") or "").strip()
        if not task_id:
            log_kie_event(
                "kie_request_failed", **self.log_context(), stage="submit",
                http_status=self.last_http_status, error_category="invalid_response",
                elapsed_ms=(time.perf_counter() - started_at) * 1000,
            )
            raise KieAPIError("Kie 创建任务成功但没有返回 data.taskId", raw=raw)
        if not self.quiet:
            log_kie_event(
                "kie_create_task", **self.log_context(), stage="submitted",
                http_status=self.last_http_status,
                elapsed_ms=(time.perf_counter() - started_at) * 1000,
            )
        return task_id, raw

    async def query_task(self, task_id):
        task_id = str(task_id or "").strip()
        if not task_id:
            raise KieAPIError("Kie taskId 不能为空", status_code=400)
        query = urllib.parse.urlencode({"taskId": task_id})
        started_at = time.perf_counter()
        raw = await self._request_json("GET", f"/api/v1/jobs/recordInfo?{query}", log_stage="query")
        if not self.quiet:
            log_kie_event(
                "kie_query_task", **self.log_context(), stage="query",
                http_status=self.last_http_status,
                elapsed_ms=(time.perf_counter() - started_at) * 1000,
            )
        return raw
