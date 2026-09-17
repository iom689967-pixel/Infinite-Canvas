"""Generation-only limits, independent of the 100k editor/storage limit.

Kie limits below were confirmed in the owner's 2026-09-15 requirements.
Only routes implemented by our adapter are listed. Unconfirmed routes stay unknown.
Personal frontends receive limits via resolved Provider capabilities.
"""
from copy import deepcopy


MODEL_PROMPT_CAPABILITIES = {
    "kie": {
        "routes": {
            "gpt-image-2": {"text": "gpt-image-2-text-to-image", "image": "gpt-image-2-image-to-image"},
            "nano-banana-pro": {"text": "nano-banana-pro", "image": "nano-banana-pro"},
        },
        "models": {
            "gpt-image-2-text-to-image": {"limit": 20_000, "label": "Kie GPT Image 2"},
            "gpt-image-2-image-to-image": {"limit": 20_000, "label": "Kie GPT Image 2"},
            "nano-banana-pro": {"limit": 20_000, "label": "Kie Nano Banana Pro"},
        },
    },
}


def public_prompt_capabilities():
    return deepcopy(MODEL_PROMPT_CAPABILITIES)


def resolve_prompt_capability(provider, model, reference_count=0):
    config = MODEL_PROMPT_CAPABILITIES.get(provider, {})
    upstream = config.get("routes", {}).get(model, {}).get("image" if reference_count else "text", model)
    capability = config.get("models", {}).get(upstream)
    return ({**capability, "model": upstream} if capability else None)


class ModelPromptLimitError(ValueError):
    def __init__(self, provider, model, prompt, reference_count=0, *, upstream=False, preserve_prompt=False):
        capability = resolve_prompt_capability(provider, model, reference_count)
        current = len(str(prompt or "") if preserve_prompt else str(prompt or "").strip())  # exactly Kie input.prompt
        limit = capability["limit"] if capability else None
        label = capability["label"] if capability else model
        if not upstream or (limit is not None and current > limit):
            message = (f"当前模型 {label} 最多支持 {limit:,} 字符。"
                       f"当前 Prompt 为 {current:,} 字符，超出 {current - limit:,} 字符，请缩短后重试。")
        else:
            message = "当前模型拒绝了 Prompt：文本长度超过模型限制。"
            if limit is not None:
                message += f"当前配置上限为 {limit:,} 字符，当前 Prompt 为 {current:,} 字符；上游规则可能已变化，请缩短后重试。"
        self.details = {
            "code": "model_prompt_too_long", "message": message,
            "provider": provider, "model": capability["model"] if capability else model,
            "current_length": current, "limit": limit,
            "exceeded": max(0, current - limit) if limit is not None else None,
        }
        super().__init__(message)


def validate_model_prompt(provider, model, prompt, reference_count=0, *, preserve_prompt=False):
    capability = resolve_prompt_capability(provider, model, reference_count)
    if capability and len(str(prompt or "") if preserve_prompt else str(prompt or "").strip()) > capability["limit"]:
        raise ModelPromptLimitError(provider, model, prompt, reference_count, preserve_prompt=preserve_prompt)


def is_upstream_prompt_limit_error(message):
    return "the text length cannot exceed the maximum limit" in str(message or "").lower()
