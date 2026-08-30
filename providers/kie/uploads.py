"""Kie-only reference image normalization, upload, and URL verification."""

import base64
import hashlib
import json
import os
import re
import urllib.parse
from io import BytesIO

import httpx
from PIL import Image, ImageOps


KIE_UPLOAD_BASE_URL = "https://kieai.redpandaai.co"
KIE_STREAM_UPLOAD_PATH = "/api/file-stream-upload"
KIE_UPLOAD_PATH = "images/infinite-canvas"


class KieReferenceError(RuntimeError):
    def __init__(
        self,
        message,
        *,
        index,
        filename,
        stage="upload",
        http_status=None,
        content_type="",
    ):
        self.index = int(index)
        self.filename = str(filename or f"reference-{index}")
        self.stage = str(stage or "upload")
        self.http_status = http_status
        self.content_type = str(content_type or "")
        stage_label = "Kie 读取预检失败" if self.stage == "kie-read" else "上传失败"
        status_label = str(http_status) if http_status is not None else "未取得"
        type_label = self.content_type or "未取得"
        super().__init__(
            f"第{self.index}张参考图「{self.filename}」{stage_label}：{message}；"
            f"HTTP {status_label}；Content-Type {type_label}"
        )


def classify_reference_url(value):
    text = str(value or "").strip()
    if text.startswith("blob:"):
        return "blob"
    if re.match(r"^data:image/[^;,]+;base64,", text, re.I):
        return "data:image/base64"
    if text.startswith("data:"):
        return "data:other"
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme in {"http", "https"}:
        host = (parsed.hostname or "").lower()
        if host in {"127.0.0.1", "localhost", "::1"}:
            return "127.0.0.1/localhost URL"
        return "public HTTPS URL" if parsed.scheme == "https" else "public HTTP URL"
    if text.startswith(("/assets/", "/output/")):
        return "local canvas URL"
    if parsed.scheme == "file" or os.path.isabs(text):
        return "local file path"
    return "unknown"


def _magic_image_type(content):
    raw = bytes(content or b"")
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if raw.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    return ""


def _safe_filename(value, fallback):
    name = os.path.basename(str(value or "").strip()) or fallback
    stem = re.sub(r"[^0-9A-Za-z._-]+", "_", os.path.splitext(name)[0]).strip("._")
    return (stem or fallback)[:80]


def _decode_data_url(value, *, index, filename):
    header, sep, encoded = str(value or "").partition(",")
    content_type = header[5:].split(";", 1)[0].strip().lower() if header.startswith("data:") else ""
    if not sep or ";base64" not in header.lower() or not encoded:
        raise KieReferenceError(
            "Base64 数据为空、被截断或不是合法 data URL",
            index=index,
            filename=filename,
            content_type=content_type,
        )
    try:
        content = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise KieReferenceError(
            "Base64 数据无法完整解码，可能已截断",
            index=index,
            filename=filename,
            content_type=content_type,
        ) from exc
    return content, content_type


async def _download_public_image(client, url, *, index, filename, max_bytes):
    try:
        response = await client.get(url, headers={"Accept": "image/*"})
    except httpx.HTTPError as exc:
        raise KieReferenceError(
            f"公网地址下载失败：{exc}", index=index, filename=filename
        ) from exc
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if response.status_code != 200:
        raise KieReferenceError(
            "公网地址没有返回 HTTP 200",
            index=index,
            filename=filename,
            http_status=response.status_code,
            content_type=content_type,
        )
    if not content_type.startswith("image/"):
        raise KieReferenceError(
            "公网地址返回的不是图片（可能是登录页或 HTML）",
            index=index,
            filename=filename,
            http_status=response.status_code,
            content_type=content_type,
        )
    if not response.content:
        raise KieReferenceError(
            "公网图片内容为空",
            index=index,
            filename=filename,
            http_status=response.status_code,
            content_type=content_type,
        )
    if len(response.content) > max_bytes:
        raise KieReferenceError(
            f"公网图片超过 {max_bytes} bytes",
            index=index,
            filename=filename,
            http_status=response.status_code,
            content_type=content_type,
        )
    magic_type = _magic_image_type(response.content)
    if not magic_type:
        raise KieReferenceError(
            "Content-Type 虽是 image/*，但内容 magic bytes 不是可识别图片",
            index=index,
            filename=filename,
            http_status=response.status_code,
            content_type=content_type,
        )
    return response.content, content_type, response.status_code


