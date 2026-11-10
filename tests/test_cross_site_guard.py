import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


def reason(method, path, **headers):
    return main.cross_site_rejection_reason(method, path, {k.replace("_", "-").lower(): v for k, v in headers.items()})


class CrossSiteGuardTests(unittest.TestCase):
    def test_foreign_origin_write_rejected(self):
        self.assertTrue(reason("PUT", "/api/providers", host="127.0.0.1:3000", origin="https://evil.example"))
        self.assertTrue(reason("POST", "/api/providers/fetch-models", host="127.0.0.1:3000", origin="https://evil.example"))

    def test_null_origin_rejected(self):
        self.assertTrue(reason("POST", "/api/canvases", host="127.0.0.1:3000", origin="null"))

    def test_local_and_extension_origins_allowed(self):
        for origin in ("http://127.0.0.1:3000", "http://localhost:5173", "http://[::1]:3000", "chrome-extension://abcdef"):
            self.assertEqual(reason("POST", "/api/canvases", host="127.0.0.1:3000", origin=origin), "", origin)

    def test_same_origin_lan_ip_allowed(self):
        self.assertEqual(reason("PUT", "/api/canvases/x", host="192.168.1.20:3000", origin="http://192.168.1.20:3000"), "")

    def test_non_browser_write_without_origin_allowed(self):
        # PS UXP 插件、脚本和单元测试不带 Origin/Referer。
        self.assertEqual(reason("POST", "/api/canvas-llm", host="owner.test"), "")

    def test_dns_rebinding_host_rejected(self):
        self.assertTrue(reason("GET", "/api/config/token", host="evil.example:3000"))
        self.assertTrue(reason("GET", "/api/providers/kie/fetch-models", host="evil.example:3000"))
        self.assertTrue(reason("POST", "/api/canvases", host="evil.example:3000", origin="http://evil.example:3000"))

    def test_plain_get_not_guarded(self):
        self.assertEqual(reason("GET", "/api/canvases", host="evil.example:3000"), "")

    def test_referer_checked_when_origin_missing(self):
        self.assertTrue(reason("POST", "/api/canvases", host="127.0.0.1:3000", referer="https://evil.example/page"))
        self.assertEqual(reason("POST", "/api/canvases", host="127.0.0.1:3000", referer="http://127.0.0.1:3000/static/canvas.html"), "")

    def test_env_allow_lists(self):
        with patch.dict(os.environ, {"INFINITE_CANVAS_ALLOWED_ORIGINS": "https://tools.example", "INFINITE_CANVAS_ALLOWED_HOSTS": "mac.local"}):
            self.assertEqual(reason("POST", "/api/canvases", host="127.0.0.1:3000", origin="https://tools.example"), "")
            self.assertEqual(reason("GET", "/api/providers", host="mac.local:3000"), "")


class MaskedTokenTests(unittest.TestCase):
    def test_masked_explicit_key_falls_back_to_server_key(self):
        with patch.object(main, "provider_env_key_value", return_value="ms-server-key-9999"):
            self.assertEqual(main.modelscope_api_key(main.mask_secret("ms-server-key-9999")), "ms-server-key-9999")
            self.assertEqual(main.modelscope_api_key("ms-explicit"), "ms-explicit")

    def test_legacy_global_config_token_still_used(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "global_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"modelscope_token": "ms-legacy-1234"}, f)
            with patch.object(main, "GLOBAL_CONFIG_FILE", path), patch.object(main, "provider_env_key_value", return_value=""), patch.object(main, "MODELSCOPE_API_KEY", ""):
                self.assertEqual(main.modelscope_api_key(), "ms-legacy-1234")


if __name__ == "__main__":
    unittest.main()
