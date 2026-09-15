"""Loopback-only browser acceptance with an in-memory mock inbox. Never use in production.
Run with stdin open; send `status` or `finish`. No credentials/tokens are printed.
"""
import asyncio
from contextlib import asynccontextmanager
from html import escape
import json
import re
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import httpx
import uvicorn
from starlette.responses import HTMLResponse,JSONResponse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from public_beta import create_app
from public_beta_store import BetaConfig,GatewayStore
from public_beta_ipc import SupervisorClient
from public_beta_mail import MockMailer,ResendMailer
from instance_auth import password_hash
from test_instance_isolation import free_port


def main():
    program=Path(__file__).resolve().parents[1]
    resend_mock='--resend-http-mock' in sys.argv
    mode='resend' if resend_mock else 'mock'
    address_domain='example.org' if resend_mock else 'example.test'
    with tempfile.TemporaryDirectory(prefix='mio-email-browser-') as temporary:
        root=Path(temporary);port=free_port();password=secrets.token_urlsafe(24)
        secret=root/'password.txt';secret.write_text(password);secret.chmod(0o600)
        env={'PATH':os.environ.get('PATH',os.defpath),'PUBLIC_BETA_ROOT':str(root/'gateway'),
             'PUBLIC_BETA_INSTANCES_ROOT':str(root/'instances'),'PUBLIC_BETA_BACKUP_ROOT':str(root/'backups'),
             'PUBLIC_BETA_SUPERVISOR_SOCKET':str(root/'gateway/ctl.sock'),'GATEWAY_PORT':str(port),
             'MIO_MAIL_MODE':mode,'MIN_FREE_DISK_BYTES':'0','PYTHONDONTWRITEBYTECODE':'1'}
        cfg=BetaConfig(root/'gateway',root/'instances',port=port,backup_root=root/'backups',
                       supervisor_socket=env['PUBLIC_BETA_SUPERVISOR_SOCKET'],mail_mode=mode,min_free_disk=0,
                       register_limit=100,login_limit=100)
        marker=Path('/private/tmp/mio-email-browser-current.json')
        marker.write_text(json.dumps({'root':str(root),'origin':cfg.origin}));marker.chmod(0o600)
        with open(root/'fixture.log','w') as log:
            daemon=subprocess.Popen([sys.executable,str(program/'public_beta_daemon.py')],cwd=program,env=env,stdout=log,stderr=log)
            control=None;server=None;thread=None
            try:
                subprocess.run([sys.executable,str(program/'public_beta_daemon.py'),'ready'],cwd=program,env=env,stdout=log,stderr=log,check=True)
                store=GatewayStore(cfg);control=SupervisorClient(store)
                control.call('provision',username='legacy',password_hash=password_hash(password))
                mail=MockMailer();api_status={'status':202,'requests':0}
                if resend_mock:
                    messages=mail.messages
                    async def resend_transport(request):
                        if '--slow-mail' in sys.argv:await asyncio.sleep(.5)
                        if str(request.url)!='https://api.resend.com/emails':raise RuntimeError('unexpected test endpoint')
                        api_status['requests']+=1
                        if api_status['status']!=202:return httpx.Response(api_status['status'],json={'message':'synthetic upstream private error'})
                        data=json.loads(request.content)
                        code=re.search(r'^([0-9]{6})$',data['text'],re.MULTILINE).group(1)
                        messages.append({'recipient':data['to'][0],'code':code,'minutes':10})
                        return httpx.Response(202,json={'id':'synthetic-message-id'})
                    mail=ResendMailer({'MIO_RESEND_API_KEY':'synthetic-browser-key','MIO_MAIL_FROM':'Mio Canvas <noreply@mio-canvas.eu.cc>'},
                                      sending_domain='mio-canvas.eu.cc',transport=httpx.MockTransport(resend_transport))
                    mail.messages=messages
                app=create_app(cfg,mailer=mail)
                async def inbox(request):
                    user=request.path_params['username']
                    if user not in {'alice','bob','carol'}:return JSONResponse({},404)
                    exists=any(m['recipient']==user+'@'+address_domain for m in mail.messages)
                    if not exists:return HTMLResponse('<p>暂无测试邮件</p>')
                    return HTMLResponse('<h1>Mock 邮箱</h1><p>测试验证码已在内存收取。</p>',headers={'Cache-Control':'no-store'})
                async def token(request):
                    user=request.path_params['username']
                    if user not in {'alice','bob','carol'}:return JSONResponse({},404)
                    message=next((m for m in reversed(mail.messages) if m['recipient']==user+'@'+address_domain),None)
                    return JSONResponse({'code':message['code']} if message else {},status_code=200 if message else 404,headers={'Cache-Control':'no-store'})
                from starlette.routing import Route
                # Only this explicit loopback harness owns these routes. Nothing
                # is wired into the production create_app or environment flags.
                clock_offset={'seconds':0};real_time=time.time
                # Advance only code deadlines, never the Gateway/worker SSO clock.
                clock=patch('public_beta_email_codes.time',SimpleNamespace(time=lambda:real_time()+clock_offset['seconds']))
                clock.start()
                code_rng=patch('public_beta_email_codes.secrets.randbelow',side_effect=[38421,742813,918364,627183,435928,821637])
                code_rng.start()
                async def advance(request):
                    data=await request.json()
                    if set(data)!={'seconds'} or data['seconds'] not in {60,61,600}:return JSONResponse({},400)
                    clock_offset['seconds']+=data['seconds'];return JSONResponse({'test_clock_advanced':True})
                app.router.routes[0:0]=[Route('/__test/inbox/{username}',inbox),Route('/__test/mail-code/{username}',token),
                                      Route('/__test/advance',advance,methods=['POST'])]
                if resend_mock:
                    async def mail_status(request):
                        value=request.path_params['status']
                        if value not in {'202','500'}:return JSONResponse({},400)
                        api_status['status']=int(value);return JSONResponse({'mock_http_status':int(value)})
                    app.router.routes.insert(0,Route('/__test/mail-status/{status}',mail_status,methods=['POST']))
                log_config={'version':1,'disable_existing_loggers':False,
                    'handlers':{'fixture':{'class':'logging.StreamHandler','stream':log}},
                    'loggers':{'uvicorn.error':{'handlers':['fixture'],'level':'WARNING','propagate':False},
                               'uvicorn.access':{'handlers':[],'level':'CRITICAL','propagate':False}}}
                server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,proxy_headers=False,access_log=False,log_level='warning',log_config=log_config))
                thread=threading.Thread(target=server.run,daemon=True);thread.start()
                for _ in range(100):
                    try:
                        if httpx.get(cfg.origin+'/healthz',trust_env=False).status_code==200:break
                    except httpx.HTTPError:time.sleep(.1)
                print(json.dumps({'origin':cfg.origin,'mail_mode':mode,'http_transport_mock':resend_mock,'real_mail_calls':0,'real_smtp_calls':0,'real_model_calls':0}),flush=True)
                for line in sys.stdin:
                    if line.strip()=='finish':break
                    if line.strip()=='status':
                        with store.db() as db:
                            users=[dict(r) for r in db.execute('SELECT username,status,email_status FROM users ORDER BY username')]
                            count=db.execute('SELECT COUNT(*) FROM instances').fetchone()[0]
                        secrets_to_check=[password,'synthetic-browser-key']+[m['code'] for m in mail.messages]
                        with store.db() as db:
                            secrets_to_check += [r[0] for r in db.execute('SELECT code_hmac FROM email_code_pending WHERE code_hmac IS NOT NULL')]
                            for row in db.execute('SELECT code_history FROM email_code_pending'):
                                secrets_to_check += [entry[1] for entry in json.loads(row[0])]
                        print(json.dumps({'users':users,'instance_count':count,'messages':len(mail.messages),'real_smtp_calls':0,'real_model_calls':0,
                                          'resend_mock_requests':api_status['requests'],'real_mail_calls':0,
                                          'log_secret_free':not any(value in (root/'fixture.log').read_text() for value in secrets_to_check)}),flush=True)
            finally:
                if 'clock' in locals():clock.stop()
                if 'code_rng' in locals():code_rng.stop()
                if server:server.should_exit=True
                if thread:thread.join(timeout=15)
                if control:
                    for row in control.call('list_running')['instances']:control.stop(row['user_id'])
                daemon.terminate();daemon.wait(timeout=15)
                marker.unlink(missing_ok=True)


if __name__=='__main__':main()
