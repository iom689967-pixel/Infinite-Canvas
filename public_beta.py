"""Mio Canvas Public Beta loopback Gateway; one shared UI, isolated workers."""
import asyncio
from instance_maintenance import tracked_to_thread
from contextlib import asynccontextmanager
import hmac
import ipaddress
import json
import mimetypes
import secrets
from types import SimpleNamespace
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, WebSocket
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse, Response
from starlette.websockets import WebSocketDisconnect

from instance_auth import PASSWORD_MAX_LENGTH
from public_beta_store import BetaConfig, GatewayStore, BetaError, REGISTRATION_PASSWORD_MIN_LENGTH
from public_beta_ipc import gateway_supervisor
from workspace_assets import program_assets, PRIVATE_CACHE
from public_beta_pages import page
from public_beta_email import EmailRegistration
from public_beta_mail import mailer_for
from public_beta_email_codes import PENDING_COOKIE

COOKIE='mio_beta_session'



def create_app(config, *, mailer=None):
    store=GatewayStore(config);supervisor=gateway_supervisor(store)
    @asynccontextmanager
    async def lifespan(app):
        await tracked_to_thread(supervisor.recover_incomplete)
        yield
        await tracked_to_thread(supervisor.close)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.store=store;app.state.supervisor=supervisor
    emails=EmailRegistration(store,supervisor,mailer if mailer is not None else mailer_for(config))
    app.state.emails=emails
    assets = program_assets(str(supervisor.program / 'static'))

    def connection(scope, headers):
        peer=str((scope.get('client') or ('',0))[0])
        if config.proxied:
            try:
                if str(ipaddress.ip_address(peer)) not in config.trusted_proxies: raise ValueError()
                forwarded=headers.get('x-forwarded-for','')
                if ',' in forwarded or str(ipaddress.ip_address(forwarded))!=forwarded: raise ValueError()
            except ValueError:
                return ''
            expected_scheme=config.origin.split(':',1)[0]
            if headers.get('host')!=config.public_host or headers.get('x-forwarded-proto')!=expected_scheme:
                return ''
            return forwarded
        expected_scheme='ws' if scope.get('type')=='websocket' else 'http'
        if headers.get('host')!=config.public_host or scope.get('scheme')!=expected_scheme: return ''
        return peer

    @app.middleware('http')
    async def boundary(request, call_next):
        client_ip=connection(request.scope,request.headers)
        if not client_ip:
            return JSONResponse({'detail':'请求来源不受信任'},403)
        request.scope.setdefault('state',{})['beta_client_ip']=client_ip
        if request.method not in {'GET','HEAD','OPTIONS'} and request.headers.get('origin')!=config.origin:
            return JSONResponse({'detail':'请求来源不受信任'},403)
        try: response=await call_next(request)
        except BetaError as exc: response=JSONResponse({'detail':exc.message},exc.status)
        except Exception: response=JSONResponse({'detail':'服务暂时不可用，请稍后再试'},503)
        cache = assets.cache_control(request.url.path, request.url.query, request.method,
                                     response.status_code, 'set-cookie' in response.headers)
        response.headers.update({'Cache-Control':cache,'Referrer-Policy':'no-referrer',
                                 'X-Content-Type-Options':'nosniff','X-Frame-Options':'SAMEORIGIN'})
        if cache != PRIVATE_CACHE:
            for name in ('pragma', 'x-instance-namespace'):
                if name in response.headers: del response.headers[name]
        return response

    async def body(request):
        if not request.headers.get('content-type','').startswith('application/json'): raise BetaError('需要 JSON 请求')
        data=bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data)>8192: raise BetaError('请求过大',413)
        try:
            result=json.loads(data)
            if not isinstance(result,dict): raise ValueError()
            return result
        except (ValueError,TypeError): raise BetaError('请求格式不正确') from None

    def current(request):
        result=store.principal(request.cookies.get(COOKIE,''))
        if not result: raise BetaError('请先登录',401)
        return result

    def csrf(request,principal):
        if not hmac.compare_digest(principal['csrf'],request.headers.get('x-csrf-token','')):
            raise BetaError('CSRF 校验失败',403)

    def signed_in(token, **data):
        response=JSONResponse({'ok':True,**data})
        response.set_cookie(COOKIE,token,httponly=True,secure=config.secure,samesite='strict',max_age=config.session_ttl,path='/')
        return response

    def outward_cookie(value):
        return value+'; Secure' if config.secure and '; secure' not in value.lower() else value

    @app.get('/healthz')
    async def health(): return {'ok':True}

    @app.get('/login')
    async def login_page(): return HTMLResponse(page('login',config))

    @app.get('/register')
    async def register_page(): return HTMLResponse(page('register',config))

    @app.get('/verify-email')
    async def verify_page(): return HTMLResponse(page('verify-email',config))

    @app.get('/resend-verification')
    async def resend_page(): return HTMLResponse(page('resend-verification',config))

    def pending_cookie(request):
        principal=store.principal(request.cookies.get(COOKIE,''))
        if principal: csrf(request,principal)
        return request.cookies.get(PENDING_COOKIE,'')

    @app.get('/api/beta/email-code-pending')
    async def pending_email_code(request:Request):
        return await tracked_to_thread(emails.codes.status,request.cookies.get(PENDING_COOKIE,''))

    @app.post('/api/beta/verify-email-code')
    async def verify_email_code(request:Request):
        values=await body(request)
        if set(values)!={'pending_id','code'}: raise BetaError('验证字段不正确')
        cookie=pending_cookie(request)
        token=await tracked_to_thread(emails.codes.verify,values['pending_id'],cookie,values['code'],request.state.beta_client_ip)
        store.revoke(request.cookies.get(COOKIE,''))
        response=signed_in(token,status='verified')
        response.delete_cookie(PENDING_COOKIE,path='/',httponly=True,secure=config.secure,samesite='strict')
        return response

    @app.post('/api/beta/resend-email-code')
    async def resend_email_code(request:Request):
        values=await body(request)
        if set(values)!={'pending_id'}: raise BetaError('验证字段不正确')
        return await tracked_to_thread(emails.codes.resend,values['pending_id'],pending_cookie(request),request.state.beta_client_ip)

    @app.post('/api/beta/cancel-email-code')
    async def cancel_email_code(request:Request):
        values=await body(request)
        if set(values)!={'pending_id'}: raise BetaError('验证字段不正确')
        result=await tracked_to_thread(emails.codes.cancel,values['pending_id'],pending_cookie(request))
        response=JSONResponse(result)
        response.delete_cookie(PENDING_COOKIE,path='/',httponly=True,secure=config.secure,samesite='strict')
        return response

    @app.post('/api/beta/verify-email')
    async def verify_email(request:Request):
        values=await body(request)
        if set(values)!={'token'}: raise BetaError('验证字段不正确')
        return await tracked_to_thread(emails.verify,values['token'],request.state.beta_client_ip)

    @app.post('/api/beta/resend-verification')
    async def resend_email(request:Request):
        values=await body(request)
        if set(values)!={'email'}: raise BetaError('验证字段不正确')
        return await tracked_to_thread(emails.resend,values['email'],request.state.beta_client_ip)

    @app.post('/api/beta/register')
    async def register(request:Request):
        if config.registration_mode=='closed': raise BetaError('当前 Mio Canvas Public Beta 暂未开放注册。',403)
        values=await body(request)
        expected={'email','username','password','confirmation'} | ({'invite_code'} if config.registration_mode=='invite' else set())
        if set(values)!=expected: raise BetaError('注册字段不正确')
        if config.registration_mode=='invite':
            store.limit('registration-invite',request.state.beta_client_ip,config.register_limit)
        if not config.invite_valid(values.pop('invite_code','')): raise BetaError('邀请码无效',403)
        pending_cookie(request)
        outcome=await tracked_to_thread(emails.register,values['email'],values['username'],values['password'],values['confirmation'],request.state.beta_client_ip)
        response=JSONResponse(outcome.data)
        response.set_cookie(PENDING_COOKIE,outcome.cookie,httponly=True,secure=config.secure,
                            samesite='strict',max_age=config.pending_ttl,path='/')
        return response

    @app.post('/api/beta/login')
    async def login(request:Request):
        values=await body(request)
        if set(values) not in ({'identifier','password'},{'username','password'}): raise BetaError('登录字段不正确')
        token=await tracked_to_thread(store.login,values.get('identifier',values.get('username')),values['password'],request.state.beta_client_ip)
        store.revoke(request.cookies.get(COOKIE,''))
        return signed_in(token)

    @app.get('/api/beta/me')
    async def me(request:Request):
        principal=current(request)
        return {k:principal[k] for k in ('username','csrf')}

    def instance_cookie(instance):
        from instance_auth import AuthStore
        return 'ic_dev_'+AuthStore.digest(instance['instance_id']+instance['data_root'])[:16]

    websocket_counts={}
    @app.websocket('/ws/stats')
    async def stats(socket:WebSocket):
        token=socket.cookies.get(COOKIE,'');principal=store.principal(token)
        if not principal or socket.headers.get('origin')!=config.origin or not connection(socket.scope,socket.headers):
            await socket.close(code=1008);return
        uid=principal['id']
        if websocket_counts.get(uid,0)>=8:
            await socket.close(code=1008);return
        instance=store.instance(uid);websocket_counts[uid]=websocket_counts.get(uid,0)+1
        tasks=[]
        try:
            from websockets.asyncio.client import connect
            cookie=instance_cookie(instance)
            async with connect(f'ws://127.0.0.1:{instance["assigned_port"]}/ws/stats',
                    origin=f'http://127.0.0.1:{instance["assigned_port"]}',
                    additional_headers={'Cookie':cookie+'='+socket.cookies.get(cookie,'')},proxy=None,
                    max_size=1024**2,open_timeout=3) as upstream:
                await socket.accept()
                async def inbound():
                    while True:
                        message=await socket.receive()
                        if message['type']=='websocket.disconnect':return
                        await upstream.send(message.get('text') if message.get('text') is not None else message.get('bytes',b''))
                async def outbound():
                    async for message in upstream:
                        if isinstance(message,str):await socket.send_text(message)
                        else:await socket.send_bytes(message)
                async def revoked():
                    while store.principal(token):
                        supervisor.touch(instance['instance_id'])
                        await asyncio.sleep(1)
                tasks=[asyncio.create_task(coro()) for coro in (inbound,outbound,revoked)]
                await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        except Exception:
            pass  # No URL, cookies, sessions or raw network exception in logs.
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            websocket_counts[uid]-=1
            try: await socket.close(code=1008)
            except (RuntimeError, WebSocketDisconnect): pass

    def workspace_shell(principal, document='index.html'):
        from instance_frontend import authenticated_html
        instance=store.instance(principal['id'])
        # Program HTML and presentation context only. Private content still requires
        # the worker session; the Gateway never reads Provider or user content files.
        paths=SimpleNamespace(data_root=Path(instance['data_root']),instance_id=instance['instance_id'],
                              public_beta=True,program_root=supervisor.program)
        presentation={'username':principal['username'],'permissions':['manage_own_providers']}
        html=(supervisor.program/'static'/document).read_text()
        return HTMLResponse(authenticated_html(html,paths,presentation,gateway_workspace=document=='index.html'))

    async def ensure_instance(principal):
        with store.db() as db:
            row=db.execute('''SELECT u.username,u.password_hash FROM users u JOIN email_code_pending p ON p.user_id=u.id
                WHERE u.id=? AND u.status='active' AND p.completed_at IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM instances WHERE user_id=u.id)''',(principal['id'],)).fetchone()
        if row:
            if hasattr(supervisor,'call'):
                await tracked_to_thread(supervisor.call,'provision',username=row['username'],password_hash=row['password_hash'])
            else:
                await tracked_to_thread(supervisor.provision,row['username'],row['password_hash'])

    @app.get('/workspace')
    async def workspace_alias(request:Request):
        principal=current(request);await ensure_instance(principal)
        return workspace_shell(principal)

    @app.post('/api/beta/logout')
    async def logout_before_ready(request:Request):
        principal=current(request);csrf(request,principal)
        store.revoke(request.cookies.get(COOKIE,''))
        response=JSONResponse({'ok':True})
        names=[COOKIE,PENDING_COOKIE]
        try: names.append(instance_cookie(store.instance(principal['id'])))
        except BetaError: pass
        for name in names:
            response.delete_cookie(name,path='/',httponly=True,secure=config.secure,samesite='strict')
        return response

    @app.post('/api/beta/enter')
    async def enter(request:Request):
        principal=current(request);csrf(request,principal)
        await ensure_instance(principal)
        instance=await tracked_to_thread(supervisor.start,principal['id'])
        origin=f'http://127.0.0.1:{instance["assigned_port"]}'
        reply=None
        async with httpx.AsyncClient(trust_env=False,timeout=5) as client:
            cookie=instance_cookie(instance)
            existing=request.cookies.get(cookie,'')
            me=await client.get(origin+'/api/auth/me',headers={'Cookie':cookie+'='+existing}) if existing else None
            if me is None or me.status_code!=200:
                reply=await client.post(origin+'/__mio/handoff',headers={'Origin':origin},json={'ticket':supervisor.ticket(instance)})
                if reply.status_code!=200: raise BetaError('工作区登录交接失败，请重试',503)
                me=await client.get(origin+'/api/auth/me')
        if me.status_code!=200: raise BetaError('工作区登录交接失败，请重试',503)
        # Disable/session revocation can race a slow start; never issue a usable central session after it.
        current(request)
        response=JSONResponse({'ok':True,'session':me.json()})
        if reply is not None:
            for cookie in reply.headers.get_list('set-cookie'): response.headers.append('set-cookie',outward_cookie(cookie))
        return response

    async def forward(request,principal):
        instance=store.instance(principal['id'])
        if instance['status']!='running': raise BetaError('工作区尚未启动，请返回首页',503)
        supervisor.touch(instance['instance_id'])
        origin=f'http://127.0.0.1:{instance["assigned_port"]}'
        headers={k:v for k,v in request.headers.items() if k.lower() in {'accept','content-type','range','if-range','if-none-match','if-modified-since','x-csrf-token','accept-encoding'}}
        headers['host']=f'127.0.0.1:{instance["assigned_port"]}'
        from instance_maintenance import CURRENT_ACTIVITY
        admission = CURRENT_ACTIVITY.get()
        if admission:
            headers['x-mio-maintenance-parent'] = admission.id
        if request.headers.get('origin'): headers['origin']=origin
        cookie=instance_cookie(instance)
        headers['cookie']=cookie+'='+request.cookies.get(cookie,'')
        async def chunks():
            total=0
            async for chunk in request.stream():
                total+=len(chunk)
                if total>config.max_upload+1024**2: raise BetaError('请求超过上传上限',413)
                yield chunk
        long_generation=request.url.path in {'/api/online-image','/api/canvas-video','/api/chat','/api/chat/agent','/api/ms/generate','/api/angle/generate','/generate'}
        client=httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=httpx.Timeout(1830 if long_generation else 120,connect=3))
        url=httpx.URL(origin).copy_with(path=request.url.path,query=request.url.query.encode())
        try:
            upstream=await client.send(client.build_request(request.method,url,headers=headers,content=chunks() if request.method not in {'GET','HEAD'} else None),stream=True)
        except Exception:
            await client.aclose();raise
        if request.url.path=='/api/auth/logout' and upstream.status_code==200:
            store.revoke(request.cookies.get(COOKIE,''))
        copied={k:v for k,v in upstream.headers.items() if k.lower() in {'content-type','content-length','content-encoding','content-disposition','x-instance-namespace','content-security-policy','etag','last-modified','content-range','accept-ranges','x-mio-maintenance','retry-after'}}
        location=upstream.headers.get('location')
        if location:
            if location.startswith('/') and not location.startswith('//'): copied['location']=location
            else: await upstream.aclose();await client.aclose();raise BetaError('工作区重定向不可用',502)
        async def relay():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                import anyio
                with anyio.CancelScope(shield=True):
                    async with asyncio.timeout(5):
                        await upstream.aclose()
                        await client.aclose()
        response=StreamingResponse(relay(),status_code=upstream.status_code,headers=copied)
        for value in upstream.headers.get_list('set-cookie'): response.headers.append('set-cookie',outward_cookie(value))
        if request.url.path=='/api/auth/logout' and upstream.status_code==200:
            response.delete_cookie(COOKIE,path='/',httponly=True,secure=config.secure,samesite='strict')
        return response

    @app.api_route('/{path:path}',methods=['GET','HEAD','POST','PUT','PATCH','DELETE'])
    async def workspace(path:str,request:Request):
        if path.startswith(('__mio/','api/beta/')): raise BetaError('未开放此接口',404)
        principal=store.principal(request.cookies.get(COOKIE,''))
        if not principal:
            if not path and request.method=='GET': return HTMLResponse(page('home',config))
            if request.method=='GET' and 'text/html' in request.headers.get('accept','') and (path.endswith('.html') or not path):
                return RedirectResponse('/login',303)
            raise BetaError('请先登录',401)
        if path in {'','static/index.html'} and request.method=='GET':
            await ensure_instance(principal)
            return workspace_shell(principal)
        if path=='static/canvas-list.html' and request.method=='GET' and request.headers.get('sec-fetch-dest')=='iframe':
            # The same Canvas framework can render early. Its session bootstrap
            # inherits the parent handoff before making any private API request.
            return workspace_shell(principal,'canvas-list.html')
        # These exact program resources are shared, credential-free bytes. Serving
        # them here lets the authenticated shell render while its worker is cold.
        url='/'+path
        if url in assets.files and request.method in {'GET','HEAD'}:
            data,etag=assets.representation(url)
            validators={v.strip().removeprefix('W/') for v in request.headers.get('if-none-match','').split(',')}
            status=304 if etag in validators or '*' in validators else 200
            headers={'ETag':etag}
            if status==200: headers['Content-Length']=str(len(data))
            return Response(data if status==200 and request.method=='GET' else b'',status_code=status,
                            media_type=mimetypes.guess_type(url)[0],headers=headers)
        return await forward(request,principal)

    if config.maintenance_root:
        from instance_maintenance import MaintenanceMiddleware
        app.add_middleware(MaintenanceMiddleware, root=config.maintenance_root)
    return app


def main():
    import os
    import uvicorn
    config=BetaConfig.from_env()
    inherited=os.environ.get('LISTEN_PID')==str(os.getpid()) and os.environ.get('LISTEN_FDS')=='1'
    uvicorn.run(create_app(config),host=config.host,port=config.port,fd=3 if inherited else None,
                proxy_headers=False,access_log=False,log_level='warning',timeout_graceful_shutdown=25)


if __name__=='__main__': main()
