import asyncio
import inspect
import unittest
from unittest.mock import AsyncMock, patch

import main


class DeprecationMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_runs_startup_once(self):
        startup = AsyncMock()
        with patch.object(main, "startup_event", startup):
            async with main.lifespan(main.app):
                pass
        startup.assert_awaited_once_with()

    async def test_startup_preserves_migration_order(self):
        calls = []
        previous_loop = main.GLOBAL_LOOP

        async def fake_to_thread(function, *args, **kwargs):
            calls.append(function)

        try:
            with patch.object(main.asyncio, "to_thread", new=fake_to_thread):
                await main.startup_event()
            self.assertIs(main.GLOBAL_LOOP, asyncio.get_running_loop())
            self.assertEqual(calls, [
                main.migrate_asset_library_into_dirs,
                main.migrate_double_extension_uploads,
                main.migrate_mislabeled_image_extensions,
            ])
        finally:
            main.GLOBAL_LOOP = previous_loop

    def test_provider_save_uses_model_dump_with_same_exclusion(self):
        source = inspect.getsource(main.save_providers)
        self.assertIn('item.model_dump(exclude={"api_key"})', source)
        self.assertNotIn('item.dict(exclude={"api_key"})', source)
        payload = main.ApiProviderPayload(id="audit", name="Audit", api_key="secret")
        dumped = payload.model_dump(exclude={"api_key"})
        self.assertNotIn("api_key", dumped)
        self.assertEqual(dumped["id"], "audit")
        self.assertEqual(dumped["name"], "Audit")


if __name__ == "__main__":
    unittest.main()
