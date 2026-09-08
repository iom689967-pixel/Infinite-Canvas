"""Authentication acceptance uses real independent HTTP/WebSocket servers and local admin."""
import asyncio
import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import unittest

import httpx
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from instance_access import ROUTE_ACCESS, WORKBENCH_STATIC
from instance_auth import AuthStore, password_hash, verify_password
from test_instance_isolation import ROOT, clean_env, instance_env, free_port
import test_instance_isolation as isolation


class AuthenticationUnitTests(unittest.TestCase):
    def test_scrypt_hash_is_salted_and_uses_reviewed_parameters(self):
        password = secrets.token_urlsafe(24)
        first, second = password_hash(password), password_hash(password)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("scrypt$131072$8$1$"))
        self.assertTrue(verify_password(password, first))
        self.assertFalse(verify_password(secrets.token_urlsafe(24), first))
        self.assertFalse(password in first, "plaintext password must not be stored")

    def test_route_inventory_has_explicit_classification_and_no_automatic_allow(self):
        actual = set()
        for node in ast.parse((ROOT / "main.py").read_text()).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "app" and decorator.func.attr in {"get", "post", "put", "delete", "patch", "websocket"}:
                    actual.add(decorator.func.attr.upper()+" "+ast.literal_eval(decorator.args[0]))
        self.assertEqual(actual, set(ROUTE_ACCESS))
        self.assertNotIn("GET /api/new-unreviewed-route", ROUTE_ACCESS)
        self.assertNotIn("/static/api-settings.html", WORKBENCH_STATIC)
        self.assertNotIn("/static/runninghub/api_providers.json", WORKBENCH_STATIC)

    def test_no_auth_database_means_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = instance_env(Path(tmp)/"new", "missing-auth", free_port())
            proc = subprocess.run([sys.executable, "-c", "import main"], cwd=ROOT, env=env,
                                  capture_output=True, text=True, timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("禁止匿名启动", proc.stderr)

    def test_http_exception_is_opt_in_and_secure_cookie_is_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = instance_env(Path(tmp)/"new", "transport", free_port())
            env.pop("INSTANCE_AUTH_ALLOW_HTTP_LOOPBACK")
            code = "import instance_paths as p; assert not p.PATHS.auth_http_loopback"
            self.assertEqual(subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                                           capture_output=True, timeout=10).returncode, 0)
        from instance_http import InstanceAuthMiddleware
        from types import SimpleNamespace
        middleware = InstanceAuthMiddleware(None, routes=[], paths=SimpleNamespace(auth_http_loopback=False, host="127.0.0.1", port=43101),
                                            store=SimpleNamespace(digest=AuthStore.digest, instance_id="test", root="/tmp/test"), catalog=None)
        self.assertTrue(middleware.secure)
        self.assertTrue(middleware.cookie_name.startswith("__Host-"))

    def test_auth_paths_cannot_enter_release_replacement_set(self):
        import main
        for path in (".auth/access.sqlite3", ".auth/access.sqlite3-journal", ".instance.json", ".instance.lock"):
            self.assertFalse(main.update_allowed_file(path))
        manifest = json.loads((ROOT / "release-manifest.json").read_text())
        self.assertIn(".auth/", manifest["protected"])
        self.assertNotIn(".auth/", manifest["include"])

    def test_empty_account_database_and_unsafe_password_input_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "account"
            command = [sys.executable, str(ROOT / "instance_admin.py"), "--data-root", str(root), "--instance-id", "assistant01"]
            initialized = subprocess.run([*command, "init"], env=clean_env(), capture_output=True, timeout=10)
            self.assertEqual(initialized.returncode, 0)
            store = AuthStore(root, "assistant01")
            with self.assertRaisesRegex(RuntimeError, "唯一的 assistant"):
                store.start_server()
            refused = subprocess.run([*command, "create", "--username", "assistant01"], env=clean_env(),
                                     input=b"", capture_output=True, timeout=10)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("需要可隐藏输入的终端", refused.stderr.decode())
            self.assertEqual((root / ".auth").stat().st_mode & 0o777, 0o700)
            self.assertEqual(store.db_path.stat().st_mode & 0o777, 0o600)

    def test_invalid_auth_startup_configuration_never_falls_back_to_legacy(self):
        for settings in ({"INSTANCE_AUTH_ALLOW_HTTP_LOOPBACK":"yes"},
                         {"INSTANCE_SESSION_TTL_SECONDS":"0"},
                         {"INSTANCE_SESSION_TTL_SECONDS":"86401"},
                         {"INSTANCE_HOST":"0.0.0.0"}):
            with self.subTest(settings=settings), tempfile.TemporaryDirectory() as tmp:
                env = instance_env(Path(tmp)/"invalid", "invalid-auth", free_port())
                env.update(settings)
                proc = subprocess.run([sys.executable, "-c", "import instance_paths"], cwd=ROOT, env=env,
                                      capture_output=True, timeout=10)
                self.assertNotEqual(proc.returncode, 0)


