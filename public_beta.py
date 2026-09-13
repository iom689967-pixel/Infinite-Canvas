"""Mio Canvas Public Beta loopback Gateway; one shared UI, isolated workers."""
import asyncio
from contextlib import asynccontextmanager
import hmac
import ipaddress
import json
import secrets

import httpx
from fastapi import FastAPI, Request, WebSocket
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask

from instance_auth import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH
from public_beta_store import BetaConfig, GatewayStore, BetaError
from public_beta_supervisor import Supervisor
from workspace_assets import program_assets, PRIVATE_CACHE

COOKIE='mio_beta_session'


def page(kind, config):
    title={'home':'Mio Canvas','login':'登录 Mio Canvas','register':'注册 Mio Canvas','starting':'正在启动你的工作区…'}[kind]
    if kind=='home':
        register='<a href="/register">注册</a>' if config.registration_mode != 'closed' else ''
        body='<p>Public Beta · 免费使用 · API 由用户自行配置</p><nav><a href="/login">登录</a>'+register+'</nav>'
    elif kind=='starting':
        body='<p>请稍候，工作区健康后会自动打开。</p><button id="retry">重试</button><p><a href="/login">返回登录</a></p>'
    elif kind=='register' and config.registration_mode == 'closed':
        body='<p>当前 Mio Canvas Public Beta 暂未开放注册。</p><p><a href="/login">已有账号，登录</a></p>'
    else:
        password_bounds=f'minlength="{PASSWORD_MIN_LENGTH}" maxlength="{PASSWORD_MAX_LENGTH}"'
        confirm='<label>确认密码<input name="confirmation" type="password" '+password_bounds+' autocomplete="new-password" required></label>' if kind=='register' else ''
        invite='<label>测试邀请码<input name="invite_code" type="password" maxlength="1024" autocomplete="one-time-code" required></label>' if kind=='register' and config.registration_mode=='invite' else ''
        register_link='<p><a href="/register">注册账号</a></p>' if kind=='login' and config.registration_mode!='closed' else ''
        alternative='<p><a href="/login">已有账号，登录</a></p>' if kind=='register' else register_link
        body='<form><label>用户名<input name="username" minlength="3" maxlength="32" pattern="[A-Za-z0-9][A-Za-z0-9_-]{2,31}" autocomplete="username" required></label><label>密码<input name="password" type="password" '+password_bounds+' autocomplete="'+('new-password' if kind=='register' else 'current-password')+'" required></label>'+confirm+invite+'<button type="submit">'+('注册并进入' if kind=='register' else '登录')+'</button></form>'+alternative
    script=''
    if kind in {'login','register'} and not (kind=='register' and config.registration_mode=='closed'):
        script="""document.querySelector('form').addEventListener('submit',async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{const values=Object.fromEntries(new FormData(e.target));const r=await fetch('/api/beta/"""+kind+"""',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});const d=await r.json();if(!r.ok)throw new Error(d.detail);e.target.reset();location.replace('/workspace');}catch(e){document.querySelector('#message').textContent=e.message||'请求失败';}finally{b.disabled=false;}});"""
    elif kind=='starting':
        script="""async function enter(){const b=document.querySelector('#retry');b.disabled=true;try{const m=await fetch('/api/beta/me').then(r=>r.json());const r=await fetch('/api/beta/enter',{method:'POST',headers:{'X-CSRF-Token':m.csrf}});const d=await r.json();if(!r.ok)throw new Error(d.detail);location.replace('/');}catch(e){document.querySelector('#message').textContent=e.message||'暂时无法启动';}finally{b.disabled=false;}}document.querySelector('#retry').onclick=enter;enter();"""
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title><style>body{margin:0;background:#f7f7f5;color:#252525;font:16px system-ui,sans-serif;display:grid;min-height:100vh;place-items:center}main{width:min(380px,85vw);padding:36px;background:white;border:1px solid #ddd;border-radius:20px}h1{font-size:28px}p{line-height:1.6;color:#666}label{display:block;margin:18px 0}input{display:block;box-sizing:border-box;width:100%;padding:12px;margin-top:8px;border:1px solid #bbb;border-radius:8px;font:inherit}button,nav a{display:inline-block;padding:12px 20px;background:#252525;color:white;border:0;border-radius:9px;font:inherit;cursor:pointer}button:disabled{opacity:.5}a{color:inherit;margin-right:12px}#message{color:#a32c2c}</style></head><body><main><h1>'+title+'</h1>'+body+'<p id="message" role="status"></p></main><script>'+script+'</script></body></html>'


def create_app(config):
    store=GatewayStore(config);supervisor=Supervisor(store)
    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(supervisor.recover_incomplete)
        yield
        await asyncio.to_thread(supervisor.close)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.store=store;app.state.supervisor=supervisor
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
        if headers.get('host')!=config.public_host or scope.get('scheme')!='http': return ''
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

    def signed_in(token):
        response=JSONResponse({'ok':True})
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

    @app.post('/api/beta/register')
    async def register(request:Request):
        if config.registration_mode=='closed': raise BetaError('当前 Mio Canvas Public Beta 暂未开放注册。',403)
        values=await body(request)
        expected={'username','password','confirmation'} | ({'invite_code'} if config.registration_mode=='invite' else set())
        if set(values)!=expected: raise BetaError('注册字段不正确')
        if config.registration_mode=='invite':
            store.limit('registration-invite',request.state.beta_client_ip,config.register_limit)
        if not config.invite_valid(values.pop('invite_code','')): raise BetaError('邀请码无效',403)
        token=await asyncio.to_thread(supervisor.register,values['username'],values['password'],values['confirmation'],request.state.beta_client_ip)
        store.revoke(request.cookies.get(COOKIE,''))
        return signed_in(token)

    @app.post('/api/beta/login')
    async def login(request:Request):
        values=await body(request)
        if set(values)!={'username','password'}: raise BetaError('登录字段不正确')
        token=await asyncio.to_thread(store.login,values['username'],values['password'],request.state.beta_client_ip)
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
                    while store.principal(token):await asyncio.sleep(1)
                tasks=[asyncio.create_task(coro()) for coro in (inbound,outbound,revoked)]
                await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        except Exception:
            pass  # No URL, cookies, sessions or raw network exception in logs.
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            websocket_counts[uid]-=1
            try: await socket.close(code=1008)
            except RuntimeError: pass

    @app.get('/workspace')
    async def starting(request:Request):
        current(request)
        return HTMLResponse(page('starting',config))

    @app.post('/api/beta/enter')
    async def enter(request:Request):
        principal=current(request);csrf(request,principal)
        instance=await asyncio.to_thread(supervisor.start,principal['id'])
        origin=f'http://127.0.0.1:{instance["assigned_port"]}'
        async with httpx.AsyncClient(trust_env=False,timeout=5) as client:
            reply=await client.post(origin+'/__mio/handoff',headers={'Origin':origin},json={'ticket':supervisor.ticket(instance)})
        if reply.status_code!=200: raise BetaError('工作区登录交接失败，请重试',503)
        # Disable/session revocation can race a slow start; never issue a usable central session after it.
        current(request)
        response=JSONResponse({'ok':True})
        for cookie in reply.headers.get_list('set-cookie'): response.headers.append('set-cookie',outward_cookie(cookie))
        return response

    async def forward(request,principal):
        instance=store.instance(principal['id'])
        if instance['status']!='running': raise BetaError('工作区尚未启动，请返回首页',503)
        origin=f'http://127.0.0.1:{instance["assigned_port"]}'
        headers={k:v for k,v in request.headers.items() if k.lower() in {'accept','content-type','range','if-range','if-none-match','if-modified-since','x-csrf-token','accept-encoding'}}
        headers['host']=f'127.0.0.1:{instance["assigned_port"]}'
        if request.headers.get('origin'): headers['origin']=origin
        cookie=instance_cookie(instance)
        headers['cookie']=cookie+'='+request.cookies.get(cookie,'')
        async def chunks():
            total=0
            async for chunk in request.stream():
                total+=len(chunk)
                if total>config.max_upload+1024**2: raise BetaError('请求超过上传上限',413)
                yield chunk
        client=httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=httpx.Timeout(120,connect=3))
        url=httpx.URL(origin).copy_with(path=request.url.path,query=request.url.query.encode())
        try:
            upstream=await client.send(client.build_request(request.method,url,headers=headers,content=chunks() if request.method not in {'GET','HEAD'} else None),stream=True)
        except Exception:
            await client.aclose();raise
        if request.url.path=='/api/auth/logout' and upstream.status_code==200:
            store.revoke(request.cookies.get(COOKIE,''))
        copied={k:v for k,v in upstream.headers.items() if k.lower() in {'content-type','content-length','content-encoding','content-disposition','x-instance-namespace','content-security-policy','etag','last-modified'}}
        location=upstream.headers.get('location')
        if location:
            if location.startswith('/') and not location.startswith('//'): copied['location']=location
            else: await upstream.aclose();await client.aclose();raise BetaError('工作区重定向不可用',502)
        async def close(): await upstream.aclose();await client.aclose()
        response=StreamingResponse(upstream.aiter_raw(),status_code=upstream.status_code,headers=copied,background=BackgroundTask(close))
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
        instance=store.instance(principal['id'])
        if not path and request.method=='GET' and (instance['status']!='running' or not request.cookies.get(instance_cookie(instance))):
            return HTMLResponse(page('starting',config))
        return await forward(request,principal)

    return app


def main():
    import uvicorn
    config=BetaConfig.from_env()
    uvicorn.run(create_app(config),host=config.host,port=config.port,proxy_headers=False,access_log=False,log_level='warning')


if __name__=='__main__': main()
