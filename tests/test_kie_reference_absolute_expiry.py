import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

import providers.kie.uploads as uploads


class KieReferenceAbsoluteExpiryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.cache_path = self.root / "data" / "kie_reference_cache.json"
        self.clock = [1_000.0]
        self.cache = uploads.KieReferenceUploadCache(
            self.cache_path,
            ttl_seconds=100,
            now_fn=lambda: self.clock[0],
        )
        self.paths = {}

    def tearDown(self):
        self.temp_dir.cleanup()

    def add_images(self, count):
        references = []
        for index in range(count):
            name = f"reference-{index}.png"
            path = self.root / name
            Image.new("RGB", (8 + index, 6), (index * 20, 10, 30)).save(path, format="PNG")
            url = f"/assets/{name}"
            self.paths[url] = path
            references.append({"url": url, "name": name})
        return references

    def resolve_local_path(self, url):
        path = self.paths.get(url)
        return str(path) if path else ""

    @staticmethod
    def upload_mock(*, official_expiry=0.0):
        sequence = {"value": 0}

        async def upload(_client, _api_key, _content, meta, *, content_hash, **_kwargs):
            sequence["value"] += 1
            result = (
                f"https://kie.test/{content_hash}-{sequence['value']}.png",
                200,
                meta["mime_type"],
            )
            return (*result, official_expiry) if official_expiry else result

        return AsyncMock(side_effect=upload)

    @staticmethod
    def valid_full_check():
        return AsyncMock(return_value=(200, "image/png"))

    @staticmethod
    def valid_light_check():
        return AsyncMock(return_value=(200, "image/png"))

    async def prepare(self, references, upload, full_check, light_check, *, capture=None):
        stream = capture or io.StringIO()
        with contextlib.redirect_stdout(stream), patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(
            uploads, "_validate_kie_public_url", full_check
        ), patch.object(
            uploads, "validate_reference_availability_lightweight", light_check
        ):
            result = await uploads.prepare_kie_references(
                "mock-api-key",
                references,
                resolve_local_path=self.resolve_local_path,
                max_bytes=1024 * 1024,
                cache=self.cache,
                http_client=object(),
            )
        return result, stream

    @staticmethod
    def latest_metrics(stream):
        return [
            json.loads(line)
            for line in stream.getvalue().splitlines()
            if '"event": "kie_reference_prepare_metrics"' in line
        ][-1]

    def cache_payload(self):
        return json.loads(self.cache_path.read_text(encoding="utf-8"))

    async def test_01_new_upload_stores_created_validated_and_absolute_expiry(self):
        reference = self.add_images(1)
        upload = self.upload_mock()
        await self.prepare(reference, upload, self.valid_full_check(), self.valid_light_check())
        entry = next(iter(self.cache_payload()["entries"].values()))
        self.assertEqual(entry["created_at"], self.clock[0])
        self.assertEqual(entry["validated_at"], self.clock[0])
        self.assertEqual(
            entry["expires_at"],
            self.clock[0] + uploads.KIE_REFERENCE_ABSOLUTE_TTL_SECONDS,
        )

    async def test_02_before_absolute_expiry_reuses_cached_url(self):
        reference = self.add_images(1)
        upload = self.upload_mock()
        full = self.valid_full_check()
        light = self.valid_light_check()
        first, _ = await self.prepare(reference, upload, full, light)
        self.clock[0] += 10
        second, _ = await self.prepare(reference, upload, full, light)
        self.assertEqual(first[0], second[0])
        self.assertEqual(upload.await_count, 1)
        self.assertEqual(light.await_count, 1)

    async def test_03_validation_refresh_does_not_extend_absolute_expiry(self):
        reference = self.add_images(1)
        upload = self.upload_mock()
        full = self.valid_full_check()
        light = self.valid_light_check()
        await self.prepare(reference, upload, full, light)
        original = next(iter(self.cache_payload()["entries"].values()))
        original_expiry = original["expires_at"]
        self.clock[0] += 101
        await self.prepare(reference, upload, full, light)
        refreshed = next(iter(self.cache_payload()["entries"].values()))
        self.assertEqual(refreshed["validated_at"], self.clock[0])
        self.assertEqual(refreshed["expires_at"], original_expiry)
        self.assertEqual(upload.await_count, 1)

    async def test_04_absolute_expiry_forces_upload_without_stale_validation(self):
        reference = self.add_images(1)
        upload = self.upload_mock()
        full = self.valid_full_check()
        light = self.valid_light_check()
        first, _ = await self.prepare(reference, upload, full, light)
        self.clock[0] += uploads.KIE_REFERENCE_ABSOLUTE_TTL_SECONDS + 1
        second, output = await self.prepare(reference, upload, full, light)
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(upload.await_count, 2)
        self.assertEqual(full.await_count, 2)
        self.assertEqual(light.await_count, 0)
        self.assertIn("ABSOLUTE_EXPIRED hash=", output.getvalue())
        self.assertIn("REUPLOAD_EXPIRED hash=", output.getvalue())

    async def test_05_legacy_entry_derives_expiry_only_from_created_at(self):
        reference = self.add_images(1)
        upload = self.upload_mock()
        full = self.valid_full_check()
        light = self.valid_light_check()
        await self.prepare(reference, upload, full, light)
        payload = self.cache_payload()
        entry = next(iter(payload["entries"].values()))
        created_at = entry["created_at"]
        entry.pop("expires_at")
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        self.clock[0] += 10
        await self.prepare(reference, upload, full, light)
        repaired = next(iter(self.cache_payload()["entries"].values()))
        self.assertEqual(
            repaired["expires_at"],
            created_at + uploads.KIE_REFERENCE_ABSOLUTE_TTL_SECONDS,
        )
        self.assertEqual(upload.await_count, 1)

    async def test_06_legacy_entry_without_created_at_reuploads(self):
        reference = self.add_images(1)
        upload = self.upload_mock()
        full = self.valid_full_check()
        light = self.valid_light_check()
        first, _ = await self.prepare(reference, upload, full, light)
        payload = self.cache_payload()
        entry = next(iter(payload["entries"].values()))
        entry.pop("expires_at")
        entry.pop("created_at")
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
        second, _ = await self.prepare(reference, upload, full, light)
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(upload.await_count, 2)

    async def test_07_pre_submit_all_valid_reuses_all_six(self):
        references = self.add_images(6)
        upload = self.upload_mock()
        full = self.valid_full_check()
        light = self.valid_light_check()
        await self.prepare(references, upload, full, light)
        (_urls, _audits), output = await self.prepare(references, upload, full, light)
        metrics = self.latest_metrics(output)
        self.assertEqual(upload.await_count, 6)
        self.assertEqual(light.await_count, 6)
        self.assertEqual(metrics["pre_submit_invalid_count"], 0)
        self.assertEqual(metrics["selective_reupload_count"], 0)
        self.assertEqual(metrics["valid_reference_reuse_count"], 6)

    async def test_08_pre_submit_one_404_reuploads_only_one(self):
        references = self.add_images(6)
        upload = self.upload_mock()
        full = self.valid_full_check()
        light = self.valid_light_check()
        first, _ = await self.prepare(references, upload, full, light)
        light.side_effect = [
            uploads.KieReferenceError("Not Found", index=1, filename="x", stage="kie-read", http_status=404, content_type="text/plain"),
            *((200, "image/png") for _ in range(5)),
        ]
        (second_urls, audits), output = await self.prepare(references, upload, full, light)
        self.assertNotEqual(first[0][0], second_urls[0])
        self.assertEqual(first[0][1:], second_urls[1:])
        self.assertEqual(upload.await_count, 7)
        self.assertEqual(sum(a["cache_status"] == "reuploaded_unavailable" for a in audits), 1)
        metrics = self.latest_metrics(output)
        self.assertEqual(metrics["pre_submit_invalid_count"], 1)
        self.assertEqual(metrics["selective_reupload_count"], 1)
        self.assertEqual(metrics["valid_reference_reuse_count"], 5)

    async def test_09_pre_submit_two_404s_reupload_only_two(self):
        references = self.add_images(6)
        upload = self.upload_mock()
        full = self.valid_full_check()
        light = self.valid_light_check()
        first, _ = await self.prepare(references, upload, full, light)
        failure = lambda index: uploads.KieReferenceError(
            "Not Found", index=index, filename="x", stage="kie-read", http_status=404, content_type="text/plain"
        )
        light.side_effect = [failure(1), failure(2), *((200, "image/png") for _ in range(4))]
        (second_urls, _audits), output = await self.prepare(references, upload, full, light)
        self.assertNotEqual(first[0][0], second_urls[0])
        self.assertNotEqual(first[0][1], second_urls[1])
        self.assertEqual(first[0][2:], second_urls[2:])
        self.assertEqual(upload.await_count, 8)
        metrics = self.latest_metrics(output)
        self.assertEqual(metrics["pre_submit_invalid_count"], 2)
        self.assertEqual(metrics["selective_reupload_count"], 2)
        self.assertEqual(metrics["valid_reference_reuse_count"], 4)

    async def test_10_text_plain_not_found_is_invalid_and_reuploaded(self):
        reference = self.add_images(1)
        upload = self.upload_mock()
        full = self.valid_full_check()
        light = self.valid_light_check()
        await self.prepare(reference, upload, full, light)
        light.side_effect = uploads.KieReferenceError(
            "Not Found", index=1, filename="x", stage="kie-read", http_status=404, content_type="text/plain"
        )
        (_urls, audits), output = await self.prepare(reference, upload, full, light)
        self.assertEqual(audits[0]["verify_content_type"], "image/png")
        self.assertIn("PRE_SUBMIT_404 hash=", output.getvalue())
        self.assertIn("REUPLOAD_UNAVAILABLE hash=", output.getvalue())

    async def test_11_stale_but_not_absolute_expired_can_revalidate(self):
        reference = self.add_images(1)
        upload = self.upload_mock()
        full = self.valid_full_check()
        light = self.valid_light_check()
        first, _ = await self.prepare(reference, upload, full, light)
        expiry = next(iter(self.cache_payload()["entries"].values()))["expires_at"]
        self.clock[0] += 101
        second, _ = await self.prepare(reference, upload, full, light)
        self.assertEqual(first[0], second[0])
        self.assertEqual(next(iter(self.cache_payload()["entries"].values()))["expires_at"], expiry)
        self.assertEqual(upload.await_count, 1)

    async def test_12_absolute_expired_url_is_reuploaded_even_if_checks_would_pass(self):
        reference = self.add_images(1)
        upload = self.upload_mock()
        full = self.valid_full_check()
        light = self.valid_light_check()
        await self.prepare(reference, upload, full, light)
        self.clock[0] += uploads.KIE_REFERENCE_ABSOLUTE_TTL_SECONDS + 1
        await self.prepare(reference, upload, full, light)
        self.assertEqual(light.await_count, 0)
        self.assertEqual(upload.await_count, 2)

    async def test_13_concurrent_absolute_expiry_reuploads_once(self):
        reference = self.add_images(1)
        upload = self.upload_mock()
        full = self.valid_full_check()
        light = self.valid_light_check()
        await self.prepare(reference, upload, full, light)
        self.clock[0] += uploads.KIE_REFERENCE_ABSOLUTE_TTL_SECONDS + 1
        with contextlib.redirect_stdout(io.StringIO()), patch.object(
            uploads, "normalize_image_bytes", wraps=uploads.normalize_image_bytes
        ) as normalize, patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(
            uploads, "_validate_kie_public_url", full
        ), patch.object(
            uploads, "validate_reference_availability_lightweight", light
        ):
            results = await asyncio.gather(
                uploads.prepare_kie_references(
                    "mock-api-key", reference, resolve_local_path=self.resolve_local_path,
                    max_bytes=1024 * 1024, cache=self.cache, http_client=object(),
                ),
                uploads.prepare_kie_references(
                    "mock-api-key", reference, resolve_local_path=self.resolve_local_path,
                    max_bytes=1024 * 1024, cache=self.cache, http_client=object(),
                ),
            )
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(upload.await_count, 2)
        self.assertEqual(normalize.call_count, 1)

    async def test_14_upload_response_expiry_is_authoritative(self):
        reference = self.add_images(1)
        official_expiry = self.clock[0] + 5_000
        upload = self.upload_mock(official_expiry=official_expiry)
        await self.prepare(reference, upload, self.valid_full_check(), self.valid_light_check())
        entry = next(iter(self.cache_payload()["entries"].values()))
        self.assertEqual(entry["expires_at"], official_expiry)

    async def test_15_lightweight_guard_stops_after_magic_prefix(self):
        class Response:
            status_code = 200
            headers = {"Content-Type": "image/png", "Content-Length": "9999999"}

            def __init__(self):
                self.chunks_read = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def aiter_bytes(self, **_kwargs):
                self.chunks_read += 1
                yield b"\x89PNG\r\n\x1a\n" + b"x" * 24
                self.chunks_read += 1
                yield b"this chunk must not be consumed"

        response = Response()

        class Client:
            def stream(self, *_args, **_kwargs):
                return response

        status, content_type = await uploads.validate_reference_availability_lightweight(
            Client(), "https://kie.test/ref.png", index=1, filename="ref.png"
        )
        self.assertEqual((status, content_type), (200, "image/png"))
        self.assertEqual(response.chunks_read, 1)

    async def test_16_upload_response_parses_official_expires_at(self):
        class Response:
            status_code = 200
            headers = {"Content-Type": "application/json"}

            @staticmethod
            def json():
                return {
                    "code": 200,
                    "data": {
                        "downloadUrl": "https://kie.test/reference.png",
                        "mimeType": "image/png",
                        "expiresAt": "2030-01-02T03:04:05Z",
                    },
                }

        class Client:
            @staticmethod
            async def post(*_args, **_kwargs):
                return Response()

        result = await uploads._upload_normalized_image(
            Client(),
            "mock-api-key",
            b"normalized-image",
            {"extension": ".png", "mime_type": "image/png"},
            index=1,
            filename="reference.png",
        )
        self.assertEqual(result[:3], ("https://kie.test/reference.png", 200, "image/png"))
        self.assertEqual(
            result[3],
            uploads.datetime.datetime(2030, 1, 2, 3, 4, 5, tzinfo=uploads.datetime.timezone.utc).timestamp(),
        )

    async def test_17_upload_response_without_expiry_uses_cache_fallback(self):
        class Response:
            status_code = 200
            headers = {"Content-Type": "application/json"}

            @staticmethod
            def json():
                return {
                    "code": 200,
                    "data": {
                        "downloadUrl": "https://kie.test/reference.png",
                        "mimeType": "image/png",
                    },
                }

        class Client:
            @staticmethod
            async def post(*_args, **_kwargs):
                return Response()

        result = await uploads._upload_normalized_image(
            Client(),
            "mock-api-key",
            b"normalized-image",
            {"extension": ".png", "mime_type": "image/png"},
            index=1,
            filename="reference.png",
        )
        self.assertEqual(result[3], 0.0)


if __name__ == "__main__":
    unittest.main()
