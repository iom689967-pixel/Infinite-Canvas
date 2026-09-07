"""Static Kie image-model whitelist and request adapters.

Only the two UI model ids below are accepted from the application. Kie's
internal Market model ids are selected here and are never accepted directly
from a browser request.
"""

from copy import deepcopy


KIE_BASE_URL = "https://api.kie.ai"

GPT_IMAGE_2 = "gpt-image-2"
NANO_BANANA_PRO = "nano-banana-pro"
KIE_UI_MODELS = (GPT_IMAGE_2, NANO_BANANA_PRO)
KIE_MODEL_NAMES = {
    GPT_IMAGE_2: "GPT Image 2",
    NANO_BANANA_PRO: "Nano Banana Pro",
}

GPT_IMAGE_2_RATIOS = (
    "auto", "1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16",
    "2:1", "1:2", "3:1", "1:3", "21:9", "9:21", "5:4", "4:5",
)
GPT_IMAGE_2_HIGH_RES_UNSUPPORTED_RATIOS = ("5:4", "4:5", "3:1", "1:3", "9:21")
NANO_BANANA_PRO_RATIOS = (
    "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16",
    "16:9", "21:9", "auto",
)
KIE_RESOLUTIONS = ("1K", "2K", "4K")

_CAPABILITIES = {
    GPT_IMAGE_2: {
        "ui_model": GPT_IMAGE_2,
        "label": KIE_MODEL_NAMES[GPT_IMAGE_2],
        "aspect_ratios": GPT_IMAGE_2_RATIOS,
        "resolutions": KIE_RESOLUTIONS,
        "output_formats": (),
        "default_aspect_ratio": "auto",
        "default_resolution": "1K",
        "reference_image_limit": 16,
        "reference_max_bytes": 30 * 1024 * 1024,
        "reference_formats": ("JPEG", "JPG", "PNG", "WEBP"),
        "resolution_ratio_exclusions": {
            "2K": GPT_IMAGE_2_HIGH_RES_UNSUPPORTED_RATIOS,
            "4K": GPT_IMAGE_2_HIGH_RES_UNSUPPORTED_RATIOS,
        },
    },
    NANO_BANANA_PRO: {
        "ui_model": NANO_BANANA_PRO,
        "label": KIE_MODEL_NAMES[NANO_BANANA_PRO],
        "aspect_ratios": NANO_BANANA_PRO_RATIOS,
        "resolutions": KIE_RESOLUTIONS,
        "output_formats": ("png", "jpg"),
        "default_aspect_ratio": "1:1",
        "default_resolution": "1K",
        "default_output_format": "png",
        "reference_image_limit": 8,
        "reference_max_bytes": 30 * 1024 * 1024,
        "reference_formats": ("JPEG", "PNG", "WEBP"),
        "resolution_ratio_exclusions": {},
    },
}


class KieValidationError(ValueError):
    pass


def is_allowed_ui_model(model):
    return str(model or "").strip() in KIE_UI_MODELS


def capability_for(model):
    model = str(model or "").strip()
    capability = _CAPABILITIES.get(model)
    if not capability:
        allowed = "、".join(KIE_MODEL_NAMES[item] for item in KIE_UI_MODELS)
        raise KieValidationError(f"Kie 只允许使用：{allowed}")
    return capability


def model_reference_limit(model):
    return int(capability_for(model)["reference_image_limit"])


def normalize_resolution(value, capability):
    resolution = str(value or capability["default_resolution"]).strip().upper()
    if resolution not in capability["resolutions"]:
        raise KieValidationError(
            f"{capability['label']} 不支持分辨率 {resolution or '(empty)'}；允许值："
            + "、".join(capability["resolutions"])
        )
    return resolution


def normalize_aspect_ratio(value, capability):
    ratio = str(value or capability["default_aspect_ratio"]).strip().lower()
    if ratio not in capability["aspect_ratios"]:
        raise KieValidationError(
            f"{capability['label']} 不支持比例 {ratio or '(empty)'}；允许值："
            + "、".join(capability["aspect_ratios"])
        )
    return ratio


def validate_resolution_ratio(capability, resolution, aspect_ratio):
    excluded = capability.get("resolution_ratio_exclusions", {}).get(resolution, ())
    if aspect_ratio in excluded:
        raise KieValidationError(
            f"{capability['label']} 的 {resolution} 不支持比例 {aspect_ratio}；请改用 1K 或其他比例"
        )