class AuthHTTPTests(unittest.TestCase):
    # Reuse fixture mechanics only, not copied/skipped assertions or a test-only auth bypass.
    setUpClass = classmethod(isolation.TwoProcessIsolationTests.setUpClass.__func__)
    tearDownClass = classmethod(isolation.TwoProcessIsolationTests.tearDownClass.__func__)
    start = classmethod(isolation.TwoProcessIsolationTests.start.__func__)
    stop = classmethod(isolation.TwoProcessIsolationTests.stop.__func__)
    request = classmethod(isolation.TwoProcessIsolationTests.request.__func__)
    ok = isolation.TwoProcessIsolationTests.ok
    canvas = isolation.TwoProcessIsolationTests.canvas

    def origin(self, name="A"):
        return f"http://127.0.0.1:{self.ports[name]}"

    def raw(self, name, method, path, **kwargs):
        with httpx.Client(trust_env=False, timeout=10) as client:
            return client.request(method, self.origin(name)+path, **kwargs)

    def login(self, name="A"):
        with httpx.Client(trust_env=False, timeout=10) as client:
            result = client.post(self.origin(name)+"/api/auth/login", headers={"Origin":self.origin(name)},
                                 json={"username":name, "password":self.passwords[name]})
            self.assertEqual(result.status_code, 200)
            me = client.get(self.origin(name)+"/api/auth/me").json()
            return dict(client.cookies), me, result

    def session_headers(self, cookies, me, name="A"):
        return {"Cookie":"; ".join(k+"="+v for k,v in cookies.items()), "Origin":self.origin(name), "X-CSRF-Token":me["csrf"]}

    def ws(self, name, cookies, origin=None):
        return connect(f"ws://127.0.0.1:{self.ports[name]}/ws/stats?client_id=forged", origin=origin or self.origin(name),
                       additional_headers={"Cookie":"; ".join(k+"="+v for k,v in cookies.items())},
                       proxy=None, open_timeout=3, close_timeout=1, ping_interval=None)

    def admin(self, name, action, password=None):
        args = [sys.executable, str(ROOT/"instance_admin.py"), "--data-root", str(self.roots[name]),
                "--instance-id", name, action, "--username", name]
        if password is not None:
            args.append("--password-stdin")
        result = subprocess.run(args, input=password, text=True, capture_output=True, cwd=ROOT, env=clean_env(), timeout=10)
        self.assertEqual(result.returncode, 0, "local administration must succeed without restarting the instance")

    def test_anonymous_navigation_api_media_download_and_ws_are_rejected(self):
        response = self.raw("A", "GET", "/", headers={"Accept":"text/html"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")
        self.assertEqual(self.raw("A", "GET", "/login").status_code, 200)
        self.assertEqual(self.raw("A", "GET", "/healthz").json(), {"ok":True})
        for path in ("/api/canvases", "/assets/output/same-name.png", "/output/test.png", "/api/download-output?url=/assets/output/same-name.png", "/static/js/smart-canvas.js", "/api/auth/me"):
            response = self.raw("A", "GET", path)
            self.assertEqual(response.status_code, 401, path)
            self.assertIn("application/json", response.headers["content-type"])
        with self.assertRaises(InvalidStatus):
            self.ws("A", {})

    def test_cookie_rotation_expiry_attributes_and_private_cache(self):
        cookies, me, response = self.login()
        header = response.headers["set-cookie"]
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=strict", header)
        self.assertIn("Max-Age=28800", header)
        self.assertNotIn("Domain=", header)
        self.assertNotIn("Secure", header)  # Only this explicitly configured loopback fixture.
        result = self.raw("A", "POST", "/api/auth/login", headers=self.session_headers(cookies, me),
                          json={"username":"A", "password":self.passwords["A"]})
        self.assertEqual(result.status_code, 200)
        self.assertNotEqual(dict(result.cookies), cookies)
        self.assertEqual(self.raw("A", "GET", "/api/auth/me", headers=self.session_headers(cookies, me)).status_code, 401)
        for path in ("/", "/api/canvases", "/assets/output/same-name.png"):
            self.assertIn("no-store", self.request("A", "GET", path).headers["cache-control"])

    def test_wrong_password_forged_cookie_and_cross_instance_cookie_fail(self):
        bad = self.raw("A", "POST", "/api/auth/login", headers={"Origin":self.origin()}, json={"username":"A", "password":secrets.token_urlsafe(24)})
        unknown = self.raw("A", "POST", "/api/auth/login", headers={"Origin":self.origin()}, json={"username":"not-an-account", "password":secrets.token_urlsafe(24)})
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(bad.json(), unknown.json())
        cookies, me, _ = self.login()
        b_name = next(iter(self.cookies["B"]))
        for cookie in ({b_name:next(iter(cookies.values()))}, {b_name:secrets.token_urlsafe(32)}):
            self.assertEqual(self.raw("B", "GET", "/api/auth/me", headers={"Cookie":"; ".join(k+"="+v for k,v in cookie.items())}).status_code, 401)

    def test_assistant_cannot_access_management_or_private_configuration_files(self):
        for method,path,payload in (("GET","/api/config/token",None), ("PUT","/api/providers",[]),
                                    ("GET","/api/storage-settings",None), ("PATCH","/api/storage-settings",{}),
                                    ("POST","/api/update-from-github",{}), ("POST","/api/update-rollback",{}),
                                    ("POST","/api/shared-folders",{"path":str(self.roots["A"])}),
                                    ("GET","/static/api-settings.html",None), ("GET","/static/runninghub/api_providers.json",None),
                                    ("GET","/docs",None), ("GET","/openapi.json",None), ("GET","/api/new-unreviewed-route",None)):
            self.assertEqual(self.request("A", method, path, json=payload).status_code, 403, path)
        for key in ("base_url", "key_env", "api_key", "key_mask", "wallet_api_key"):
            self.assertNotIn(key, json.dumps(self.ok("A", "GET", "/api/providers")))
        private = self.roots["A"] / ".auth/access.sqlite3"
        before = private.read_bytes()
        self.assertEqual(self.request("A", "POST", "/api/smart-canvas/group-export",
                                     json={"folder":str(private.parent), "items":[{"kind":"text","name":"access.sqlite3","text":"overwrite"}]}).status_code, 403)
        link = self.roots["A"] / "assets/auth-link"
        link.symlink_to(private.parent, target_is_directory=True)
        try:
            for path in ("/assets/auth-link/access.sqlite3", "/api/download-output?url=/assets/auth-link/access.sqlite3"):
                self.assertIn(self.request("A", "GET", path).status_code, (401,403,404))
        finally:
            link.unlink()
        self.assertEqual(private.read_bytes(), before)

    def test_spoofed_role_user_and_instance_do_not_change_identity(self):
        result = self.ok("A", "GET", "/api/auth/me?role=admin&user_id=B&instance_id=B",
                         headers={"X-User-ID":"B", "X-Role":"admin", "X-Instance-ID":"B"})
        self.assertEqual((result["username"], result["role"], result["instance_id"]), ("A","assistant","A"))
        result = self.ok("A", "POST", "/api/conversations", json={"title":"bound-to-session"}, headers={"X-User-ID":"B"})
        self.assertEqual(self.request("B", "GET", "/api/conversations/"+result["conversation"]["id"]).status_code, 404)

    def test_csrf_origin_proxy_headers_cors_and_upload_form_are_enforced(self):
        cookies, me, _ = self.login()
        good = self.session_headers(cookies, me)
        for changes in ({"X-CSRF-Token":""}, {"Origin":""}, {"Origin":self.origin("B")},
                        {"Origin":self.origin().replace("http:","https:")}, {"X-CSRF-Token":self.csrf["A"]},
                        {"X-Forwarded-For":"198.51.100.7"}, {"Forwarded":"for=198.51.100.7"}):
            headers = {**good, **changes}
            for path in ("/api/canvases", "/api/auth/logout", "/api/canvas-llm/cancel"):
                self.assertEqual(self.raw("A", "POST", path, headers=headers, json={"request_id":"forged"}).status_code, 403)
        self.assertEqual(self.raw("A", "POST", "/api/ai/upload", headers={**good,"X-CSRF-Token":""}, files={"files":("test.txt",b"temporary")}).status_code, 403)
        response = self.raw("A", "OPTIONS", "/api/canvases", headers={"Origin":self.origin("B"), "Access-Control-Request-Method":"POST"})
        self.assertNotIn("access-control-allow-origin", response.headers)
        with self.assertRaises(InvalidStatus):
            self.ws("A", cookies, self.origin("B"))

    def test_logout_revokes_existing_websocket_before_further_private_messages(self):
        cookies, me, _ = self.login()
        with self.ws("A", cookies) as ws:
            self.assertIn("stats", ws.recv(timeout=2))
            result = self.raw("A", "POST", "/api/auth/logout", headers=self.session_headers(cookies,me))
            self.assertEqual(result.status_code, 200)
            with self.assertRaises(ConnectionClosed):
                ws.recv(timeout=2)
        self.assertEqual(self.raw("A", "GET", "/api/canvases", headers=self.session_headers(cookies,me)).status_code, 401)

    def test_expired_session_revokes_http_and_live_websocket(self):
        cookies, me, _ = self.login()
        store = AuthStore(self.roots["A"], "A")
        with self.ws("A", cookies) as ws:
            ws.recv(timeout=2)
            # Advancing a persisted session's expiry locally exercises the production clock check,
            # not a monkeypatch or bypass. Every HTTP/WS send still validates the real store.
            with store.connect() as db:
                db.execute("UPDATE sessions SET expires=? WHERE token_hash=?", (time.time()+.2,store.digest(next(iter(cookies.values())))))
            time.sleep(.3)
            self.assertEqual(self.raw("A", "GET", "/api/auth/me", headers=self.session_headers(cookies,me)).status_code, 401)
            with self.assertRaises(ConnectionClosed):
                ws.recv(timeout=2)

    def test_password_reset_revokes_sessions_and_websocket_without_service_restart(self):
        cookies, me, _ = self.login()
        pid = self.procs["A"].pid
        with self.ws("A", cookies) as ws:
            ws.recv(timeout=2)
            self.admin("A", "reset-password", self.passwords["A"])
            with self.assertRaises(ConnectionClosed):
                ws.recv(timeout=2)
        self.assertEqual(self.procs["A"].pid,pid)
        self.assertEqual(self.raw("A", "GET", "/api/auth/me", headers=self.session_headers(cookies,me)).status_code,401)
        new_cookies,new_me,_ = self.login()
        self.cookies["A"],self.csrf["A"] = new_cookies,new_me["csrf"]

    def test_restart_invalidates_old_sessions_and_keeps_account(self):
        cookies,me,_ = self.login()
        self.stop("A")
        self.start("A")
        self.assertEqual(self.raw("A", "GET", "/api/auth/me", headers=self.session_headers(cookies,me)).status_code,401)
        self.assertEqual(self.ok("A","GET","/api/auth/me")["username"],"A")

    def test_task_cancellation_requires_auth_and_unknown_task_is_not_owned(self):
        self.assertEqual(self.raw("A","POST","/api/canvas-llm/cancel",json={"request_id":"known"}).status_code,401)
        self.assertEqual(self.request("A","DELETE","/api/canvas-image-tasks/guessed").status_code,404)
        self.assertEqual(self.request("A","GET","/api/canvas-image-tasks/guessed").status_code,404)

    def test_private_media_is_not_an_executable_same_origin_document(self):
        path = self.roots["A"] / "assets/output/untrusted.svg"
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>not_executed()</script></svg>')
        response = self.request("A", "GET", "/assets/output/untrusted.svg")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-security-policy"], "sandbox; default-src 'none'")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(self.raw("A", "GET", "/assets/output/untrusted.svg").status_code, 401)

    def test_mock_llm_can_run_and_cancel_without_cross_instance_task_access(self):
        marker = secrets.token_hex(12)
        payload = {"provider":"mock", "model":"mock-chat", "message":"WAIT_CANCEL "+marker,
                   "request_id":marker, "client_id":"forged", "role":"admin"}
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.request,"A","POST","/api/canvas-llm",json=payload)
            deadline=time.monotonic()+3
            while time.monotonic()<deadline and not any(marker in json.dumps(call[1]) for call in isolation.MockUpstream.calls):
                time.sleep(.02)
            self.assertFalse(future.done())
            other=self.ok("B","POST","/api/canvas-llm/cancel",json={"request_id":marker})
            self.assertFalse(other["active"])
            self.assertFalse(future.done())
            own=self.ok("A","POST","/api/canvas-llm/cancel",json={"request_id":marker})
            self.assertTrue(own["active"])
            self.assertEqual(future.result(timeout=3).status_code,499)

    def test_y_login_rate_limit_is_finite_and_does_not_log_credentials(self):
        store = AuthStore(self.roots["A"], "A")
        with store.connect() as db:
            db.execute("DELETE FROM attempts")
        password = secrets.token_urlsafe(25)
        for _ in range(10):
            self.assertEqual(self.raw("A","POST","/api/auth/login",headers={"Origin":self.origin()},json={"username":"A","password":password}).status_code,401)
        blocked = self.raw("A","POST","/api/auth/login",headers={"Origin":self.origin()},json={"username":"A","password":password})
        self.assertEqual(blocked.status_code,429)
        self.assertEqual(blocked.headers["retry-after"],"300")
        with store.connect() as db:
            db.execute("UPDATE attempts SET started=?",(time.time()-301,))
        cookies,me,_ = self.login()
        logs=(self.roots["A"] / ".runtime/logs/server.log").read_text()
        self.assertFalse(password in logs, "password must not be logged")
        self.assertFalse(any(value in logs for value in cookies.values()), "session token must not be logged")
        self.assertFalse(me["csrf"] in logs, "CSRF token must not be logged")

    def test_z_disable_revokes_sessions_and_websocket(self):
        cookies,me,_ = self.login("B")
        with self.ws("B",cookies) as ws:
            ws.recv(timeout=2)
            self.admin("B","disable")
            with self.assertRaises(ConnectionClosed):
                ws.recv(timeout=2)
        self.assertEqual(self.raw("B","GET","/api/auth/me",headers=self.session_headers(cookies,me,"B")).status_code,401)
        self.assertEqual(self.raw("B","POST","/api/auth/login",headers={"Origin":self.origin("B")},json={"username":"B","password":self.passwords["B"]}).status_code,401)