def normalize_image_bytes(content, *, index, filename, max_bytes):
    if not content:
        raise KieReferenceError("图片文件大小为 0", index=index, filename=filename)
    try:
        with Image.open(BytesIO(content)) as source:
            source.load()
            source = ImageOps.exif_transpose(source)
            width, height = source.size
            if width <= 0 or height <= 0:
                raise ValueError("invalid dimensions")
            if source.mode in {"RGBA", "LA"} or (source.mode == "P" and "transparency" in source.info):
                rgba = source.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                normalized = flattened
            else:
                normalized = source.convert("RGB")
            buffer = BytesIO()
            normalized.save(buffer, format="PNG", optimize=True)
            output = buffer.getvalue()
            output_format = "PNG"
            mime_type = "image/png"
            extension = ".png"
            if len(output) > max_bytes:
                buffer = BytesIO()
                normalized.save(buffer, format="JPEG", quality=95, optimize=True)
                output = buffer.getvalue()
                output_format = "JPEG"
                mime_type = "image/jpeg"
                extension = ".jpg"
    except KieReferenceError:
        raise
    except Exception as exc:
        raise KieReferenceError(
            "图片无法正常解码或不是受支持的位图",
            index=index,
            filename=filename,
            content_type=_magic_image_type(content),
        ) from exc
    if not output or len(output) > max_bytes:
        raise KieReferenceError(
            f"规范化后的图片为空或超过 {max_bytes} bytes",
            index=index,
            filename=filename,
            content_type=mime_type,
        )
    return output, {
        "format": output_format,
        "mime_type": mime_type,
        "extension": extension,
        "mode": "RGB",
        "bits_per_channel": 8,
        "width": width,
        "height": height,
        "bytes": len(output),
    }


async def _upload_normalized_image(client, api_key, content, meta, *, index, filename):
    digest = hashlib.sha256(content).hexdigest()[:12]
    stem = _safe_filename(filename, f"reference-{index}")
    upload_name = f"{stem}-{digest}{meta['extension']}"
    try:
        response = await client.post(
            f"{KIE_UPLOAD_BASE_URL}{KIE_STREAM_UPLOAD_PATH}",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            data={"uploadPath": KIE_UPLOAD_PATH, "fileName": upload_name},
            files={"file": (upload_name, content, meta["mime_type"])},
        )
    except httpx.HTTPError as exc:
        raise KieReferenceError(
            f"Kie File Upload 网络请求失败：{exc}",
            index=index,
            filename=filename,
            content_type=meta["mime_type"],
        ) from exc
    response_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    try:
        payload = response.json()
    except ValueError as exc:
        raise KieReferenceError(
            "Kie File Upload 返回了非 JSON 响应",
            index=index,
            filename=filename,
            http_status=response.status_code,
            content_type=response_type,
        ) from exc
    if not isinstance(payload, dict):
        raise KieReferenceError(
            "Kie File Upload 返回的 JSON 结构不是对象",
            index=index,
            filename=filename,
            http_status=response.status_code,
            content_type=response_type,
        )
    code = payload.get("code")
    if response.status_code != 200 or code not in (None, 200, "200") or payload.get("success") is False:
        message = payload.get("msg") or payload.get("message") or "Kie File Upload 未成功"
        raise KieReferenceError(
            str(message),
            index=index,
            filename=filename,
            http_status=response.status_code,
            content_type=response_type,
        )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    public_url = str(data.get("downloadUrl") or data.get("fileUrl") or "").strip()
    if not public_url.startswith("https://"):
        raise KieReferenceError(
            "Kie File Upload 没有返回公网 HTTPS downloadUrl/fileUrl",
            index=index,
            filename=filename,
            http_status=response.status_code,
            content_type=response_type,
        )
    return public_url, response.status_code, str(data.get("mimeType") or meta["mime_type"])