def normalize_output_format(value, capability):
    formats = capability.get("output_formats") or ()
    if not formats:
        return ""
    output_format = str(value or capability.get("default_output_format") or formats[0]).strip().lower()
    if output_format not in formats:
        raise KieValidationError(
            f"{capability['label']} 不支持输出格式 {output_format or '(empty)'}；允许值："
            + "、".join(formats)
        )
    return output_format


def build_routed_model_input(kie_model, prompt, references, *, aspect_ratio, resolution, output_format=""):
    """Map each Kie Market route to its own documented reference-image field.

    GPT Image 1.5 remains an internal adapter route and is not added to the
    browser/UI whitelist.
    """
    if kie_model in {"gpt-image-2-image-to-image", "gpt-image/1.5-image-to-image"}:
        return {
            "prompt": prompt,
            "input_urls": list(references),
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        }
    if kie_model == "gpt-image-2-text-to-image":
        return {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        }
    if kie_model == "nano-banana-pro":
        return {
            "prompt": prompt,
            "image_input": list(references),
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "output_format": output_format,
        }
    raise KieValidationError(f"未定义 Kie 模型路由字段：{kie_model}")


def build_create_payload(model, prompt, reference_urls=None, aspect_ratio="", resolution="", output_format=""):
    capability = capability_for(model)
    prompt = str(prompt or "").strip()
    if not prompt:
        raise KieValidationError("Kie 生图提示词不能为空")
    references = [str(url or "").strip() for url in (reference_urls or []) if str(url or "").strip()]
    limit = int(capability["reference_image_limit"])
    if len(references) > limit:
        raise KieValidationError(
            f"{capability['label']} 最多支持 {limit} 张参考图，当前收到 {len(references)} 张"
        )
    ratio = normalize_aspect_ratio(aspect_ratio, capability)
    resolution = normalize_resolution(resolution, capability)
    validate_resolution_ratio(capability, resolution, ratio)
    output_format = normalize_output_format(output_format, capability)

    if model == GPT_IMAGE_2:
        kie_model = "gpt-image-2-image-to-image" if references else "gpt-image-2-text-to-image"
        model_input = build_routed_model_input(
            kie_model,
            prompt,
            references,
            aspect_ratio=ratio,
            resolution=resolution,
        )
    else:
        kie_model = "nano-banana-pro"
        model_input = build_routed_model_input(
            kie_model,
            prompt,
            references,
            aspect_ratio=ratio,
            resolution=resolution,
            output_format=output_format,
        )
    return {
        "model": kie_model,
        "input": model_input,
    }, {
        "ui_model": model,
        "kie_model": kie_model,
        "requested_resolution": resolution,
        "requested_aspect_ratio": ratio,
        "reference_count": len(references),
        "output_format": output_format,
    }


def build_capability_schema(model):
    capability = deepcopy(capability_for(model))
    fields = [
        {
            "key": "aspect_ratio",
            "type": "select",
            "label": "比例",
            "options": [{"value": value, "label": value} for value in capability["aspect_ratios"]],
            "default": capability["default_aspect_ratio"],
        },
        {
            "key": "resolution",
            "type": "select",
            "label": "分辨率",
            "options": [{"value": value, "label": value} for value in capability["resolutions"]],
            "default": capability["default_resolution"],
            "aspect_ratio_exclusions": {
                key: list(values)
                for key, values in capability.get("resolution_ratio_exclusions", {}).items()
            },
        },
    ]
    if capability.get("output_formats"):
        fields.append({
            "key": "output_format",
            "type": "select",
            "label": "输出格式",
            "options": [{"value": value, "label": value.upper()} for value in capability["output_formats"]],
            "default": capability.get("default_output_format") or capability["output_formats"][0],
        })
    fields.append({
        "key": "reference_images",
        "type": "refs",
        "label": "参考图",
        "max": capability["reference_image_limit"],
        "max_file_bytes": capability["reference_max_bytes"],
        "formats": list(capability["reference_formats"]),
    })
    return {
        "provider_id": "kie",
        "model": model,
        "label": capability["label"],
        "reference_image_limit": capability["reference_image_limit"],
        "fields": fields,
    }
