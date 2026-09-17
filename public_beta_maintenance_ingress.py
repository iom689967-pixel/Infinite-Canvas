"""Temporary first-upgrade observer. No new permanent service or public admin API.

Start before observing old accepted requests; use the Mio-only Caddy route to
reach it. It cannot retroactively observe requests accepted before installation.
The production upgrade must retain that bootstrap uncertainty as a blocker.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
import ipaddress
import os
from pathlib import Path
import urllib.parse

import anyio
import httpx
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse, JSONResponse

from instance_maintenance import MaintenanceMiddleware


def create_ingress(root, target, *, public_host, proxied=True, mock=False, transport=None):
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('临时入口只允许固定的 loopback Gateway，不是任意代理')
    if not mock and not proxied:
        raise ValueError('生产入口必须保留 Caddy 来源边界')
    client = httpx.AsyncClient(trust_env=False, follow_redirects=False, transport=transport, timeout=httpx.Timeout(1830, connect=3))
    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.api_route('/{path:path}', methods=['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'])
    async def forward(path: str, request: Request):
        try:
            if not ipaddress.ip_address(request.client.host).is_loopback or request.headers.get('host') != public_host:
                raise ValueError()
        except ValueError:
            return JSONResponse({'detail': '请求来源不受信任'}, 403)
        # Transparent Origin/CSRF/session boundary remains in the old Gateway.
        # Fixed loopback destination only; no upstream Provider keys or redirects.
        headers = {k:v for k,v in request.headers.items() if k.lower() not in {
            'connection','transfer-encoding','content-length','x-mio-maintenance-parent'}}
        if not proxied:
            headers = {k:v for k,v in headers.items() if not k.lower().startswith('x-forwarded-') and k.lower()!='forwarded'}
            headers['host'] = parsed.netloc
        url = httpx.URL(target).copy_with(path=request.url.path, query=request.url.query.encode())
        async def chunks():
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                # Existing deployment upload quotas remain enforced by Gateway.
                # This envelope also protects the temporary observer's ingress.
                if size > 64*1024**2:
                    raise ValueError('临时入口请求体安全上限')
                yield chunk
        from instance_maintenance import CURRENT_ACTIVITY
        activity = CURRENT_ACTIVITY.get()
        uncertainty = activity.gate.uncertain_llm(activity.instance, 'legacy_request_unknown') if activity else None
        client.cookies.clear()  # The observer must never supply another user's session.
        response = await client.send(client.build_request(request.method, url, headers=headers,
            content=chunks() if request.method not in {'GET','HEAD'} else None), stream=True)
        client.cookies.clear()
        copied = [(k,v) for k,v in response.headers.multi_items() if k.lower() not in {
            'connection','transfer-encoding','keep-alive'}]
        # Retain uncertainty when a legacy backend response is interrupted. An
        # ingress disconnect does not prove the old backend or remote work ended.
        async def relay():
            completed = False
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
                completed = True
            finally:
                with anyio.CancelScope(shield=True):
                    async with asyncio.timeout(5):
                        await response.aclose()
                if completed and uncertainty and response.status_code < 500:
                    activity.gate.complete_llm(uncertainty)
        result = StreamingResponse(relay(), status_code=response.status_code)
        result.raw_headers = [(k.encode('latin-1'), v.encode('latin-1')) for k,v in copied]
        return result

    app.add_middleware(MaintenanceMiddleware, root=root, instance='ingress')
    return app


def main():
    parser=argparse.ArgumentParser(description='临时首次升级入口；不能证明切入前的旧请求已排空')
    parser.add_argument('--root',required=True);parser.add_argument('--target',required=True)
    parser.add_argument('--public-host',required=True);parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--mock',action='store_true')
    args=parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit('Invalid loopback port')
    if args.mock and not Path(args.root).resolve().is_relative_to(Path(__import__('tempfile').gettempdir()).resolve()):
        raise SystemExit('mock 只允许临时目录')
    import uvicorn
    uvicorn.run(create_ingress(args.root,args.target,public_host=args.public_host,proxied=not args.mock,mock=args.mock),
                host='127.0.0.1',port=args.port,proxy_headers=False,access_log=False,log_level='warning')


if __name__=='__main__':
    main()