async def _validate_kie_public_url(client, url, *, index, filename):
    try:
        response = await client.get(url, headers={"Accept": "image/*"})
    except httpx.HTTPError as exc:
        raise KieReferenceError(
            f"Kie 临时图片 URL 无法匿名读取：{exc}",
            index=index,
            filename=filename,
            stage="kie-read",
        ) from exc
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if response.status_code != 200:
        raise KieReferenceError(
            "Kie 临时图片 URL 没有返回 HTTP 200",
            index=index,
            filename=filename,
            stage="kie-read",
            http_status=response.status_code,
            content_type=content_type,
        )
    if not content_type.startswith("image/") or not _magic_image_type(response.content):
        raise KieReferenceError(
            "Kie 临时图片 URL 返回了非图片内容",
            index=index,
            filename=filename,
            stage="kie-read",
            http_status=response.status_code,
            content_type=content_type,
        )
    return response.status_code, content_type


async def prepare_kie_references(api_key, references, *, resolve_local_path, max_bytes):
    prepared_urls = []
    audits = []
    timeout = httpx.Timeout(connect=20.0, read=120.0, write=120.0, pool=20.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for index, ref in enumerate(references or [], 1):
            raw_url = ref.get("url", "") if isinstance(ref, dict) else ref
            source_url = str(raw_url or "").strip()
            filename = str((ref or {}).get("name") or "").strip() if isinstance(ref, dict) else ""
            filename = filename or os.path.basename(urllib.parse.urlsplit(source_url).path) or f"reference-{index}.png"
            source_form = classify_reference_url(source_url)
            source_status = None
            source_type = ""
            if source_form == "blob":
                raise KieReferenceError(
                    "blob URL 只存在于当前浏览器，服务端无法读取；请先通过画布上传接口保存后再生成",
                    index=index,
                    filename=filename,
                )
            if source_form == "data:image/base64":
                content, source_type = _decode_data_url(source_url, index=index, filename=filename)
            elif source_form in {"public HTTPS URL", "public HTTP URL"}:
                content, source_type, source_status = await _download_public_image(
                    client, source_url, index=index, filename=filename, max_bytes=max_bytes
                )
            else:
                local_path = resolve_local_path(source_url)
                if not local_path or not os.path.isfile(local_path):
                    raise KieReferenceError(
                        f"无法解析本地图片来源（实际形式：{source_form}）",
                        index=index,
                        filename=filename,
                    )
                try:
                    with open(local_path, "rb") as handle:
                        content = handle.read(max_bytes + 1)
                except OSError as exc:
                    raise KieReferenceError(
                        f"本地图片读取失败：{exc}", index=index, filename=filename
                    ) from exc
                source_type = _magic_image_type(content)
                if len(content) > max_bytes:
                    raise KieReferenceError(
                        f"本地图片超过 {max_bytes} bytes",
                        index=index,
                        filename=filename,
                        content_type=source_type,
                    )
            normalized, meta = normalize_image_bytes(
                content, index=index, filename=filename, max_bytes=max_bytes
            )
            public_url, upload_status, upload_type = await _upload_normalized_image(
                client, api_key, normalized, meta, index=index, filename=filename
            )
            verify_status, verify_type = await _validate_kie_public_url(
                client, public_url, index=index, filename=filename
            )
            prepared_urls.append(public_url)
            audits.append({
                "index": index,
                "filename": filename,
                "source_form": source_form,
                "source_http_status": source_status,
                "source_content_type": source_type,
                "normalized": meta,
                "upload_http_status": upload_status,
                "upload_content_type": upload_type,
                "public_url": public_url,
                "verify_http_status": verify_status,
                "verify_content_type": verify_type,
            })
    print(json.dumps({"event": "kie_reference_preflight", "references": audits}, ensure_ascii=False), flush=True)
    return prepared_urls, audits
