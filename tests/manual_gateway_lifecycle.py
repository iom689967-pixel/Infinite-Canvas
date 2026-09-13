"""Isolated systemd socket + independent daemon acceptance. No generation requests."""
import argparse, asyncio, collections, json, os, pathlib, pwd, re, secrets, socket, sqlite3, subprocess, sys, tempfile, time
import httpx
from websockets.asyncio.client import connect

parser=argparse.ArgumentParser(description='Isolated Linux/systemd lifecycle test; no production units or model calls')
parser.add_argument('--program',required=True)
parser.add_argument('--data-parent',required=True)
parser.add_argument('--python',default=sys.executable)
args=parser.parse_args()
if sys.platform!='linux' or os.geteuid()!=0:raise SystemExit('Requires Linux systemd test host and local operator privileges')
program=pathlib.Path(args.program).resolve();parent=pathlib.Path(args.data_parent).resolve();python=pathlib.Path(args.python).resolve()
if not all(re.fullmatch(r'[A-Za-z0-9/_.-]+',str(p)) for p in [program,parent,python]):raise SystemExit('Test paths must not contain whitespace or control characters')
if not (program/'public_beta_daemon.py').is_file() or not parent.is_dir() or not python.is_file():raise SystemExit('Explicit test paths must exist')
root=pathlib.Path(tempfile.mkdtemp(prefix='mio-lifecycle-',dir=parent))
account=pwd.getpwnam('mio-canvas');os.chown(root,account.pw_uid,account.pw_gid)
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
with socket.socket() as sock:sock.bind(('127.0.0.1',0));proxy_port=sock.getsockname()[1]
prefix='mio-lc-lab-'+str(port);gateway=prefix+'-gateway';daemon=prefix+'-supervisor'
env={'PUBLIC_BETA_ROOT':str(root/'g'),'PUBLIC_BETA_INSTANCES_ROOT':str(root/'i'),
     'PUBLIC_BETA_BACKUP_ROOT':str(root/'b'),'GATEWAY_PORT':str(port),
     'PUBLIC_BETA_SUPERVISOR_SOCKET':str(root/'g/ctl.sock'),
     'INSTANCE_PORT_START':'35100','INSTANCE_PORT_END':'35110','MAX_PUBLIC_USERS':'2',
     'MAX_RUNNING_INSTANCES':'2','MIN_FREE_DISK_BYTES':'10737418240',
     'PUBLIC_BETA_REGISTRATION_MODE':'open','PYTHONDONTWRITEBYTECODE':'1'}
envfile=root/'lab.env';envfile.write_text('\n'.join(k+'='+v for k,v in env.items())+'\n');envfile.chmod(0o600)
unitroot=pathlib.Path('/run/systemd/system')
common=f'User=mio-canvas\nGroup=mio-canvas\nEnvironmentFile={envfile}\nWorkingDirectory={program}\nStandardOutput=null\nStandardError=null\nTimeoutStopSec=45\n'
files={
    daemon+'.service':'[Service]\n'+common+f'ExecStart={python} {program}/public_beta_daemon.py\nExecStartPost={python} {program}/public_beta_daemon.py ready\nKillMode=process\nMemoryMax=550M\n',
    gateway+'.service':f'[Unit]\nRequires={gateway}.socket\nWants={daemon}.service\nAfter={gateway}.socket {daemon}.service\n[Service]\n'+common+f'ExecStart={python} {program}/public_beta.py\nSockets={gateway}.socket\nKillMode=control-group\nMemoryMax=200M\n',
    gateway+'.socket':f'[Socket]\nListenStream=127.0.0.1:{port}\nService={gateway}.service\nBacklog=1024\nNoDelay=true\n'}
