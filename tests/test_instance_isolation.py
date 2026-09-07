"""Real-process/HTTP acceptance; no production data, credentials or model traffic."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def clean_env():
    return {k: v for k, v in os.environ.items() if not k.startswith("INSTANCE_") and k != "PROGRAM_ROOT"}


def instance_env(root, name, port):
    return {**clean_env(), "INSTANCE_ID": name, "INSTANCE_DATA_ROOT": str(root),
            "INSTANCE_HOST": "127.0.0.1", "INSTANCE_PORT": str(port),
            # Deliberately contaminate the parent with fake credentials/paths.
            "COMFLY_API_KEY": "inherited-fake-must-disappear",
            "MODELSCOPE_API_KEY": "inherited-fake-must-disappear",
            "API_PROVIDER_GEMINI_KEY": "inherited-fake-must-disappear",
            "UNKNOWN_PROVIDER_SECRET": "inherited-fake-must-disappear",
            "CODEX_AUTH_FILE": str(ROOT / "API/never-read-auth.json")}


class MockUpstream(BaseHTTPRequestHandler):
    calls = []

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append((self.path, payload, self.headers.get("Authorization")))
        body = json.dumps({"choices": [{"message": {"content": "MOCK OK"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


# Seeds exercise production writer functions in each child, not parent monkeypatches.
# No test endpoints are added to the application.
CHILD = r'''
import main, os, json, tempfile
from pathlib import Path
from providers.kie.uploads import KieReferenceUploadCache
from PIL import Image
assert not os.getenv("UNKNOWN_PROVIDER_SECRET")
assert not main.AI_API_KEY and not main.MODELSCOPE_API_KEY
assert not os.getenv("API_PROVIDER_GEMINI_KEY") and not os.getenv("CODEX_AUTH_FILE")
root = Path(main.INSTANCE_DATA_ROOT)
name = main.INSTANCE_ID
generated = root / "assets/output/same-name.png"
seeded = root / ".runtime/test-seeded"
if not seeded.exists():
    Image.new("RGB", (32,32), "red" if name == "A" else "blue").save(generated)
    main.save_to_history({"timestamp": 100, "prompt": name, "images": ["/assets/output/same-name.png"]})
    seeded.write_text("done")
cache = KieReferenceUploadCache()
cache.store(name, "https://mock.invalid/"+name, size=4, mime_type="image/png")
with tempfile.NamedTemporaryFile(prefix="instance-test-", delete=False) as f:
    f.write(name.encode())
snapshot = {key: getattr(main,key) for key in ("PROGRAM_ROOT", "INSTANCE_ID", "INSTANCE_DATA_ROOT",
    "API_ENV_FILE", "API_PROVIDERS_FILE", "GLOBAL_CONFIG_FILE", "HISTORY_FILE", "DATA_DIR",
    "CONVERSATION_DIR", "CANVAS_DIR", "ASSETS_DIR", "OUTPUT_DIR", "WORKFLOW_DIR", "STATIC_DIR")}
snapshot.update(pid=os.getpid(), cache=str(cache.path), temp=f.name, home=os.environ["HOME"])
(root / ".runtime/test-paths.json").write_text(json.dumps(snapshot))
import uvicorn
uvicorn.run(main.app, host=main.INSTANCE_HOST, port=main.INSTANCE_PORT, log_level="warning")
'''


class InstanceConfigurationTests(unittest.TestCase):
    def run_config(self, root, extra=None, code="import instance_paths; print('READY')"):
        env = instance_env(root, "config", free_port())
        env.update(extra or {})
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=10)

    def test_missing_invalid_and_nonloopback_configuration_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for changes in ({"INSTANCE_ID": ""}, {"INSTANCE_DATA_ROOT": "relative"},
                            {"INSTANCE_ID": "../owner"}, {"INSTANCE_HOST": "0.0.0.0"},
                            {"INSTANCE_PORT": "no"}, {"INSTANCE_PORT": "0"},
                            {"INSTANCE_DATA_ROOT": str(ROOT)}, {"PROGRAM_ROOT": tmp}):
                with self.subTest(changes=changes):
                    result = self.run_config(Path(tmp) / "new", changes)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("READY", result.stdout)

    def test_unmarked_existing_data_and_symlink_root_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "existing"
            root.mkdir()
            (root / "owner-canary").write_text("untouched")
            self.assertNotEqual(self.run_config(root).returncode, 0)
            link = Path(tmp) / "link"
            link.symlink_to(root, target_is_directory=True)
            self.assertNotEqual(self.run_config(link).returncode, 0)
            self.assertEqual((root / "owner-canary").read_text(), "untouched")

    def test_identity_change_nested_root_and_startup_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            self.assertEqual(self.run_config(root).returncode, 0)
            self.assertNotEqual(self.run_config(root, {"INSTANCE_ID": "someone-else"}).returncode, 0)
            self.assertNotEqual(self.run_config(root / "nested").returncode, 0)
            (root / "data/link").symlink_to(Path(tmp), target_is_directory=True)
            self.assertNotEqual(self.run_config(root).returncode, 0)

    def test_data_root_cannot_nest_in_another_legacy_owner_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner = Path(tmp) / "owner"
            owner.mkdir()
            (owner / "main.py").write_text("# fake owner source")
            (owner / "VERSION").write_text("2026.09.08")
            result = self.run_config(owner / "assets/empty")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((owner / "assets").exists())

    def test_legacy_paths_and_environment_unchanged(self):
        code = "import os,instance_paths as p; assert not p.PATHS.explicit; assert p.INSTANCE_DATA_ROOT==p.PROGRAM_ROOT; assert os.getenv('COMFLY_API_KEY')=='fake'; print('OK')"
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                env={**clean_env(), "COMFLY_API_KEY": "fake"},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_protected_environment_cannot_be_overridden_by_instance_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            self.assertEqual(self.run_config(root).returncode, 0)
            (root / "API/.env").write_text("INSTANCE_DATA_ROOT=/not-allowed\n")
            result = self.run_config(root, code="import main")
            self.assertNotEqual(result.returncode, 0)

    def test_release_manifest_includes_path_module_without_version_bump(self):
        manifest = json.loads((ROOT / "release-manifest.json").read_text())
        self.assertIn("instance_paths.py", manifest["include"])
        self.assertEqual(manifest["version"], (ROOT / "VERSION").read_text().strip())

    def test_io_guard_blocks_secret_fallback_writes_and_native_subprocesses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "instance"
            outside = Path(tmp) / "fake-owner.env"
            outside.write_text("FAKE_SECRET=must-not-load")
            code = f'''
import instance_paths as p, subprocess, sys, os
from pathlib import Path
operations = [lambda: Path({str(outside)!r}).read_text(),
              lambda: Path({str(outside)!r}).write_text("overwrite"),
              lambda: subprocess.run([sys.executable, "-c", "print('unsafe')"]),
              lambda: os.remove(p.PATHS.data_root / ".instance.lock"),
              lambda: Path(p.PROGRAM_ROOT, "API/.env").read_text()]
for operation in operations:
    try:
        operation()
    except p.InstanceBoundaryError:
        continue
    raise AssertionError("boundary did not reject operation")
print("READY")
'''
            result = self.run_config(root, code=code)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(outside.read_text(), "FAKE_SECRET=must-not-load")

    def test_server_configuration_cannot_be_overridden_by_uvicorn_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_config(Path(tmp) / "root", code="import instance_paths,socket; s=socket.socket(); s.bind(('0.0.0.0', 0))")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("configured loopback", result.stderr)


class TwoProcessIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="canvas-two-process-")
        cls.root = Path(cls.temp.name).resolve()
        cls.roots = {name: cls.root / name for name in ("A", "B")}
        cls.ports = {name: free_port() for name in ("A", "B")}
        while cls.ports["A"] == cls.ports["B"]:
            cls.ports["B"] = free_port()
        cls.mock = ThreadingHTTPServer(("127.0.0.1", 0), MockUpstream)
        cls.thread = threading.Thread(target=cls.mock.serve_forever, daemon=True)
        cls.thread.start()
        cls.procs, cls.logs = {}, {}
        try:
            for name in ("A", "B"):
                cls.start(name)
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def start(cls, name):
        port = cls.ports[name]
        env = instance_env(cls.roots[name], name, port)
        env["INSTANCE_MOCK_UPSTREAMS"] = f"127.0.0.1:{cls.mock.server_port}"
        log = open(cls.root / f"{name}-process.log", "a+")
        cls.logs[name] = log
        proc = subprocess.Popen([sys.executable, "-c", CHILD], cwd=ROOT, env=env,
                                stdout=log, stderr=log)
        cls.procs[name] = proc
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                log.flush()
                raise AssertionError((cls.root / f"{name}-process.log").read_text())
            try:
                if cls.request(name, "GET", "/api/canvases").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(.05)
        raise AssertionError(f"Instance {name} failed to start")

    @classmethod
    def stop(cls, name):
        proc = cls.procs.get(name)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if name in cls.logs:
            cls.logs[name].close()

    @classmethod
    def tearDownClass(cls):
        for name in ("A", "B"):
            cls.stop(name)
        cls.mock.shutdown()
        cls.mock.server_close()
        cls.thread.join(timeout=2)
        cls.temp.cleanup()

    @classmethod
    def request(cls, name, method, path, **kwargs):
        with httpx.Client(trust_env=False, timeout=5) as client:
            return client.request(method, f"http://127.0.0.1:{cls.ports[name]}{path}", **kwargs)

    def ok(self, name, method, path, **kwargs):
        response = self.request(name, method, path, **kwargs)
        self.assertEqual(response.status_code, 200, response.text[:1500])
        return response.json()

    def canvas(self, name, title):
        return self.ok(name, "POST", "/api/canvases", json={"title": title, "kind": "smart"})["canvas"]

    def test_canvas_ids_are_unreadable_and_undeletable_in_other_process(self):
        canvas = self.canvas("A", "only-A")
        b_list = self.ok("B", "GET", "/api/canvases")["canvases"]
        self.assertNotIn(canvas["id"], [c["id"] for c in b_list])
        for method in ("GET", "DELETE", "PUT"):
            kwargs = {"json": {"title": "attack"}} if method == "PUT" else {}
            self.assertEqual(self.request("B", method, "/api/canvases/"+canvas["id"], **kwargs).status_code, 404)
        self.assertEqual(self.ok("A", "GET", "/api/canvases/"+canvas["id"])["canvas"]["title"], "only-A")

    def test_browser_parameters_cannot_select_another_instance(self):
        response = self.ok("A", "POST", "/api/canvases?INSTANCE_ID=B&INSTANCE_DATA_ROOT="+str(self.roots["B"]),
                           json={"title": "server-owned", "instance_id": "B", "data_root": str(self.roots["B"])},
                           headers={"x-instance-id": "B", "x-instance-data-root": str(self.roots["B"])})
        canvas_id = response["canvas"]["id"]
        self.assertTrue((self.roots["A"] / f"data/canvases/{canvas_id}.json").exists())
        self.assertFalse((self.roots["B"] / f"data/canvases/{canvas_id}.json").exists())

    def test_asset_libraries_are_independent(self):
        for name in ("A", "B"):
            self.ok(name, "POST", "/api/asset-library/libraries", json={"name": name+"-assets"})
        for name, other in (("A", "B"), ("B", "A")):
            result = self.ok(name, "GET", "/api/asset-library")
            self.assertIn(name+"-assets", json.dumps(result))
            self.assertNotIn(other+"-assets", json.dumps(result))

    def test_local_import_cannot_read_other_instance(self):
        response = self.request("A", "POST", "/api/ai/import-local-image",
                                json={"path": str(self.roots["B"] / "assets/output/same-name.png")},
                                headers={"origin": f"http://127.0.0.1:{self.ports['A']}"})
        self.assertEqual(response.status_code, 403, response.text)

    def test_concurrent_saves_use_separate_files(self):
        canvases = {name: self.canvas(name, name) for name in ("A", "B")}
        def save(name):
            for i in range(5):
                self.ok(name, "PUT", "/api/canvases/"+canvases[name]["id"],
                        json={"title": f"{name}-{i}", "nodes": [{"id": name}]})
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(save, ("A", "B")))
        for name in ("A", "B"):
            stored = json.loads((self.roots[name] / f"data/canvases/{canvases[name]['id']}.json").read_text())
            self.assertEqual(stored["title"], f"{name}-4")
            self.assertEqual(stored["nodes"], [{"id": name}])

    def test_upload_static_preview_and_download_do_not_cross_instances(self):
        image = BytesIO()
        Image.new("RGB", (32, 32), "green").save(image, format="PNG")
        payload = {"data": base64.b64encode(image.getvalue()).decode(), "name": "ref.png", "content_type": "image/png"}
        url = self.ok("A", "POST", "/api/ai/upload-base64", json=payload)["files"][0]["url"]
        response = self.request("A", "GET", url)
        self.assertEqual(response.status_code, 200, (self.roots["A"] / ".runtime/logs/server.log").read_text())
        self.assertEqual(response.content, image.getvalue())
        self.assertEqual(self.request("B", "GET", url).status_code, 404)
        for endpoint in ("/api/media-preview", "/api/download-output"):
            self.assertEqual(self.request("A", "GET", endpoint, params={"url": url}).status_code, 200)
            self.assertIn(self.request("B", "GET", endpoint, params={"url": url}).status_code, (400, 404))
        self.assertTrue(list((self.roots["A"] / "data/media_previews").glob("*")))
        # An absolute HTTP URL must not turn B into a proxy to A or the owner.
        result = self.request("B", "GET", "/api/download-output",
                              params={"url": f"http://127.0.0.1:{self.ports['A']}{url}"})
        self.assertGreaterEqual(result.status_code, 400)

    def test_storage_shared_export_absolute_traversal_and_symlink_are_blocked(self):
        outside = self.roots["B"] / "assets/output"
        for path in (str(outside), "../B/assets/output", str(ROOT / "assets")):
            for method, endpoint, payload in (
                ("PATCH", "/api/storage-settings", {"generated": path}),
                ("POST", "/api/shared-folders", {"path": path}),
                ("POST", "/api/smart-canvas/group-export", {"folder": path, "items": [{"kind": "text", "text": "forbidden"}]}),
            ):
                with self.subTest(endpoint=endpoint, path=path):
                    self.assertIn(self.request("A", method, endpoint, json=payload).status_code, (400, 403))
        link = self.roots["A"] / "assets/escape"
        link.symlink_to(outside, target_is_directory=True)
        try:
            for endpoint, params in (("/assets/escape/same-name.png", {}),
                                     ("/api/media-preview", {"url": "/assets/escape/same-name.png"}),
                                     ("/api/download-output", {"url": "/assets/escape/same-name.png"})):
                self.assertIn(self.request("A", "GET", endpoint, params=params).status_code, (400, 401, 403, 404))
        finally:
            link.unlink()

    def test_custom_storage_urls_exports_and_shared_folders_work_inside_instance(self):
        folder = self.roots["A"] / "custom-storage"
        self.ok("A", "PATCH", "/api/storage-settings", json={"generated": str(folder)})
        try:
            exported = self.ok("A", "POST", "/api/smart-canvas/group-export",
                               json={"folder": str(folder), "items": [{"kind": "text", "name": "note", "text": "A-only"}]})
            self.assertEqual(exported["count"], 1)
            self.assertEqual(self.request("A", "GET", "/api/storage-files/generated/note.txt").text, "A-only")
            self.assertEqual(self.request("B", "GET", "/api/storage-files/generated/note.txt").status_code, 404)
            shared = self.ok("A", "POST", "/api/shared-folders", json={"path": str(folder)})["folder"]
            self.assertEqual(self.request("B", "GET", "/api/shared-folders/"+shared["id"]+"/tree").status_code, 404)
            self.assertIn(self.request("A", "GET", "/api/shared-folders/"+shared["id"]+"/file",
                                       params={"path": "../../B/assets/output/same-name.png"}).status_code, (400, 403))
        finally:
            self.ok("A", "PATCH", "/api/storage-settings", json={})

    def test_prompt_projects_conversations_and_custom_workflows_are_independent(self):
        for name in ("A", "B"):
            self.ok(name, "POST", "/api/prompt-libraries", json={"name": name+"-prompt"})
            self.ok(name, "POST", "/api/projects", json={"name": name+"-project"})
            self.ok(name, "POST", "/api/conversations", json={"title": name+"-chat"}, headers={"x-user-id": "same-browser"})
            self.ok(name, "POST", "/api/workflows", json={"name": "same.json", "workflow": {"1": {"class_type": name}}})
        for name, other in (("A", "B"), ("B", "A")):
            for endpoint, forbidden in (("/api/prompt-libraries", other+"-prompt"),
                                        ("/api/projects", other+"-project"),
                                        ("/api/conversations", other+"-chat")):
                result = self.ok(name, "GET", endpoint, headers={"x-user-id": "same-browser"})
                self.assertNotIn(forbidden, json.dumps(result))
            self.assertEqual(self.ok(name, "GET", "/api/workflows/custom/same.json")["workflow"]["1"]["class_type"], name)

    def test_builtin_workflow_config_is_an_instance_override(self):
        source = ROOT / "workflows/MiniMax_H3.config.json"
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        self.ok("A", "PUT", "/api/workflows/MiniMax_H3.json/config", json={"title": "A override"})
        self.assertEqual(self.ok("A", "GET", "/api/workflows/MiniMax_H3.json")["config"]["title"], "A override")
        self.assertNotEqual(self.ok("B", "GET", "/api/workflows/MiniMax_H3.json")["config"]["title"], "A override")
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        self.assertEqual(self.request("A", "DELETE", "/api/workflows/MiniMax_H3.json").status_code, 400)

    def test_history_and_kie_cache_use_instance_paths(self):
        for name, other in (("A", "B"), ("B", "A")):
            history = self.ok(name, "GET", "/api/history")
            self.assertTrue(all(item["prompt"] == name for item in history))
            cache = json.loads((self.roots[name] / "data/kie_reference_cache.json").read_text())
            self.assertIn(name, cache["entries"])
            self.assertNotIn(other, cache["entries"])

    def test_media_cleanup_in_a_preserves_b_same_name_file(self):
        before = (self.roots["B"] / "assets/output/same-name.png").read_bytes()
        self.ok("A", "POST", "/api/history/delete", json={"timestamp": 100})
        self.assertFalse((self.roots["A"] / "assets/output/same-name.png").exists())
        self.assertEqual((self.roots["B"] / "assets/output/same-name.png").read_bytes(), before)

    def test_log_media_cleanup_uses_only_its_instance(self):
        for name in ("A", "B"):
            Image.new("RGB", (16, 16), "red" if name == "A" else "blue").save(self.roots[name] / "assets/output/log-cleanup.png")
        before = (self.roots["B"] / "assets/output/log-cleanup.png").read_bytes()
        canvas = self.canvas("A", "log-cleanup")
        self.ok("A", "PUT", "/api/canvases/"+canvas["id"],
                json={"logs": [{"id": "log-one", "outputs": ["/assets/output/log-cleanup.png"]}]})
        result = self.ok("A", "POST", "/api/canvases/"+canvas["id"]+"/logs/delete",
                         json={"log_id": "log-one", "delete_unreferenced_media": True})
        self.assertIn("log-cleanup.png", result["removed_files"])
        self.assertFalse((self.roots["A"] / "assets/output/log-cleanup.png").exists())
        self.assertEqual((self.roots["B"] / "assets/output/log-cleanup.png").read_bytes(), before)

    def test_process_paths_temp_home_and_credentials_are_isolated(self):
        snapshots = []
        for name in ("A", "B"):
            snapshot = json.loads((self.roots[name] / ".runtime/test-paths.json").read_text())
            snapshots.append(snapshot)
            self.assertEqual(snapshot["PROGRAM_ROOT"], str(ROOT))
            self.assertEqual(snapshot["STATIC_DIR"], str(ROOT / "static"))
            for key, value in snapshot.items():
                if key not in {"PROGRAM_ROOT", "STATIC_DIR", "INSTANCE_ID", "pid"}:
                    self.assertTrue(Path(value).is_relative_to(self.roots[name]), (key, value))
            config = self.ok(name, "GET", "/api/config")
            self.assertFalse(config["has_api_key"])
            self.assertFalse(config["has_ms_key"])
        self.assertNotEqual(snapshots[0]["pid"], snapshots[1]["pid"])

    def test_mock_llm_uses_only_explicit_fake_provider(self):
        for name in ("A", "B"):
            providers = [{"id": "mock", "name": "Mock", "protocol": "openai", "enabled": True,
                          "base_url": f"http://127.0.0.1:{self.mock.server_port}/v1",
                          "chat_models": ["mock-chat"], "api_key": "fake-"+name}]
            self.ok(name, "PUT", "/api/providers", json=providers)
            response = self.ok(name, "POST", "/api/canvas-llm",
                               json={"provider": "mock", "model": "mock-chat", "message": "reply OK only", "images": [], "videos": []})
            self.assertIn("MOCK OK", json.dumps(response))
            self.assertIn("fake-"+name, (self.roots[name] / "API/.env").read_text())
        self.assertEqual({c[2] for c in MockUpstream.calls}, {"Bearer fake-A", "Bearer fake-B"})

    def test_update_rollback_and_cli_controls_are_disabled(self):
        for method, path in (("GET", "/api/check-update"), ("GET", "/api/update-backups"),
                             ("POST", "/api/update-from-github"), ("POST", "/api/update-rollback"),
                             ("GET", "/api/jimeng/status")):
            self.assertEqual(self.request("A", method, path, json={}).status_code, 403)

    def test_same_root_second_process_is_rejected(self):
        env = instance_env(self.roots["A"], "A", free_port())
        proc = subprocess.run([sys.executable, "-c", "import main"], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already in use", proc.stderr)

    def test_z_restart_preserves_each_instance(self):
        canvases = {name: self.canvas(name, "persistent-"+name) for name in ("A", "B")}
        for name in ("A", "B"):
            self.ok(name, "POST", "/api/prompt-libraries", json={"name": "persistent-prompt-"+name})
            self.ok(name, "POST", "/api/workflows", json={"name": "persistent.json", "workflow": {"1": {"class_type": name}}})
        for name in ("A", "B"):
            old_pid = self.procs[name].pid
            self.stop(name)
            self.start(name)
            self.assertNotEqual(self.procs[name].pid, old_pid)
            self.assertEqual(self.ok(name, "GET", "/api/canvases/"+canvases[name]["id"])["canvas"]["title"], "persistent-"+name)
            self.assertIn(name, json.loads((self.roots[name] / "data/kie_reference_cache.json").read_text())["entries"])
            self.assertIn("persistent-prompt-"+name, json.dumps(self.ok(name, "GET", "/api/prompt-libraries")))
            self.assertEqual(self.ok(name, "GET", "/api/workflows/custom/persistent.json")["workflow"]["1"]["class_type"], name)


if __name__ == "__main__":
    unittest.main()
