"""Allowlisted structured logging for the Kie request lifecycle."""

import datetime
import json
import math
import re
import secrets


_EVENT_FIELDS = {
    "kie_reference_preflight": {
        "trace_id", "provider", "model", "stage", "reference_count",
        "valid_reference_count", "invalid_reference_count", "elapsed_ms",
    },
    "kie_reference_prepare_metrics": {
        "trace_id", "provider", "model", "stage", "reference_count",
        "cache_hits", "cache_misses", "source_cache_hits", "source_cache_misses",
        "normalize_skipped_count", "stale_normalize_avoided_count",
        "absolute_expired_count", "pre_submit_invalid_count",
        "selective_reupload_count", "valid_reference_reuse_count",
        "pre_submit_validation_ms", "elapsed_ms",
    },
    "kie_create_request": {
        "trace_id", "provider", "model", "stage", "reference_count",
        "image_count", "size", "resolution", "aspect_ratio", "output_format",
        "elapsed_ms",
    },
    "kie_create_task": {
        "trace_id", "provider", "model", "stage", "http_status", "elapsed_ms",
    },
    "kie_query_task": {
        "trace_id", "provider", "model", "stage", "http_status", "elapsed_ms",
    },
    "kie_task_state": {
        "trace_id", "provider", "model", "stage", "status", "first_query",
        "elapsed_ms",
    },
    "kie_task_result": {
        "trace_id", "provider", "model", "stage", "status", "result_count",
        "elapsed_ms",
    },
    "kie_request_failed": {
        "trace_id", "provider", "model", "stage", "http_status",
        "error_category", "elapsed_ms",
    },
}

_TOKEN_FIELDS = {
    "trace_id": 64,
    "provider": 64,
    "model": 128,
    "stage": 48,
    "status": 48,
    "error_category": 48,
    "size": 32,
    "resolution": 16,
    "aspect_ratio": 16,
    "output_format": 16,
}
_INTEGER_FIELDS = {
    "reference_count", "valid_reference_count", "invalid_reference_count",
    "cache_hits", "cache_misses", "source_cache_hits", "source_cache_misses",
    "normalize_skipped_count", "stale_normalize_avoided_count",
    "absolute_expired_count", "pre_submit_invalid_count",
    "selective_reupload_count", "valid_reference_reuse_count", "image_count",
    "http_status", "result_count",
}
_MILLISECOND_FIELDS = {"pre_submit_validation_ms", "elapsed_ms"}
_ENUM_FIELDS = {
    "stage": {
        "preflight", "prepare", "submit", "submitted", "query", "poll",
        "complete", "validate", "cancel", "submit_or_query", "unknown",
    },
    "status": {"waiting", "queuing", "generating", "success", "fail"},
    "error_category": {
        "validation", "canceled", "timeout", "network", "invalid_response",
        "upstream", "upstream_task", "upstream_api", "reference", "internal",
    },
}
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def new_trace_id():
    return f"kie_{secrets.token_hex(8)}"


def _safe_value(name, value):
    if name == "trace_id":
        text = str(value or "").strip()
        return text if re.fullmatch(r"kie_[0-9a-f]{16}", text) else None
    if name in _ENUM_FIELDS:
        text = str(value or "").strip().lower()
        return text if text in _ENUM_FIELDS[name] else None
    if name in _TOKEN_FIELDS:
        text = str(value or "").strip()
        validator = _SAFE_TOKEN
        if name == "provider":
            validator = _SAFE_IDENTIFIER
        elif name == "model":
            validator = _SAFE_MODEL
        if not text or len(text) > _TOKEN_FIELDS[name] or not validator.fullmatch(text):
            return None
        return text
    if name in _INTEGER_FIELDS:
        if isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if 0 <= number <= 1_000_000 else None
    if name in _MILLISECOND_FIELDS:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(number) or number < 0 or number > 86_400_000:
            return None
        return round(number, 3)
    if name == "first_query":
        return value if isinstance(value, bool) else None
    return None


def log_kie_event(event, **values):
    """Print only validated fields declared for a known event."""
    allowed = _EVENT_FIELDS.get(str(event or ""))
    if allowed is None:
        return None
    record = {
        "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "event": event,
    }
    for name in sorted(allowed):
        safe = _safe_value(name, values.get(name))
        if safe is not None:
            record[name] = safe
    print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)
    return record
