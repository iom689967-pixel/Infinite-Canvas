"""D：删除确认无人调用的两个路由。替代它们的路由保留：
- POST /api/temp-sh/upload → POST /api/cloud-video/upload（界面上的"上传到云端"按钮）
- GET /api/providers/{id}/fetch-models → POST /api/providers/fetch-models（设置页按表单值拉取模型）
smart-canvas/group-export 和 asset-library/items/crop 按决定保留。"""
import unittest

from fastapi.testclient import TestClient

import instance_access
import main


def routes():
    out = set()
    for route in main.app.routes:
        for method in getattr(route, "methods", None) or ():
            out.add(f"{method} {route.path}")
    return out


class UnusedRoutesRemovedTests(unittest.TestCase):
    def test_removed_routes_are_gone_and_replacements_stay(self):
        current = routes()
        for gone in ("POST /api/temp-sh/upload", "GET /api/providers/{provider_id}/fetch-models"):
            self.assertNotIn(gone, current)
            self.assertNotIn(gone, instance_access.ROUTE_ACCESS)
        for kept in ("POST /api/cloud-video/upload", "POST /api/providers/fetch-models",
                     "POST /api/smart-canvas/group-export", "POST /api/asset-library/items/crop"):
            self.assertIn(kept, current)

    def test_removed_routes_answer_404_or_405(self):
        client = TestClient(main.app, base_url="http://127.0.0.1")
        self.assertIn(client.post("/api/temp-sh/upload", json={"url": "/x.mp4"}).status_code, {404, 405})
        self.assertIn(client.get("/api/providers/kie/fetch-models").status_code, {404, 405})

    def test_helpers_used_only_by_them_are_gone(self):
        for name in ("TempShUploadRequest", "upload_local_video_to_temp_sh", "fetch_upstream_models"):
            self.assertFalse(hasattr(main, name), name)
        self.assertTrue(hasattr(main, "upload_video_to_temp_sh"))  # 云端上传的 temp.sh 备选仍在用


if __name__ == "__main__":
    unittest.main()
