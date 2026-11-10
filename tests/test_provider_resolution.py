import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main

PROVIDERS = [
    {"id": "modelscope", "name": "ModelScope", "enabled": True, "image_models": ["m"]},
    {"id": "runninghub", "name": "RunningHub", "enabled": True, "image_models": []},
    {"id": "custom-api", "name": "API", "enabled": True, "image_models": ["gpt-image-2"]},
    {"id": "off", "name": "Off", "enabled": False, "image_models": ["x"]},
]


class ProviderResolutionTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(main, "load_api_providers", side_effect=lambda: [dict(p) for p in PROVIDERS])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_known_provider_returned(self):
        self.assertEqual(main.get_api_provider("custom-api")["id"], "custom-api")

    def test_unknown_provider_rejected_instead_of_falling_back(self):
        with self.assertRaises(HTTPException) as ctx:
            main.get_api_provider("custom-api-5")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("custom-api-5", ctx.exception.detail)

    def test_unspecified_or_legacy_alias_uses_default(self):
        default_id = main.get_primary_provider_id(PROVIDERS)
        self.assertEqual(main.get_api_provider("")["id"], default_id)
        self.assertEqual(main.get_api_provider("comfly")["id"], default_id)
        self.assertEqual(main.get_api_provider(None)["id"], default_id)

    def test_disabled_provider_still_rejected(self):
        with self.assertRaises(HTTPException):
            main.get_api_provider("off")

    def test_chat_image_provider_unknown_rejected(self):
        with self.assertRaises(HTTPException):
            main.pick_chat_image_provider("deleted-provider", "custom-api")
        self.assertEqual(main.pick_chat_image_provider("", "comfly")["id"], "modelscope")


if __name__ == "__main__":
    unittest.main()