def ctl(*args):subprocess.run(['systemctl',*args],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def records():
    with sqlite3.connect('file:'+str(root/'g/gateway.sqlite3')+'?mode=ro',uri=True) as db:
        return list(db.execute("SELECT user_id,instance_id,pid,process_started,assigned_port,status FROM instances ORDER BY user_id"))
for name,content in files.items():(unitroot/name).write_text(content)
ctl('daemon-reload')

async def measure():
    origin=f'http://127.0.0.1:{port}';proxy_origin=f'http://127.0.0.1:{proxy_port}';password=secrets.token_urlsafe(24)
    clients=[]
    async def ready(c):
        for _ in range(150):
            try:
                if (await c.get('/healthz')).status_code==200:return
            except httpx.HTTPError:pass
            await asyncio.sleep(.1)
        raise RuntimeError('lab_gateway_not_ready')
    try:
        for name in ['restart-a','restart-b']:
            c=httpx.AsyncClient(base_url=proxy_origin,trust_env=False,timeout=8,headers={'Origin':origin});clients.append(c)
            await ready(c)
            assert (await c.post('/api/beta/register',json={'username':name,'password':password,'confirmation':password})).status_code==200
            csrf=(await c.get('/api/beta/me')).json()['csrf']
            assert (await c.post('/api/beta/enter',headers={'X-CSRF-Token':csrf})).status_code==200
            c.headers['X-CSRF-Token']=(await c.get('/api/auth/me')).json()['csrf']
        a,b=clients
        canvas=(await a.post('/api/canvases',json={'title':'Lifecycle fixture'})).json()['canvas']['id']
        assert (await b.get('/api/canvases/'+canvas)).status_code==404
        before=records();counts=collections.Counter();errors=collections.Counter();waits=[];running=True
        async def traffic(path):
            async with httpx.AsyncClient(base_url=proxy_origin,trust_env=False,timeout=8,cookies=a.cookies) as c:
                while running:
                    began=time.monotonic()
                    try:counts[(await c.get(path)).status_code]+=1
                    except httpx.HTTPError as exc:errors[type(exc).__name__]+=1
                    waits.append(time.monotonic()-began);await asyncio.sleep(.04)
        tasks=[asyncio.create_task(traffic(p)) for p in ['/','/login','/api/beta/me','/api/canvases']]
        ws_headers={'Cookie':'; '.join(k+'='+v for k,v in a.cookies.items())}
        uri=origin.replace('http:','ws:')+'/ws/stats'
        async with connect(uri,origin=origin,additional_headers=ws_headers,proxy=None) as ws:
            assert await asyncio.wait_for(ws.recv(),5)
            await asyncio.sleep(.7)
            restart_ms=[]
            for _ in range(3):
                began=time.monotonic();await asyncio.to_thread(ctl,'restart',gateway)
                restart_ms.append(round((time.monotonic()-began)*1000,1))
                await ready(a);await asyncio.sleep(.5)
        await ready(a)
        async with connect(uri,origin=origin,additional_headers=ws_headers,proxy=None) as ws:
            assert await asyncio.wait_for(ws.recv(),5)
        await asyncio.sleep(1);running=False;await asyncio.gather(*tasks)
        after=records();assert before==after
        states=[(await c.get('/api/auth/me')).status_code for c in clients]
        assert states==[200,200]
        assert (await a.get('/api/canvases/'+canvas)).status_code==200
        assert (await a.get('/api/instance/providers')).status_code==200
        # Restart the actual independent authority, then reconcile the still-live workers.
        await asyncio.to_thread(ctl,'restart',daemon)
        await asyncio.sleep(.8)
        await asyncio.to_thread(ctl,'restart',gateway)
        await ready(a)
        assert before==records()
        assert all([(await c.get('/api/auth/me')).status_code==200 for c in clients])
        result={'requests':sum(counts.values())+sum(errors.values()),'http_status_counts':dict(counts),
                'exception_classes':dict(errors),'max_wait_ms':round(max(waits)*1000,1),'restart_command_ms':restart_ms,
                'instance_pids_unchanged':True,'instance_count':len(before),'sessions_after':[200,200],
                'canvas_after':200,'provider_after':200,'b_canvas_isolation':404,'websocket_reconnected':True,
                'supervisor_restart_reconcile':True,'paid_requests':0,'via_isolated_caddy':True,'controlled_gateway_restarts':3}
        print(json.dumps(result),flush=True)
        (root/'safe-result.json').write_text(json.dumps(result))
        assert not errors and not any(status>=500 for status in counts)
    finally:
        for c in clients:await c.aclose()

caddyfile=root/'Caddyfile'
caddyfile.write_text('{\n admin off\n auto_https off\n}\n'+f'http://127.0.0.1:{proxy_port} {{\n reverse_proxy 127.0.0.1:{port} {{\n header_up Host 127.0.0.1:{port}\n }}\n}}\n')
caddy_binary=subprocess.check_output(['which','caddy'],text=True).strip()
subprocess.run([caddy_binary,'validate','--config',str(caddyfile),'--adapter','caddyfile'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
proxy=subprocess.Popen([caddy_binary,'run','--config',str(caddyfile),'--adapter','caddyfile'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    ctl('start',daemon+'.service',gateway+'.socket',gateway+'.service')
    asyncio.run(measure())
finally:
    # Explicitly stop only recorded test instances through the restricted IPC.
    try:
        cleanup="import os;os.environ.update("+repr(env)+");from public_beta_store import BetaConfig,GatewayStore;from public_beta_ipc import SupervisorClient;s=GatewayStore(BetaConfig.from_env());c=SupervisorClient(s);[c.stop(r['user_id']) for r in c.call('list_running')['instances']]"
        subprocess.run(['runuser','-u','mio-canvas','--',str(python),'-c',cleanup],cwd=program,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45)
    finally:
        proxy.terminate();proxy.wait(timeout=10)
        for name in [gateway+'.socket',gateway+'.service',daemon+'.service']:
            subprocess.run(['systemctl','stop',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for name in files:(unitroot/name).unlink(missing_ok=True)
        ctl('daemon-reload')
