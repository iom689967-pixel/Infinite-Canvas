"""C10：外壳页 index.html 的 /ws/stats 连接断开后要自动重连。
直接沿用公测线（ec4fdcc 起）已有的共用模块 static/js/workspace-socket.js，内容与 fork/beta 一致，
以后合并公测线时这部分不会冲突：断线按 0.5 秒起翻倍、最长 8 秒重连；联网后立即重连；
服务端以 1008 拒绝（鉴权失败）时不再重连；页面关闭时停止。
浏览器里的实际行为由 repro 场景 WS_shell_socket_reconnects_and_keeps_receiving_updates 验证。"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static/index.html").read_text(encoding="utf-8")


class ShellSocketReconnectTests(unittest.TestCase):
    def test_shell_uses_the_shared_reconnecting_socket(self):
        self.assertIn('<script src="/static/js/workspace-socket.js"></script>', HTML)
        self.assertIn("const ws = new MioWorkspaceSocket(`${protocol}://${host}/ws/stats?client_id=${CID}`);", HTML)
        self.assertNotIn("new WebSocket(", HTML)

    def test_instance_mode_serves_the_module(self):
        self.assertIn('"/static/js/workspace-socket.js"', (ROOT / "instance_access.py").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("node"), "node is required")
    def test_module_behaviour(self):
        # 公测线自带的行为测试：断线重连、退避、1008 不重连、pagehide 停止
        result = subprocess.run(["node", "tests/test_workspace_socket.js"], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
