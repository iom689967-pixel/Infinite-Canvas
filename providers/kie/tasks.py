"""Shared polling and result parsing for Kie image models."""

import asyncio
import json
import time

from .client import KieAPIError


KIE_WAITING_STATUSES = {"waiting", "queuing"}
KIE_GENERATING_STATUSES = {"generating"}
KIE_SUCCESS_STATUS = "success"
KIE_FAILED_STATUS = "fail"


class KieTaskError(RuntimeError):
    def __init__(self, message, *, task_id="", fail_code=None, raw=None):
        super().__init__(str(message or "Kie 任务失败"))
        self.task_id = str(task_id or "")
        self.fail_code = fail_code
        self.raw = raw if isinstance(raw, dict) else {}


class KieTaskCancelled(KieTaskError):
    pass


def task_data(payload):
    return payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else {}


def task_status(payload):
    return str(task_data(payload).get("state") or task_data(payload).get("status") or "").strip().lower()


def parse_result_urls(payload, task_id=""):
    data = task_data(payload)
    raw_result = data.get("resultJson")
    if isinstance(raw_result, str):
        try:
            result = json.loads(raw_result)
        except ValueError as exc:
            raise KieTaskError("Kie data.resultJson 不是合法 JSON", task_id=task_id, raw=payload) from exc
    elif isinstance(raw_result, dict):
        result = raw_result
    else:
        raise KieTaskError("Kie 任务成功但缺少 data.resultJson", task_id=task_id, raw=payload)
    urls = result.get("resultUrls") if isinstance(result, dict) else None
    if not isinstance(urls, list) or not any(str(item or "").strip() for item in urls):
        raise KieTaskError("Kie 任务成功但 resultJson.resultUrls 为空", task_id=task_id, raw=payload)
    return [str(item).strip() for item in urls if str(item or "").strip()]


def task_failure(payload, task_id=""):
    data = task_data(payload)
    code = data.get("failCode")
    message = data.get("failMsg") or payload.get("msg") or "Kie 任务失败"
    return KieTaskError(message, task_id=task_id, fail_code=code, raw=payload)


async def poll_task(
    client,
    task_id,
    *,
    timeout_seconds=900,
    initial_interval=2.5,
    max_interval=12.0,
    backoff=1.35,
    cancel_event=None,
    on_status=None,
):
    deadline = time.monotonic() + max(1.0, float(timeout_seconds or 900))
    interval = max(0.05, float(initial_interval or 2.5))
    last_payload = {}
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            raise KieTaskCancelled("Kie 任务轮询已取消", task_id=task_id, raw=last_payload)
        await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        if cancel_event is not None and cancel_event.is_set():
            raise KieTaskCancelled("Kie 任务轮询已取消", task_id=task_id, raw=last_payload)
        try:
            last_payload = await client.query_task(task_id)
        except KieAPIError:
            raise
        status = task_status(last_payload)
        if on_status is not None:
            value = on_status(status, last_payload)
            if hasattr(value, "__await__"):
                await value
        if status == KIE_SUCCESS_STATUS:
            return {
                "taskId": task_id,
                "status": status,
                "resultUrls": parse_result_urls(last_payload, task_id),
                "raw": last_payload,
            }
        if status == KIE_FAILED_STATUS:
            raise task_failure(last_payload, task_id)
        if status not in KIE_WAITING_STATUSES | KIE_GENERATING_STATUSES:
            message = task_data(last_payload).get("failMsg") or last_payload.get("msg") or f"未知任务状态：{status or '(empty)'}"
            raise KieTaskError(message, task_id=task_id, raw=last_payload)
        interval = min(float(max_interval or 12.0), interval * max(1.0, float(backoff or 1.35)))
    raise TimeoutError(f"Kie 任务超过 {int(timeout_seconds)} 秒仍未完成，taskId={task_id}")
