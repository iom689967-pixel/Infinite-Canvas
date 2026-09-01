import asyncio
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

import providers.kie.uploads as uploads


class KieReferenceUploadCacheTests(unittest.IsolatedAsyncioTestCase):
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

    def add_image(self, name, color):
        path = self.root / name
        Image.new("RGB", (8, 6), color).save(path, format="PNG")
        url = f"/assets/{name}"
        self.paths[url] = path
        return {"url": url, "name": name}

    def resolve_local_path(self, url):
        path = self.paths.get(url)
        return str(path) if path else ""

    async def prepare(self, references, *, cache=None):
        return await uploads.prepare_kie_references(
            "mock-api-key",
            references,
            resolve_local_path=self.resolve_local_path,
            max_bytes=1024 * 1024,
            cache=cache or self.cache,
            http_client=object(),
        )

    @staticmethod
    def upload_mock():
        async def upload(_client, _api_key, _content, meta, *, content_hash, **_kwargs):
            return f"https://kie.test/{content_hash}.png", 200, meta["mime_type"]

        return AsyncMock(side_effect=upload)

    @staticmethod
    def validate_mock():
        return AsyncMock(return_value=(200, "image/png"))

    async def test_01_first_image_misses_uploads_validates_and_stores(self):
        reference = self.add_image("first.png", (10, 20, 30))
        upload = self.upload_mock()
        validate = self.validate_mock()

        with patch.object(uploads, "_upload_normalized_image", upload), patch.object(
            uploads, "_validate_kie_public_url", validate
        ):
            urls, audits = await self.prepare([reference])

        self.assertEqual(upload.await_count, 1)
        self.assertEqual(validate.await_count, 1)
        self.assertEqual(audits[0]["cache_status"], "miss")
        self.assertTrue(urls[0].startswith("https://kie.test/"))
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(len(payload["entries"]), 1)
        digest, entry = next(iter(payload["entries"].items()))
        normalized, _ = uploads.normalize_image_bytes(
            self.paths[reference["url"]].read_bytes(),
            index=1,
            filename="first.png",
            max_bytes=1024 * 1024,
        )
        self.assertEqual(digest, hashlib.sha256(normalized).hexdigest())
        self.assertEqual(entry["sha256"], digest)
        self.assertEqual(entry["kie_url"], urls[0])
        self.assertEqual(entry["validated_at"], self.clock[0])
        self.assertEqual(entry["created_at"], self.clock[0])
        self.assertEqual(entry["last_used_at"], self.clock[0])
        self.assertGreater(entry["size"], 0)
        self.assertEqual(entry["mime_type"], "image/png")
        serialized = self.cache_path.read_text(encoding="utf-8")
        self.assertNotIn("mock-api-key", serialized)
        self.assertNotIn("first.png", serialized)
        self.assertFalse(list(self.cache_path.parent.glob(".*.tmp")))

    async def test_02_second_same_image_hits_without_upload_or_validate(self):
        reference = self.add_image("same.png", (30, 40, 50))
        upload = self.upload_mock()
        validate = self.validate_mock()

        with patch.object(uploads, "_upload_normalized_image", upload), patch.object(
            uploads, "_validate_kie_public_url", validate
        ):
            first_urls, _ = await self.prepare([reference])
            self.clock[0] += 10
            second_urls, second_audits = await self.prepare([reference])

        self.assertEqual(first_urls, second_urls)
        self.assertEqual(upload.await_count, 1)
        self.assertEqual(validate.await_count, 1)
        self.assertEqual(second_audits[0]["cache_status"], "hit")
        entry = next(iter(json.loads(self.cache_path.read_text())["entries"].values()))
        self.assertEqual(entry["last_used_at"], self.clock[0])

    async def test_03_different_images_do_not_reuse_urls(self):
        references = [
            self.add_image("red.png", (255, 0, 0)),
            self.add_image("blue.png", (0, 0, 255)),
        ]
        upload = self.upload_mock()
        validate = self.validate_mock()

        with patch.object(uploads, "_upload_normalized_image", upload), patch.object(
            uploads, "_validate_kie_public_url", validate
        ):
            urls, _ = await self.prepare(references)

        self.assertEqual(upload.await_count, 2)
        self.assertEqual(validate.await_count, 2)
        self.assertEqual(len(set(urls)), 2)
        self.assertEqual(len(json.loads(self.cache_path.read_text())["entries"]), 2)

    async def test_04_expired_entry_is_revalidated_without_reupload(self):
        reference = self.add_image("expired.png", (90, 80, 70))
        upload = self.upload_mock()
        validate = self.validate_mock()

        with patch.object(uploads, "_upload_normalized_image", upload), patch.object(
            uploads, "_validate_kie_public_url", validate
        ):
            first_urls, _ = await self.prepare([reference])
            self.clock[0] += 101
            second_urls, second_audits = await self.prepare([reference])

        self.assertEqual(first_urls, second_urls)
        self.assertEqual(upload.await_count, 1)
        self.assertEqual(validate.await_count, 2)
        self.assertEqual(second_audits[0]["cache_status"], "revalidated")
        entry = next(iter(json.loads(self.cache_path.read_text())["entries"].values()))
        self.assertEqual(entry["validated_at"], self.clock[0])

    async def test_05_invalid_expired_url_is_invalidated_then_uploaded_again(self):
        reference = self.add_image("invalid.png", (50, 60, 70))
        upload_urls = ["https://kie.test/old.png", "https://kie.test/new.png"]
        upload = AsyncMock(side_effect=[
            (upload_urls[0], 200, "image/png"),
            (upload_urls[1], 200, "image/png"),
        ])
        validate = AsyncMock(side_effect=[
            (200, "image/png"),
            uploads.KieReferenceError(
                "cached URL unavailable",
                index=1,
                filename="invalid.png",
                stage="kie-read",
                http_status=404,
            ),
            (200, "image/png"),
        ])

        with patch.object(uploads, "_upload_normalized_image", upload), patch.object(
            uploads, "_validate_kie_public_url", validate
        ):
            await self.prepare([reference])
            self.clock[0] += 101
            urls, audits = await self.prepare([reference])

        self.assertEqual(urls, [upload_urls[1]])
        self.assertEqual(upload.await_count, 2)
        self.assertEqual(validate.await_count, 3)
        self.assertEqual(audits[0]["cache_status"], "miss")
        entry = next(iter(json.loads(self.cache_path.read_text())["entries"].values()))
        self.assertEqual(entry["kie_url"], upload_urls[1])

    async def test_06_missing_cache_file_runs_normally(self):
        reference = self.add_image("missing-cache.png", (1, 2, 3))
        self.assertFalse(self.cache_path.exists())
        upload = self.upload_mock()
        validate = self.validate_mock()

        with patch.object(uploads, "_upload_normalized_image", upload), patch.object(
            uploads, "_validate_kie_public_url", validate
        ):
            urls, _ = await self.prepare([reference])

        self.assertTrue(urls[0].startswith("https://kie.test/"))
        self.assertTrue(self.cache_path.exists())

    async def test_07_corrupt_cache_degrades_to_empty_and_recovers(self):
        reference = self.add_image("corrupt-cache.png", (3, 2, 1))
        self.cache_path.parent.mkdir(parents=True)
        self.cache_path.write_text("{not valid json", encoding="utf-8")
        upload = self.upload_mock()
        validate = self.validate_mock()

        with patch.object(uploads, "_upload_normalized_image", upload), patch.object(
            uploads, "_validate_kie_public_url", validate
        ):
            urls, _ = await self.prepare([reference])

        self.assertTrue(urls)
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["entries"]), 1)

    async def test_08_concurrent_same_hash_uploads_once(self):
        reference = self.add_image("concurrent-same.png", (11, 22, 33))
        upload_count = 0

        async def delayed_upload(_client, _api_key, _content, meta, *, content_hash, **_kwargs):
            nonlocal upload_count
            upload_count += 1
            await asyncio.sleep(0.03)
            return f"https://kie.test/{content_hash}.png", 200, meta["mime_type"]

        upload = AsyncMock(side_effect=delayed_upload)
        validate = self.validate_mock()
        with patch.object(uploads, "_upload_normalized_image", upload), patch.object(
            uploads, "_validate_kie_public_url", validate
        ):
            results = await asyncio.gather(
                self.prepare([reference]),
                self.prepare([reference]),
            )

        self.assertEqual(upload_count, 1)
        self.assertEqual(validate.await_count, 1)
        self.assertEqual(results[0][0], results[1][0])

    async def test_09_concurrent_different_hashes_do_not_block_each_other(self):
        first = self.add_image("concurrent-a.png", (120, 1, 1))
        second = self.add_image("concurrent-b.png", (1, 120, 1))
        active = 0
        max_active = 0
        both_started = asyncio.Event()

        async def coordinated_upload(_client, _api_key, _content, meta, *, content_hash, **_kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.5)
            active -= 1
            return f"https://kie.test/{content_hash}.png", 200, meta["mime_type"]

        upload = AsyncMock(side_effect=coordinated_upload)
        validate = self.validate_mock()
        with patch.object(uploads, "_upload_normalized_image", upload), patch.object(
            uploads, "_validate_kie_public_url", validate
        ):
            await asyncio.gather(self.prepare([first]), self.prepare([second]))

        self.assertEqual(max_active, 2)
        self.assertEqual(upload.await_count, 2)

    async def test_10_metrics_include_required_timings_without_private_cache_logs(self):
        reference = self.add_image("private-filename.png", (7, 8, 9))
        upload = self.upload_mock()
        validate = self.validate_mock()
        output = io.StringIO()

        with contextlib.redirect_stdout(output), patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(uploads, "_validate_kie_public_url", validate):
            await self.prepare([reference])
            await self.prepare([reference])

        lines = output.getvalue().splitlines()
        cache_lines = [line for line in lines if line.startswith("[KieRefCache]")]
        self.assertTrue(any("MISS hash=" in line for line in cache_lines))
        self.assertTrue(any("STORE hash=" in line for line in cache_lines))
        self.assertTrue(any("HIT hash=" in line for line in cache_lines))
        self.assertTrue(all("private-filename" not in line for line in cache_lines))
        metric_rows = [json.loads(line) for line in lines if '"event": "kie_reference_prepare_metrics"' in line]
        self.assertEqual(len(metric_rows), 2)
        for metrics in metric_rows:
            self.assertIn("total_reference_prepare_ms", metrics)
            self.assertIn("cache_hits", metrics)
            self.assertIn("cache_misses", metrics)
            timing = metrics["references"][0]
            for key in ("normalize_ms", "cache_lookup_ms", "upload_ms", "validate_ms"):
                self.assertIn(key, timing)


if __name__ == "__main__":
    unittest.main()
