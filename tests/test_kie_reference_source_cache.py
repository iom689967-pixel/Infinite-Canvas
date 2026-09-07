import asyncio
import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

import providers.kie.uploads as uploads


class KieReferenceSourceCacheTests(unittest.IsolatedAsyncioTestCase):
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
        self.lightweight_validate = AsyncMock(return_value=(200, "image/png"))
        self.lightweight_patcher = patch.object(
            uploads,
            "validate_reference_availability_lightweight",
            self.lightweight_validate,
        )
        self.lightweight_patcher.start()

    def tearDown(self):
        self.lightweight_patcher.stop()
        self.temp_dir.cleanup()

    def add_image(self, name, color, *, size=(8, 6)):
        path = self.root / name
        Image.new("RGB", size, color).save(path, format="PNG")
        url = f"/assets/{name}"
        self.paths[url] = path
        return {"url": url, "name": name}

    def resolve_local_path(self, url):
        path = self.paths.get(url)
        return str(path) if path else ""

    async def prepare(self, references):
        return await uploads.prepare_kie_references(
            "mock-api-key",
            references,
            resolve_local_path=self.resolve_local_path,
            max_bytes=1024 * 1024,
            cache=self.cache,
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

    @staticmethod
    def metrics_from(output):
        rows = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if '"event": "kie_reference_prepare_metrics"' in line
        ]
        return rows[-1]

    async def test_01_first_local_source_misses_normalizes_and_stores_mapping(self):
        reference = self.add_image("first-source.png", (10, 20, 30))
        upload = self.upload_mock()
        validate = self.validate_mock()
        output = io.StringIO()

        with contextlib.redirect_stdout(output), patch.object(
            uploads, "normalize_image_bytes", wraps=uploads.normalize_image_bytes
        ) as normalize, patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(uploads, "_validate_kie_public_url", validate):
            _urls, audits = await self.prepare([reference])

        self.assertEqual(normalize.call_count, 1)
        self.assertEqual(upload.await_count, 1)
        self.assertEqual(validate.await_count, 1)
        self.assertEqual(audits[0]["source_cache_status"], "miss")
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 2)
        self.assertEqual(len(payload["entries"]), 1)
        self.assertEqual(len(payload["sources"]), 1)
        source = next(iter(payload["sources"].values()))
        self.assertIn(source["normalized_sha256"], payload["entries"])
        self.assertNotIn(str(self.paths[reference["url"]]), self.cache_path.read_text())
        self.assertIn("[KieRefSourceCache] MISS source=", output.getvalue())
        self.assertIn("[KieRefSourceCache] STORE source=", output.getvalue())

    async def test_02_second_same_file_skips_normalize_upload_and_validate(self):
        reference = self.add_image("same-source.png", (30, 40, 50))
        upload = self.upload_mock()
        validate = self.validate_mock()
        output = io.StringIO()

        with contextlib.redirect_stdout(output), patch.object(
            uploads, "normalize_image_bytes", wraps=uploads.normalize_image_bytes
        ) as normalize, patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(uploads, "_validate_kie_public_url", validate):
            first_urls, _ = await self.prepare([reference])
            self.clock[0] += 10
            second_urls, second_audits = await self.prepare([reference])

        self.assertEqual(first_urls, second_urls)
        self.assertEqual(normalize.call_count, 1)
        self.assertEqual(upload.await_count, 1)
        self.assertEqual(validate.await_count, 1)
        self.assertEqual(second_audits[0]["source_cache_status"], "hit")
        self.assertEqual(second_audits[0]["timing"]["normalize_ms"], 0.0)
        self.assertEqual(second_audits[0]["timing"]["upload_ms"], 0.0)
        self.assertEqual(second_audits[0]["timing"]["validate_ms"], 0.0)
        metrics = self.metrics_from(output)
        self.assertEqual(metrics["source_cache_hits"], 1)
        self.assertEqual(metrics["source_cache_misses"], 0)
        self.assertEqual(metrics["normalize_skipped_count"], 1)

    async def test_03_same_path_size_or_mtime_change_misses_and_normalizes_again(self):
        reference = self.add_image("changed-source.png", (50, 60, 70))
        path = self.paths[reference["url"]]
        upload = self.upload_mock()
        validate = self.validate_mock()

        with contextlib.redirect_stdout(io.StringIO()), patch.object(
            uploads, "normalize_image_bytes", wraps=uploads.normalize_image_bytes
        ) as normalize, patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(uploads, "_validate_kie_public_url", validate):
            _urls, first_audits = await self.prepare([reference])
            old_mtime_ns = path.stat().st_mtime_ns
            Image.new("RGB", (11, 7), (70, 60, 50)).save(path, format="PNG")
            os.utime(path, ns=(old_mtime_ns + 1_000_000_000, old_mtime_ns + 1_000_000_000))
            _urls, second_audits = await self.prepare([reference])

        self.assertEqual(normalize.call_count, 2)
        self.assertEqual(upload.await_count, 2)
        self.assertEqual(first_audits[0]["source_cache_status"], "miss")
        self.assertEqual(second_audits[0]["source_cache_status"], "miss")
        self.assertNotEqual(
            first_audits[0]["source_fingerprint"],
            second_audits[0]["source_fingerprint"],
        )

    async def test_04_source_stale_with_valid_url_skips_normalize_and_upload(self):
        reference = self.add_image("expired-source.png", (90, 80, 70))
        upload = self.upload_mock()
        validate = self.validate_mock()
        output = io.StringIO()

        with contextlib.redirect_stdout(output), patch.object(
            uploads, "normalize_image_bytes", wraps=uploads.normalize_image_bytes
        ) as normalize, patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(uploads, "_validate_kie_public_url", validate):
            await self.prepare([reference])
            self.clock[0] += 101
            _urls, audits = await self.prepare([reference])

        self.assertEqual(normalize.call_count, 1)
        self.assertEqual(upload.await_count, 1)
        self.assertEqual(validate.await_count, 2)
        self.assertEqual(audits[0]["source_cache_status"], "stale_valid")
        self.assertEqual(audits[0]["cache_status"], "revalidated")
        self.assertEqual(audits[0]["timing"]["normalize_ms"], 0.0)
        self.assertEqual(audits[0]["timing"]["upload_ms"], 0.0)
        self.assertGreaterEqual(audits[0]["timing"]["stale_validate_ms"], 0.0)
        metrics = self.metrics_from(output)
        self.assertEqual(metrics["normalize_skipped_count"], 1)
        self.assertEqual(metrics["stale_normalize_avoided_count"], 1)
        self.assertIn("[KieRefSourceCache] STALE_VALIDATE source=", output.getvalue())
        self.assertIn("[KieRefSourceCache] STALE_VALID source=", output.getvalue())

    async def test_05_source_stale_with_invalid_url_normalizes_and_uploads(self):
        reference = self.add_image("invalid-stale-source.png", (80, 70, 60))
        upload = AsyncMock(side_effect=[
            ("https://kie.test/old.png", 200, "image/png"),
            ("https://kie.test/new.png", 200, "image/png"),
        ])
        validate = AsyncMock(side_effect=[
            (200, "image/png"),
            uploads.KieReferenceError(
                "cached URL unavailable",
                index=1,
                filename="invalid-stale-source.png",
                stage="kie-read",
                http_status=404,
            ),
            (200, "image/png"),
        ])
        output = io.StringIO()

        with contextlib.redirect_stdout(output), patch.object(
            uploads, "normalize_image_bytes", wraps=uploads.normalize_image_bytes
        ) as normalize, patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(uploads, "_validate_kie_public_url", validate):
            await self.prepare([reference])
            self.clock[0] += 101
            urls, audits = await self.prepare([reference])

        self.assertEqual(urls, ["https://kie.test/new.png"])
        self.assertEqual(normalize.call_count, 2)
        self.assertEqual(upload.await_count, 2)
        self.assertEqual(validate.await_count, 3)
        self.assertEqual(audits[0]["source_cache_status"], "stale_invalid")
        self.assertGreaterEqual(audits[0]["timing"]["normalize_ms"], 0.0)
        self.assertEqual(self.metrics_from(output)["stale_normalize_avoided_count"], 0)
        self.assertIn("[KieRefSourceCache] STALE_INVALID source=", output.getvalue())

    async def test_06_corrupt_source_mapping_safely_falls_back(self):
        reference = self.add_image("corrupt-mapping.png", (6, 7, 8))
        upload = self.upload_mock()
        validate = self.validate_mock()

        with contextlib.redirect_stdout(io.StringIO()), patch.object(
            uploads, "normalize_image_bytes", wraps=uploads.normalize_image_bytes
        ) as normalize, patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(uploads, "_validate_kie_public_url", validate):
            first_urls, _ = await self.prepare([reference])
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            next(iter(payload["sources"].values()))["normalized_sha256"] = "corrupt"
            self.cache_path.write_text(json.dumps(payload), encoding="utf-8")
            second_urls, audits = await self.prepare([reference])

        self.assertEqual(first_urls, second_urls)
        self.assertEqual(normalize.call_count, 2)
        self.assertEqual(upload.await_count, 1)
        self.assertEqual(validate.await_count, 1)
        self.assertEqual(audits[0]["source_cache_status"], "miss")

    async def test_07_concurrent_same_stale_source_validates_once(self):
        reference = self.add_image("concurrent-stale.png", (17, 18, 19))
        upload = self.upload_mock()
        validate_calls = 0

        async def delayed_validate(*_args, **_kwargs):
            nonlocal validate_calls
            validate_calls += 1
            if validate_calls > 1:
                await asyncio.sleep(0.03)
            return 200, "image/png"

        validate = AsyncMock(side_effect=delayed_validate)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(
            uploads, "normalize_image_bytes", wraps=uploads.normalize_image_bytes
        ) as normalize, patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(uploads, "_validate_kie_public_url", validate):
            await self.prepare([reference])
            self.clock[0] += 101
            results = await asyncio.gather(
                self.prepare([reference]),
                self.prepare([reference]),
            )

        self.assertEqual(normalize.call_count, 1)
        self.assertEqual(upload.await_count, 1)
        self.assertEqual(validate.await_count, 2)
        self.assertEqual(results[0][0], results[1][0])
        statuses = {results[0][1][0]["source_cache_status"], results[1][1][0]["source_cache_status"]}
        self.assertEqual(statuses, {"stale_valid", "hit"})
        metrics = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if '"event": "kie_reference_prepare_metrics"' in line
        ]
        self.assertEqual(sum(item["stale_normalize_avoided_count"] for item in metrics[-2:]), 1)

    async def test_08_version_one_upload_cache_loads_and_gains_source_mapping(self):
        reference = self.add_image("legacy-cache.png", (1, 2, 3))
        normalized, meta = uploads.normalize_image_bytes(
            self.paths[reference["url"]].read_bytes(),
            index=1,
            filename="legacy-cache.png",
            max_bytes=1024 * 1024,
        )
        digest = hashlib.sha256(normalized).hexdigest()
        legacy_url = "https://kie.test/legacy.png"
        self.cache_path.parent.mkdir(parents=True)
        self.cache_path.write_text(json.dumps({
            "version": 1,
            "updated_at": self.clock[0],
            "entries": {
                digest: {
                    "sha256": digest,
                    "kie_url": legacy_url,
                    "validated_at": self.clock[0],
                    "created_at": self.clock[0],
                    "last_used_at": self.clock[0],
                    "size": meta["bytes"],
                    "mime_type": meta["mime_type"],
                }
            },
        }), encoding="utf-8")
        upload = self.upload_mock()
        validate = self.validate_mock()

        with contextlib.redirect_stdout(io.StringIO()), patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(uploads, "_validate_kie_public_url", validate):
            urls, audits = await self.prepare([reference])

        self.assertEqual(urls, [legacy_url])
        self.assertEqual(upload.await_count, 0)
        self.assertEqual(validate.await_count, 0)
        self.assertEqual(audits[0]["cache_status"], "hit")
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 2)
        self.assertIn(digest, payload["entries"])
        self.assertEqual(len(payload["sources"]), 1)

    async def test_09_corrupt_cache_degrades_and_rebuilds_both_levels(self):
        reference = self.add_image("corrupt-source.png", (3, 2, 1))
        self.cache_path.parent.mkdir(parents=True)
        self.cache_path.write_text("{not valid json", encoding="utf-8")
        upload = self.upload_mock()
        validate = self.validate_mock()

        with contextlib.redirect_stdout(io.StringIO()), patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(uploads, "_validate_kie_public_url", validate):
            urls, audits = await self.prepare([reference])

        self.assertTrue(urls)
        self.assertEqual(audits[0]["source_cache_status"], "miss")
        payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["entries"]), 1)
        self.assertEqual(len(payload["sources"]), 1)

    async def test_10_concurrent_same_source_normalizes_once(self):
        reference = self.add_image("concurrent-source.png", (11, 22, 33))

        async def delayed_upload(_client, _api_key, _content, meta, *, content_hash, **_kwargs):
            await asyncio.sleep(0.03)
            return f"https://kie.test/{content_hash}.png", 200, meta["mime_type"]

        upload = AsyncMock(side_effect=delayed_upload)
        validate = self.validate_mock()
        with contextlib.redirect_stdout(io.StringIO()), patch.object(
            uploads, "normalize_image_bytes", wraps=uploads.normalize_image_bytes
        ) as normalize, patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(uploads, "_validate_kie_public_url", validate):
            results = await asyncio.gather(
                self.prepare([reference]),
                self.prepare([reference]),
            )

        self.assertEqual(normalize.call_count, 1)
        self.assertEqual(upload.await_count, 1)
        self.assertEqual(validate.await_count, 1)
        self.assertEqual(results[0][0], results[1][0])
        statuses = {results[0][1][0]["source_cache_status"], results[1][1][0]["source_cache_status"]}
        self.assertEqual(statuses, {"miss", "hit"})

    async def test_11_concurrent_different_sources_do_not_share_source_lock(self):
        first = self.add_image("source-a.png", (120, 1, 1))
        second = self.add_image("source-b.png", (1, 120, 1))
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
        with contextlib.redirect_stdout(io.StringIO()), patch.object(
            uploads, "_upload_normalized_image", upload
        ), patch.object(uploads, "_validate_kie_public_url", validate):
            await asyncio.gather(self.prepare([first]), self.prepare([second]))

        self.assertEqual(max_active, 2)
        self.assertEqual(upload.await_count, 2)


if __name__ == "__main__":
    unittest.main()
