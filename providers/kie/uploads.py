"""Kie-only reference image normalization, upload, caching, and URL verification."""

import asyncio
import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
import weakref
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageOps


KIE_UPLOAD_BASE_URL = "https://kieai.redpandaai.co"
KIE_STREAM_UPLOAD_PATH = "/api/file-stream-upload"
KIE_UPLOAD_PATH = "images/infinite-canvas"
KIE_REFERENCE_CACHE_TTL_SECONDS = 24 * 60 * 60
KIE_REFERENCE_CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "kie_reference_cache.json"


_CACHE_FILE_LOCK = threading.RLock()
_CACHE_HASH_LOCKS_GUARD = threading.Lock()
_CACHE_HASH_LOCKS_BY_LOOP = weakref.WeakKeyDictionary()
_CACHE_SOURCE_LOCKS_BY_LOOP = weakref.WeakKeyDictionary()
_DEFAULT_REFERENCE_CACHE = None


def _safe_timestamp(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _cache_ttl_from_env():
    value = _safe_timestamp(os.getenv("KIE_REFERENCE_CACHE_TTL_SECONDS"), KIE_REFERENCE_CACHE_TTL_SECONDS)
    return max(60.0, value)


def _log_reference_cache(action, digest=""):
    suffix = f" hash={str(digest or '')[:8]}" if digest else ""
    print(f"[KieRefCache] {action}{suffix}", flush=True)


def _log_reference_source_cache(action, fingerprint=""):
    suffix = f" source={str(fingerprint or '')[:8]}" if fingerprint else ""
    print(f"[KieRefSourceCache] {action}{suffix}", flush=True)


class KieReferenceUploadCache:
    """Persistent two-level cache for local sources and normalized Kie uploads."""

    def __init__(self, path=KIE_REFERENCE_CACHE_PATH, *, ttl_seconds=None, now_fn=None):
        self.path = Path(path)
        self.ttl_seconds = max(
            60.0,
            _safe_timestamp(ttl_seconds, _cache_ttl_from_env()),
        )
        self._now = now_fn or time.time

    def now(self):
        return float(self._now())

    def _read_payload_unlocked(self):
        if not self.path.exists():
            return {"entries": {}, "sources": {}}
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            entries = payload.get("entries") if isinstance(payload, dict) else None
            sources = payload.get("sources") if isinstance(payload, dict) else None
            return {
                "entries": dict(entries) if isinstance(entries, dict) else {},
                "sources": dict(sources) if isinstance(sources, dict) else {},
            }
        except (OSError, ValueError, TypeError):
            _log_reference_cache("LOAD_FAILED")
            return {"entries": {}, "sources": {}}

    def _read_entries_unlocked(self):
        return self._read_payload_unlocked()["entries"]

    def _write_payload_unlocked(self, entries, sources):
        temporary_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            payload = {
                "version": 2,
                "updated_at": self.now(),
                "entries": entries,
                "sources": sources,
            }
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            return True
        except (OSError, TypeError, ValueError):
            _log_reference_cache("WRITE_FAILED")
            return False
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    def lookup(self, digest):
        now = self.now()
        with _CACHE_FILE_LOCK:
            payload = self._read_payload_unlocked()
            entries = payload["entries"]
            entry = entries.get(digest)
            if not isinstance(entry, dict):
                return "miss", None
            validated_at = _safe_timestamp(entry.get("validated_at"))
            expires_at = _safe_timestamp(entry.get("expires_at"))
            url = str(entry.get("kie_url") or "").strip()
            if entry.get("sha256") != digest or not url.startswith("https://") or validated_at <= 0:
                entries.pop(digest, None)
                self._write_payload_unlocked(entries, payload["sources"])
                return "invalid", None
            if (expires_at > 0 and now >= expires_at) or now - validated_at >= self.ttl_seconds:
                return "expired", dict(entry)
            return "hit", dict(entry)

    def store(self, digest, url, *, size, mime_type, validated_at=None, expires_at=None):
        now = self.now()
        with _CACHE_FILE_LOCK:
            payload = self._read_payload_unlocked()
            entries = payload["entries"]
            previous = entries.get(digest) if isinstance(entries.get(digest), dict) else {}
            entry = {
                "sha256": digest,
                "kie_url": str(url or ""),
                "validated_at": _safe_timestamp(validated_at, now),
                "created_at": _safe_timestamp(previous.get("created_at"), now),
                "last_used_at": now,
                "size": int(size or 0),
                "mime_type": str(mime_type or ""),
            }
            if expires_at is not None and _safe_timestamp(expires_at) > 0:
                entry["expires_at"] = _safe_timestamp(expires_at)
            elif _safe_timestamp(previous.get("expires_at")) > 0:
                entry["expires_at"] = _safe_timestamp(previous.get("expires_at"))
            entries[digest] = entry
            return self._write_payload_unlocked(entries, payload["sources"])

    def touch(self, digest):
        with _CACHE_FILE_LOCK:
            payload = self._read_payload_unlocked()
            entries = payload["entries"]
            entry = entries.get(digest)
            if not isinstance(entry, dict):
                return False
            entry = dict(entry)
            entry["last_used_at"] = self.now()
            entries[digest] = entry
            return self._write_payload_unlocked(entries, payload["sources"])

    def invalidate(self, digest):
        with _CACHE_FILE_LOCK:
            payload = self._read_payload_unlocked()
            entries = payload["entries"]
            if digest not in entries:
                return True
            entries.pop(digest, None)
            return self._write_payload_unlocked(entries, payload["sources"])

    def lookup_source(self, fingerprint):
        with _CACHE_FILE_LOCK:
            payload = self._read_payload_unlocked()
            sources = payload["sources"]
            entry = sources.get(fingerprint)
            if not isinstance(entry, dict):
                return "miss", None
            digest = str(entry.get("normalized_sha256") or "").strip().lower()
            if entry.get("source_fingerprint") != fingerprint or not re.fullmatch(r"[0-9a-f]{64}", digest):
                sources.pop(fingerprint, None)
                self._write_payload_unlocked(payload["entries"], sources)
                return "invalid", None
            return "hit", dict(entry)

    def store_source(self, fingerprint, normalized_digest, *, size, mtime_ns):
        now = self.now()
        with _CACHE_FILE_LOCK:
            payload = self._read_payload_unlocked()
            sources = payload["sources"]
            previous = sources.get(fingerprint) if isinstance(sources.get(fingerprint), dict) else {}
            sources[fingerprint] = {
                "source_fingerprint": fingerprint,
                "normalized_sha256": str(normalized_digest or ""),
                "created_at": _safe_timestamp(previous.get("created_at"), now),
                "last_used_at": now,
                "size": int(size or 0),
                "mtime_ns": int(mtime_ns or 0),
            }
            return self._write_payload_unlocked(payload["entries"], sources)

    def touch_source(self, fingerprint):
        with _CACHE_FILE_LOCK:
            payload = self._read_payload_unlocked()
            sources = payload["sources"]
            entry = sources.get(fingerprint)
            if not isinstance(entry, dict):
                return False
            entry = dict(entry)
            entry["last_used_at"] = self.now()
            sources[fingerprint] = entry
            return self._write_payload_unlocked(payload["entries"], sources)


def _default_reference_cache():
    global _DEFAULT_REFERENCE_CACHE
    if _DEFAULT_REFERENCE_CACHE is None:
        _DEFAULT_REFERENCE_CACHE = KieReferenceUploadCache()
    return _DEFAULT_REFERENCE_CACHE


def _reference_hash_lock(cache_path, digest):
    loop = asyncio.get_running_loop()
    key = f"{Path(cache_path).resolve()}:{digest}"
    with _CACHE_HASH_LOCKS_GUARD:
        locks = _CACHE_HASH_LOCKS_BY_LOOP.setdefault(loop, {})
        lock = locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            locks[key] = lock
        return lock


def _reference_source_lock(cache_path, fingerprint):
    loop = asyncio.get_running_loop()
    key = f"{Path(cache_path).resolve()}:{fingerprint}"
    with _CACHE_HASH_LOCKS_GUARD:
        locks = _CACHE_SOURCE_LOCKS_BY_LOOP.setdefault(loop, {})
        lock = locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            locks[key] = lock
        return lock


def _local_source_fingerprint(path):
    canonical_path = os.path.realpath(os.path.abspath(os.fspath(path)))
    stat = os.stat(canonical_path)
    fingerprint_payload = json.dumps(
        [canonical_path, int(stat.st_size), int(stat.st_mtime_ns)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(fingerprint_payload).hexdigest(), {
        "path": canonical_path,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


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


async def _upload_normalized_image(client, api_key, content, meta, *, index, filename, content_hash=""):
    digest = str(content_hash or hashlib.sha256(content).hexdigest())[:12]
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


def _cached_normalized_meta(entry):
    mime_type = str((entry or {}).get("mime_type") or "image/png")
    return {
        "mime_type": mime_type,
        "extension": ".jpg" if mime_type == "image/jpeg" else ".png",
        "bytes": int((entry or {}).get("size") or 0),
        "cached": True,
    }


async def _resolve_normalized_upload(
    cache,
    client,
    api_key,
    normalized,
    meta,
    *,
    digest,
    index,
    filename,
):
    result = {
        "public_url": "",
        "upload_status": None,
        "upload_type": "",
        "verify_status": None,
        "verify_type": "",
        "cache_status": "miss",
        "cache_hit": 0,
        "cache_miss": 0,
        "cache_wait_ms": 0.0,
        "cache_lookup_ms": 0.0,
        "upload_ms": 0.0,
        "validate_ms": 0.0,
    }
    cache_lock = _reference_hash_lock(cache.path, digest)
    cache_wait_started = time.perf_counter()
    async with cache_lock:
        result["cache_wait_ms"] = (time.perf_counter() - cache_wait_started) * 1000
        lookup_started = time.perf_counter()
        lookup_state, cache_entry = cache.lookup(digest)
        result["cache_lookup_ms"] = (time.perf_counter() - lookup_started) * 1000

        if lookup_state == "hit":
            _log_reference_cache("HIT", digest)
            cache.touch(digest)
            result.update({
                "public_url": str(cache_entry.get("kie_url") or ""),
                "upload_type": str(cache_entry.get("mime_type") or meta["mime_type"]),
                "verify_status": 200,
                "verify_type": str(cache_entry.get("mime_type") or meta["mime_type"]),
                "cache_status": "hit",
                "cache_hit": 1,
            })
        elif lookup_state == "expired":
            _log_reference_cache("EXPIRED", digest)
            cached_url = str(cache_entry.get("kie_url") or "")
            validate_started = time.perf_counter()
            try:
                verify_status, verify_type = await _validate_kie_public_url(
                    client, cached_url, index=index, filename=filename
                )
            except KieReferenceError:
                result["validate_ms"] += (time.perf_counter() - validate_started) * 1000
                cache.invalidate(digest)
                _log_reference_cache("INVALIDATE", digest)
            else:
                result["validate_ms"] += (time.perf_counter() - validate_started) * 1000
                upload_type = str(cache_entry.get("mime_type") or meta["mime_type"])
                cache.store(
                    digest,
                    cached_url,
                    size=meta["bytes"],
                    mime_type=upload_type,
                    validated_at=cache.now(),
                )
                _log_reference_cache("HIT", digest)
                result.update({
                    "public_url": cached_url,
                    "upload_type": upload_type,
                    "verify_status": verify_status,
                    "verify_type": verify_type,
                    "cache_status": "revalidated",
                    "cache_hit": 1,
                })
        elif lookup_state == "invalid":
            _log_reference_cache("INVALIDATE", digest)

        if not result["public_url"]:
            _log_reference_cache("MISS", digest)
            result["cache_miss"] = 1
            result["cache_status"] = "miss"
            upload_started = time.perf_counter()
            public_url, upload_status, upload_type = await _upload_normalized_image(
                client,
                api_key,
                normalized,
                meta,
                index=index,
                filename=filename,
                content_hash=digest,
            )
            result["upload_ms"] = (time.perf_counter() - upload_started) * 1000
            validate_started = time.perf_counter()
            verify_status, verify_type = await _validate_kie_public_url(
                client, public_url, index=index, filename=filename
            )
            result["validate_ms"] += (time.perf_counter() - validate_started) * 1000
            result.update({
                "public_url": public_url,
                "upload_status": upload_status,
                "upload_type": upload_type,
                "verify_status": verify_status,
                "verify_type": verify_type,
            })
            if cache.store(
                digest,
                public_url,
                size=meta["bytes"],
                mime_type=upload_type or meta["mime_type"],
                validated_at=cache.now(),
            ):
                _log_reference_cache("STORE", digest)
    return result


async def _resolve_source_cache_hit(
    cache,
    fingerprint,
    *,
    digest,
    client,
    index,
    filename,
):
    result = {
        "state": "miss",
        "entry": None,
        "cache_wait_ms": 0.0,
        "cache_lookup_ms": 0.0,
        "stale_validate_ms": 0.0,
        "verify_status": None,
        "verify_type": "",
    }
    cache_lock = _reference_hash_lock(cache.path, digest)
    wait_started = time.perf_counter()
    async with cache_lock:
        result["cache_wait_ms"] = (time.perf_counter() - wait_started) * 1000
        lookup_started = time.perf_counter()
        state, entry = cache.lookup(digest)
        result["cache_lookup_ms"] = (time.perf_counter() - lookup_started) * 1000
        result["state"] = state
        result["entry"] = entry
        if state == "hit":
            _log_reference_cache("HIT", digest)
            cache.touch(digest)
            cache.touch_source(fingerprint)
        elif state == "expired":
            _log_reference_source_cache("STALE_VALIDATE", fingerprint)
            cached_url = str((entry or {}).get("kie_url") or "")
            validate_started = time.perf_counter()
            try:
                verify_status, verify_type = await _validate_kie_public_url(
                    client,
                    cached_url,
                    index=index,
                    filename=filename,
                )
            except KieReferenceError:
                result["stale_validate_ms"] = (time.perf_counter() - validate_started) * 1000
                cache.invalidate(digest)
                _log_reference_cache("INVALIDATE", digest)
                _log_reference_source_cache("STALE_INVALID", fingerprint)
                result["state"] = "stale_invalid"
                result["entry"] = None
            else:
                result["stale_validate_ms"] = (time.perf_counter() - validate_started) * 1000
                now = cache.now()
                mime_type = str((entry or {}).get("mime_type") or verify_type or "image/png")
                cache.store(
                    digest,
                    cached_url,
                    size=int((entry or {}).get("size") or 0),
                    mime_type=mime_type,
                    validated_at=now,
                )
                cache.touch_source(fingerprint)
                _log_reference_cache("HIT", digest)
                _log_reference_source_cache("STALE_VALID", fingerprint)
                result["state"] = "stale_valid"
                result["entry"] = {
                    **(entry or {}),
                    "kie_url": cached_url,
                    "mime_type": mime_type,
                    "validated_at": now,
                    "last_used_at": now,
                }
                result["verify_status"] = verify_status
                result["verify_type"] = verify_type
    return result


async def prepare_kie_references(
    api_key,
    references,
    *,
    resolve_local_path,
    max_bytes,
    cache=None,
    http_client=None,
):
    prepared_urls = []
    audits = []
    timing_rows = []
    cache_hits = 0
    cache_misses = 0
    source_cache_hits = 0
    source_cache_misses = 0
    normalize_skipped_count = 0
    stale_normalize_avoided_count = 0
    total_started = time.perf_counter()
    cache = cache or _default_reference_cache()
    timeout = httpx.Timeout(connect=20.0, read=120.0, write=120.0, pool=20.0)
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    try:
        for index, ref in enumerate(references or [], 1):
            reference_started = time.perf_counter()
            raw_url = ref.get("url", "") if isinstance(ref, dict) else ref
            source_url = str(raw_url or "").strip()
            filename = str((ref or {}).get("name") or "").strip() if isinstance(ref, dict) else ""
            filename = filename or os.path.basename(urllib.parse.urlsplit(source_url).path) or f"reference-{index}.png"
            source_form = classify_reference_url(source_url)
            source_status = None
            source_type = ""
            source_fingerprint = ""
            source_fingerprint_ms = 0.0
            source_cache_lookup_ms = 0.0
            source_cache_wait_ms = 0.0
            source_cache_status = "ineligible"
            stale_validate_ms = 0.0
            stale_normalize_avoided = False
            normalize_ms = 0.0
            cache_lookup_ms = 0.0
            cache_wait_ms = 0.0
            upload_ms = 0.0
            validate_ms = 0.0
            cache_status = "miss"
            public_url = ""
            upload_status = None
            upload_type = ""
            verify_status = None
            verify_type = ""
            digest = ""
            meta = None
            content = None
            local_source = None
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
                    fingerprint_started = time.perf_counter()
                    source_fingerprint, local_source = _local_source_fingerprint(local_path)
                    source_fingerprint_ms = (time.perf_counter() - fingerprint_started) * 1000
                except OSError as exc:
                    raise KieReferenceError(
                        f"本地图片元数据读取失败：{exc}", index=index, filename=filename
                    ) from exc
                if local_source["size"] <= 0:
                    raise KieReferenceError("图片文件大小为 0", index=index, filename=filename)
                if local_source["size"] > max_bytes:
                    raise KieReferenceError(
                        f"本地图片超过 {max_bytes} bytes",
                        index=index,
                        filename=filename,
                    )

            resolution = None
            if local_source:
                source_lock = _reference_source_lock(cache.path, source_fingerprint)
                source_wait_started = time.perf_counter()
                async with source_lock:
                    source_cache_wait_ms = (time.perf_counter() - source_wait_started) * 1000
                    source_lookup_started = time.perf_counter()
                    source_lookup_state, source_entry = cache.lookup_source(source_fingerprint)
                    source_cache_lookup_ms = (time.perf_counter() - source_lookup_started) * 1000
                    if source_lookup_state == "hit":
                        digest = str(source_entry.get("normalized_sha256") or "")
                        source_resolution = await _resolve_source_cache_hit(
                            cache,
                            source_fingerprint,
                            digest=digest,
                            client=client,
                            index=index,
                            filename=filename,
                        )
                        cache_wait_ms += source_resolution["cache_wait_ms"]
                        cache_lookup_ms += source_resolution["cache_lookup_ms"]
                        stale_validate_ms += source_resolution["stale_validate_ms"]
                        validate_ms += source_resolution["stale_validate_ms"]
                        if source_resolution["state"] in {"hit", "stale_valid"}:
                            cache_entry = source_resolution["entry"] or {}
                            if source_resolution["state"] == "hit":
                                _log_reference_source_cache("HIT", source_fingerprint)
                                source_cache_status = "hit"
                            else:
                                source_cache_status = "stale_valid"
                                stale_normalize_avoided = True
                                stale_normalize_avoided_count += 1
                            source_cache_hits += 1
                            normalize_skipped_count += 1
                            cache_hits += 1
                            cache_status = "hit" if source_resolution["state"] == "hit" else "revalidated"
                            public_url = str(cache_entry.get("kie_url") or "")
                            upload_type = str(cache_entry.get("mime_type") or "image/png")
                            source_type = upload_type
                            verify_status = source_resolution["verify_status"] or 200
                            verify_type = source_resolution["verify_type"] or upload_type
                            meta = _cached_normalized_meta(cache_entry)
                        else:
                            if source_resolution["state"] != "stale_invalid":
                                _log_reference_source_cache("STALE_INVALID", source_fingerprint)
                            source_cache_status = "stale_invalid"
                    elif source_lookup_state == "invalid":
                        _log_reference_source_cache("MISS", source_fingerprint)
                        source_cache_status = "miss"
                    else:
                        _log_reference_source_cache("MISS", source_fingerprint)
                        source_cache_status = "miss"

                    if not public_url:
                        source_cache_misses += 1
                        try:
                            with open(local_source["path"], "rb") as handle:
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
                        normalize_started = time.perf_counter()
                        normalized, meta = normalize_image_bytes(
                            content, index=index, filename=filename, max_bytes=max_bytes
                        )
                        normalize_ms = (time.perf_counter() - normalize_started) * 1000
                        digest = hashlib.sha256(normalized).hexdigest()
                        resolution = await _resolve_normalized_upload(
                            cache,
                            client,
                            api_key,
                            normalized,
                            meta,
                            digest=digest,
                            index=index,
                            filename=filename,
                        )
                        if cache.store_source(
                            source_fingerprint,
                            digest,
                            size=local_source["size"],
                            mtime_ns=local_source["mtime_ns"],
                        ):
                            _log_reference_source_cache("STORE", source_fingerprint)
            else:
                normalize_started = time.perf_counter()
                normalized, meta = normalize_image_bytes(
                    content, index=index, filename=filename, max_bytes=max_bytes
                )
                normalize_ms = (time.perf_counter() - normalize_started) * 1000
                digest = hashlib.sha256(normalized).hexdigest()
                resolution = await _resolve_normalized_upload(
                    cache,
                    client,
                    api_key,
                    normalized,
                    meta,
                    digest=digest,
                    index=index,
                    filename=filename,
                )

            if resolution:
                public_url = resolution["public_url"]
                upload_status = resolution["upload_status"]
                upload_type = resolution["upload_type"]
                verify_status = resolution["verify_status"]
                verify_type = resolution["verify_type"]
                cache_status = resolution["cache_status"]
                cache_hits += resolution["cache_hit"]
                cache_misses += resolution["cache_miss"]
                cache_wait_ms += resolution["cache_wait_ms"]
                cache_lookup_ms += resolution["cache_lookup_ms"]
                upload_ms += resolution["upload_ms"]
                validate_ms += resolution["validate_ms"]
            prepared_urls.append(public_url)
            reference_total_ms = (time.perf_counter() - reference_started) * 1000
            timing = {
                "index": index,
                "hash": digest[:8],
                "cache_status": cache_status,
                "source_fingerprint": source_fingerprint[:8],
                "source_cache_status": source_cache_status,
                "source_fingerprint_ms": round(source_fingerprint_ms, 3),
                "source_cache_lookup_ms": round(source_cache_lookup_ms, 3),
                "source_cache_wait_ms": round(source_cache_wait_ms, 3),
                "stale_validate_ms": round(stale_validate_ms, 3),
                "normalize_ms": round(normalize_ms, 3),
                "cache_wait_ms": round(cache_wait_ms, 3),
                "cache_lookup_ms": round(cache_lookup_ms, 3),
                "upload_ms": round(upload_ms, 3),
                "validate_ms": round(validate_ms, 3),
                "total_reference_prepare_ms": round(reference_total_ms, 3),
            }
            timing_rows.append(timing)
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
                "cache_status": cache_status,
                "cache_hash": digest[:8],
                "source_cache_status": source_cache_status,
                "source_fingerprint": source_fingerprint[:8],
                "stale_normalize_avoided": stale_normalize_avoided,
                "timing": timing,
            })
    finally:
        if owns_client:
            await client.aclose()
    print(json.dumps({"event": "kie_reference_preflight", "references": audits}, ensure_ascii=False), flush=True)
    print(json.dumps({
        "event": "kie_reference_prepare_metrics",
        "total_reference_prepare_ms": round((time.perf_counter() - total_started) * 1000, 3),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "source_cache_hits": source_cache_hits,
        "source_cache_misses": source_cache_misses,
        "normalize_skipped_count": normalize_skipped_count,
        "stale_normalize_avoided_count": stale_normalize_avoided_count,
        "references": timing_rows,
    }, ensure_ascii=False), flush=True)
    return prepared_urls, audits
