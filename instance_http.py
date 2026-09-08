"""Default-deny HTTP/WebSocket session boundary for explicit single-person instances."""
import asyncio
from http.cookies import SimpleCookie
import hmac
import json
import urllib.parse

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Match

from instance_access import PUBLIC_STATIC, ROUTE_ACCESS, WORKBENCH_STATIC
from instance_auth import PRINCIPAL


class InstanceAuthMiddleware:
    def __init__(self, app, *, routes, paths, store, catalog):
        self.app, self.routes, self.paths, self.store, self.catalog = app, routes, paths, store, catalog
        self.secure = not paths.auth_http_loopback
        scheme = "https" if self.secure else "http"
        self.origin = f"{scheme}://{paths.host}:{paths.port}"
        self.host = f"{paths.host}:{paths.port}"
        self.cookie_name = ("__Host-ic_" if self.secure else "ic_dev_") + store.digest(store.instance_id + str(store.root))[:16]

    def cookie(self, headers):
        try:
            values = SimpleCookie()
            values.load(headers.get("cookie", ""))
            return values[self.cookie_name].value if self.cookie_name in values else ""
        except Exception:
            return ""

    def classify(self, scope):
        path, method = scope["path"], scope.get("method", "WEBSOCKET")
        if path.startswith("/static/"):
            return "workbench" if method in {"GET", "HEAD"} and path in WORKBENCH_STATIC else "admin"
        if path.startswith(("/assets/", "/output/")):
            return "workbench" if method in {"GET", "HEAD"} else "denied"
        for route in self.routes:
            match, _ = route.matches(scope)
            if match == Match.FULL:
                return ROUTE_ACCESS.get(method + " " + route.path, "denied")
        return "denied"

    async def reply(self, scope, receive, send, response):
        response.headers.update({"Cache-Control": "no-store, private", "Pragma": "no-cache",
                                 "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
        await response(scope, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        # Proxy headers are not authoritative. Host/transport are compared to startup config.
        scope = dict(scope)
        proxy_headers = any(k.lower() == b"forwarded" or k.lower().startswith(b"x-forwarded-")
                            for k, _ in scope.get("headers", []))
        scope["headers"] = [(k, v) for k, v in scope.get("headers", [])
                            if k.lower() != b"forwarded" and not k.lower().startswith(b"x-forwarded-")]
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        path = scope["path"]
        websocket = scope["type"] == "websocket"
        expected_scheme = ("wss" if websocket else "https") if self.secure else ("ws" if websocket else "http")
        if proxy_headers or headers.get("host") != self.host or scope.get("scheme") != expected_scheme:
            if websocket:
                return await send({"type": "websocket.close", "code": 1008})
            return await self.reply(scope, receive, send, JSONResponse({"detail": "请求来源或传输方式不受信任"}, 403))
        token = self.cookie(headers)
        principal = self.store.validate(token)
        if websocket:
            if not principal or headers.get("origin") != self.origin or self.classify(scope) != "workbench":
                return await send({"type": "websocket.close", "code": 1008})
            return await self.websocket(scope, receive, send, token, principal)
        method = scope["method"]
        if path == "/healthz" and method == "GET":
            return await self.reply(scope, receive, send, JSONResponse({"ok": True}))
        if path == "/login" and method == "GET":
            body = (self.paths.program_root / "static/instance-login.html").read_text(encoding="utf-8")
            return await self.reply(scope, receive, send, Response(body, media_type="text/html"))
        if path in PUBLIC_STATIC and method in {"GET", "HEAD"}:
            return await self.app(scope, receive, send)
        if path == "/api/auth/login" and method == "POST":
            if headers.get("origin") != self.origin or not headers.get("content-type", "").startswith("application/json"):
                return await self.reply(scope, receive, send, JSONResponse({"detail": "请求来源不受信任"}, 403))
            request = Request(scope, receive)
            try:
                # Do not pass auth input to FastAPI validation/logging or echo malformed bodies.
                chunks = bytearray()
                async for chunk in request.stream():
                    chunks.extend(chunk)
                    if len(chunks) > 8192:
                        raise ValueError()
                payload = json.loads(chunks)
                username, password = payload.get("username", ""), payload.get("password", "")
                if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                return await self.reply(scope, receive, send, JSONResponse({"detail": "账号或密码不正确"}, 401))
            peer = (scope.get("client") or ("unknown", 0))[0]
            new_token, error = await asyncio.to_thread(self.store.login, username, password, peer)
            if error:
                response = JSONResponse({"detail": "尝试过于频繁，请稍后再试" if error == "limited" else "账号或密码不正确"}, 429 if error == "limited" else 401)
                if error == "limited":
                    response.headers["Retry-After"] = "300"
                return await self.reply(scope, receive, send, response)
            self.store.revoke(token)  # No session fixation; existing cookie is replaced/revoked.
            response = JSONResponse({"ok": True})
            response.set_cookie(self.cookie_name, new_token, max_age=self.store.ttl, httponly=True,
                                secure=self.secure, samesite="strict", path="/")
            return await self.reply(scope, receive, send, response)
        if not principal:
            navigation = method == "GET" and (path == "/" or path.endswith(".html")) and "text/html" in headers.get("accept", "")
            response = RedirectResponse("/login", status_code=303) if navigation else JSONResponse({"detail": "请先登录"}, 401)
            return await self.reply(scope, receive, send, response)
        if method not in {"GET", "HEAD"}:
            if headers.get("origin") != self.origin or not hmac.compare_digest(headers.get("x-csrf-token", ""), principal["csrf"]):
                return await self.reply(scope, receive, send, JSONResponse({"detail": "CSRF 或请求来源校验失败"}, 403))
        if path == "/api/auth/me" and method == "GET":
            return await self.reply(scope, receive, send, JSONResponse({k: principal[k] for k in ("username", "role", "instance_id", "csrf", "expires")}))
        if path == "/api/auth/logout" and method == "POST":
            self.store.revoke(token)
            response = JSONResponse({"ok": True})
            response.delete_cookie(self.cookie_name, path="/", secure=self.secure, httponly=True, samesite="strict")
            return await self.reply(scope, receive, send, response)
        if self.classify(scope) != "workbench":
            return await self.reply(scope, receive, send, JSONResponse({"detail": "此功能尚未开放或需要本机管理员操作"}, 403))
        scope.setdefault("state", {})["principal"] = principal
        # The server, not browser UUIDs, owns task/WebSocket/conversation identity.
        query = urllib.parse.parse_qsl(scope.get("query_string", b"").decode(), keep_blank_values=True)
        query = [(k, v) for k, v in query if k not in {"client_id", "user_id", "instance_id", "role"}]
        query.append(("client_id", principal["subject"]))
        scope["query_string"] = urllib.parse.urlencode(query).encode()
        context = PRINCIPAL.set(principal)
        started, stopped, failed = False, False, False

        async def private_send(message):
            nonlocal started, stopped, failed
            if stopped:
                return
            if not self.store.validate(token):
                stopped = True
                if not started:
                    return await self.reply(scope, receive, send, JSONResponse({"detail": "会话已失效"}, 401))
                return await send({"type": "http.response.body", "body": b"", "more_body": False})
            if message["type"] == "http.response.start":
                started = True
                failed = message["status"] >= 500
                message["headers"] = [(k, v) for k, v in message.get("headers", [])
                                      if k.lower() not in {b"cache-control", b"pragma", b"content-length"}]
                message["headers"] += [(b"cache-control", b"no-store, private"), (b"pragma", b"no-cache"),
                                       (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
                                       (b"x-frame-options", b"SAMEORIGIN")]
                if path.startswith(("/assets/", "/output/", "/api/storage-files/")) or path == "/api/download-output":
                    # Uploaded HTML/SVG must not execute as an authenticated application page.
                    message["headers"].append((b"content-security-policy", b"sandbox; default-src 'none'"))
            elif failed and message["type"] == "http.response.body":
                message = {"type": "http.response.body", "body": json.dumps({"detail": "请求失败或功能尚未开放，请重试或联系管理员"}, ensure_ascii=False).encode(), "more_body": False}
                stopped = True
            await send(message)
        try:
            if method == "GET" and path in {"/api/config", "/api/providers", "/api/models"}:
                result = self.catalog()
                if path == "/api/providers":
                    result = {"providers": result["api_providers"]}
                elif path == "/api/models":
                    result = {key: result[key] for key in ("chat_models", "image_models", "video_models")}
                return await self.reply(scope, receive, private_send, JSONResponse(result))
            await self.app(scope, receive, private_send)
        finally:
            PRINCIPAL.reset(context)

    async def websocket(self, scope, receive, send, token, principal):
        scope.setdefault("state", {})["principal"] = principal
        scope["query_string"] = urllib.parse.urlencode({"client_id": principal["subject"]}).encode()
        closed = False
        async def guarded_send(message):
            nonlocal closed
            if closed:
                return
            if message["type"] in {"websocket.accept", "websocket.send"} and not self.store.validate(token):
                closed = True
                return await send({"type": "websocket.close", "code": 1008})
            if message["type"] == "websocket.close":
                closed = True
            await send(message)
        async def watch():
            while not closed:
                await asyncio.sleep(.25)
                if not self.store.validate(token):
                    await guarded_send({"type": "websocket.close", "code": 1008})
                    return
        watcher = asyncio.create_task(watch())
        context = PRINCIPAL.set(principal)
        try:
            await self.app(scope, receive, guarded_send)
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            PRINCIPAL.reset(context)
