import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


def reason(method, path, server_port=None, **headers):
    return main.cross_site_rejection_reason(method, path, {k.replace("_", "-").lower(): v for k, v in headers.items()}, server_port)


class CrossSiteGuardTests(unittest.TestCase):
    def test_foreign_origin_write_rejected(self):
        self.assertTrue(reason("PUT", "/api/providers", host="127.0.0.1:3000", origin="https://evil.example"))
        self.assertTrue(reason("POST", "/api/providers/fetch-models", host="127.0.0.1:3000", origin="https://evil.example"))

    def test_null_origin_rejected(self):
        self.assertTrue(reason("POST", "/api/canvases", host="127.0.0.1:3000", origin="null"))

    def test_own_service_and_extension_origins_allowed(self):
        # 同源、同一端口的本机别名（localhost / IPv6 回环）、浏览器扩展
        for origin in ("http://127.0.0.1:3000", "http://localhost:3000", "http://[::1]:3000", "chrome-extension://abcdef"):
            self.assertEqual(reason("POST", "/api/canvases", host="127.0.0.1:3000", origin=origin), "", origin)
            self.assertEqual(reason("POST", "/api/canvases", server_port=3000, host="127.0.0.1:3000", origin=origin), "", origin)

    def test_other_local_ports_rejected(self):
        # 本机其他端口上的网页（开发服务器、其他本地应用）不再被信任
        # （同端口不同协议的 https://127.0.0.1:3000 不可能是另一个服务，不在此列）
        for origin in ("http://localhost:5173", "http://127.0.0.1:8080", "http://localhost", "http://[::1]:8080"):
            self.assertTrue(reason("POST", "/api/canvases", server_port=3000, host="127.0.0.1:3000", origin=origin), origin)

    def test_server_port_wins_over_spoofed_host_port(self):
        # Host 头写成 8080，但服务实际监听 3000：localhost:8080 的页面仍被拒绝（同源规则除外）
        self.assertTrue(reason("POST", "/api/canvases", server_port=3000, host="localhost:3000", origin="http://localhost:8080"))

    def test_same_origin_lan_ip_allowed(self):
        self.assertEqual(reason("PUT", "/api/canvases/x", host="192.168.1.20:3000", origin="http://192.168.1.20:3000"), "")

    def test_non_browser_write_without_origin_allowed(self):
        # PS UXP 插件、脚本和单元测试不带 Origin/Referer。
        self.assertEqual(reason("POST", "/api/canvas-llm", host="owner.test"), "")

    def test_dns_rebinding_host_rejected(self):
        self.assertTrue(reason("GET", "/api/config/token", host="evil.example:3000"))
        self.assertTrue(reason("GET", "/api/providers/kie/fetch-models", host="evil.example:3000"))
        self.assertTrue(reason("POST", "/api/canvases", host="evil.example:3000", origin="http://evil.example:3000"))

    def test_all_get_and_websocket_host_checked(self):
        # DNS rebinding：读取画布、静态页、WebSocket 都要校验 Host
        for method, path in (("GET", "/api/canvases"), ("GET", "/static/canvas.html"), ("HEAD", "/"), ("WEBSOCKET", "/ws/stats")):
            self.assertTrue(reason(method, path, host="evil.example:3000"), (method, path))
        self.assertEqual(reason("GET", "/api/canvases", host="127.0.0.1:3000"), "")

    def test_ip_literals_and_missing_host_allowed(self):
        for host in ("[::1]:3000", "192.168.1.20:3000", "10.0.0.5", "localhost:3000", ""):
            self.assertEqual(reason("GET", "/api/canvases", host=host), "", host)

    def test_websocket_origin_checked(self):
        self.assertTrue(reason("WEBSOCKET", "/ws/stats", server_port=3000, host="127.0.0.1:3000", origin="https://evil.example"))
        self.assertEqual(reason("WEBSOCKET", "/ws/stats", server_port=3000, host="127.0.0.1:3000", origin="http://127.0.0.1:3000"), "")

    def test_cross_site_navigation_referer_does_not_block_get(self):
        self.assertEqual(reason("GET", "/static/index.html", host="127.0.0.1:3000", referer="https://some.site/page"), "")

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
