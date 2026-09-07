"""Shared polling and result parsing for Kie image models."""

import asyncio
import json
import time

from .client import KieAPIError


KIE_WAITING_STATUSES = {"waiting", "queuing"}
KIE_GENERATING_STATUSES = {"generating"}
KIE_SUCCESS_STATUS = "success"
KIE_FAILED_STATUS = "fail"
KIE_POLL_FAST_WINDOW_SECONDS = 60.0
KIE_POLL_MEDIUM_WINDOW_SECONDS = 180.0
KIE_POLL_MEDIUM_INTERVAL_SECONDS = 4.0
KIE_POLL_SLOW_INTERVAL_SECONDS = 6.0


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


def _poll_interval_for_elapsed(
    elapsed_seconds,
    *,
    initial_interval=2.5,
    max_interval=12.0,
):
    """Return the bounded three-stage polling interval for elapsed task time."""
    elapsed = max(0.0, float(elapsed_seconds or 0.0))
    initial = max(0.05, float(initial_interval or 2.5))
    cap = max(0.05, float(max_interval or 12.0))
    if elapsed < KIE_POLL_FAST_WINDOW_SECONDS:
        desired = initial
    elif elapsed < KIE_POLL_MEDIUM_WINDOW_SECONDS:
        desired = max(initial, KIE_POLL_MEDIUM_INTERVAL_SECONDS)
    else:
        desired = max(initial, KIE_POLL_SLOW_INTERVAL_SECONDS)
    return min(cap, desired)


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
    started_at = time.monotonic()
    deadline = started_at + max(1.0, float(timeout_seconds or 900))
    interval = _poll_interval_for_elapsed(
        0.0,
        initial_interval=initial_interval,
        max_interval=max_interval,
    )
    # Retain the legacy keyword for caller compatibility; cadence is now time-phased.
    _ = backoff
    last_payload = {}
    first_query = True
    last_logged_status = ""
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
        if first_query or status != last_logged_status:
            print(json.dumps({
                "event": "kie_task_state",
                "taskId": task_id,
                "state": status,
                "firstQuery": first_query,
            }, ensure_ascii=False), flush=True)
            first_query = False
            last_logged_status = status
        if on_status is not None:
            value = on_status(status, last_payload)
            if hasattr(value, "__await__"):
                await value
        if status == KIE_SUCCESS_STATUS:
            result_urls = parse_result_urls(last_payload, task_id)
            print(json.dumps({
                "event": "kie_task_result",
                "taskId": task_id,
                "finalState": status,
                "resultUrlsCount": len(result_urls),
            }, ensure_ascii=False), flush=True)
            return {
                "taskId": task_id,
                "status": status,
                "resultUrls": result_urls,
                "raw": last_payload,
            }
        if status == KIE_FAILED_STATUS:
            raise task_failure(last_payload, task_id)
        if status not in KIE_WAITING_STATUSES | KIE_GENERATING_STATUSES:
            message = task_data(last_payload).get("failMsg") or last_payload.get("msg") or f"未知任务状态：{status or '(empty)'}"
            raise KieTaskError(message, task_id=task_id, raw=last_payload)
        interval = _poll_interval_for_elapsed(
            time.monotonic() - started_at,
            initial_interval=initial_interval,
            max_interval=max_interval,
        )
    raise TimeoutError(f"Kie 任务超过 {int(timeout_seconds)} 秒仍未完成，taskId={task_id}")
