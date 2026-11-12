"""C10：GET /api/storage-files 不再返回服务器上的绝对路径。"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class StorageFilesListingTests(unittest.TestCase):
    def test_listing_has_no_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "sub"))
            with open(os.path.join(tmp, "sub", "a.png"), "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
            with patch.object(main, "storage_kind_dir", lambda kind: tmp):
                response = TestClient(main.app, base_url="http://127.0.0.1").get("/api/storage-files?kind=generated")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertNotIn("root", data)
            self.assertNotIn(tmp, json.dumps(data))
            self.assertNotIn(os.path.realpath(tmp), json.dumps(data))
            item = data["items"][0]
            self.assertEqual((item["rel"], item["folder"]), ("sub/a.png", "sub"))
            self.assertEqual(item["url"], "/api/storage-files/generated/sub/a.png")


if __name__ == "__main__":
    unittest.main()
