"""Executable old-code rehearsal; loopback, temporary accounts and fake keys only.

Run with a pinned Caddy fixture. Old trees are git archives, never patched. The
initial mixed layout matches the observed deployment shape, not historical PIDs.
"""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from PIL import Image
from test_instance_models import ModelMock, configuration
from public_beta_maintenance import caddy_barrier, initialize, Maintenance, inspect, set_phase, ledger_counts, wait_for_drain
from instance_maintenance import process_identity, DETAIL

ROOT = Path(__file__).resolve().parents[1]
OLD = '5449a692da33200f8d621a8974414da5e4c9de7c'
OLDER = 'b5c0d41888ddf15cea8c632f5d2bee2cf80efa9b'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); return sock.getsockname()[1]


def eventually(test, timeout=15):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        value = test()
        if value:
            return value
        time.sleep(.04)
    raise AssertionError('Rehearsal deadline; stop upgrade, do not kill to fake draining')


class HeldMock(ModelMock):
    def do_POST(self):
        if ':generateContent' in self.path:
            self.server.llm_entered.set()
            assert self.server.llm_finish.wait(20), 'Mock-only LLM hold deadline'
        return super().do_POST()


class Rehearsal:
    def __init__(self, caddy, root):
        self.caddy, self.root = caddy, root
        self.procs, self.logs, self.evidence = {}, [], []
        self.env = {'PATH': os.defpath, 'LANG': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
            'TMPDIR': str(root), 'XDG_CONFIG_HOME': str(root/'caddy-config'), 'XDG_DATA_HOME': str(root/'caddy-data'),
            'PUBLIC_BETA_ROOT': str(root/'gateway'), 'PUBLIC_BETA_INSTANCES_ROOT': str(root/'instances'),
            'PUBLIC_BETA_BACKUP_ROOT': str(root/'backups'), 'PUBLIC_BETA_SUPERVISOR_SOCKET': str(root/'ipc/supervisor.sock'),
            'MIO_MAIL_MODE': 'disabled', 'MIN_FREE_DISK_BYTES': '0', 'MAX_RUNNING_INSTANCES': '2',
            'INSTANCE_PORT_START': '34500', 'MAX_PUBLIC_USERS': '4'}
        # Allocate a contiguous, free mock-only worker range.
        first = int(self.env['INSTANCE_PORT_START']); self.env['INSTANCE_PORT_END'] = str(min(65535, first+50))
        self.gateway_port, self.entry_port, self.admin_port, self.ingress_port = free_port(), free_port(), free_port(), free_port()
        self.unrelated_upstream_port, self.unrelated_entry_port = free_port(), free_port()
        self.env['GATEWAY_PORT'] = str(self.gateway_port)
        self.origin = f'https://127.0.0.1:{self.entry_port}'
        self.env['PUBLIC_BETA_ORIGIN'] = self.origin
        self.env['PUBLIC_BETA_TRUSTED_PROXIES'] = '127.0.0.1'
        self.gateway_origin = f'http://127.0.0.1:{self.gateway_port}'
        self.admin_origin = f'http://127.0.0.1:{self.admin_port}'
        self.mock = ThreadingHTTPServer(('127.0.0.1', 0), HeldMock)
        self.mock.calls, self.mock.jobs, self.mock.media = [], {}, {}
        self.mock.credentials = {hashlib.sha256(b'fake-rehearsal-only').hexdigest(): 'alice'}
        image = BytesIO(); Image.new('RGB', (24, 32), 'blue').save(image, 'PNG'); self.mock.image = image.getvalue()
        self.mock.origin = f'http://127.0.0.1:{self.mock.server_port}'; self.mock.finish_waiting = False
        self.mock.llm_entered, self.mock.llm_finish = threading.Event(), threading.Event()
        self.env['PUBLIC_BETA_MOCK_UPSTREAMS'] = f'127.0.0.1:{self.mock.server_port}'
        self.thread = threading.Thread(target=self.mock.serve_forever, daemon=True); self.thread.start()

    def record(self, stage, **safe):
        item = {'stage': stage, **safe}; self.evidence.append(item)
        print(json.dumps(item), flush=True)

    def archive(self, sha, name):
        path = self.root/name; path.mkdir()
        data = subprocess.check_output(['git', 'archive', sha], cwd=ROOT)
        with tarfile.open(fileobj=BytesIO(data)) as archive:
            archive.extractall(path, filter='data')
        return path

    def run(self, program, code, *, env=None):
        result = subprocess.run([sys.executable, '-c', code], cwd=program, env=env or self.env,
                                capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise AssertionError('Offline command failed: '+result.stderr[-4000:])
        return json.loads(result.stdout) if result.stdout.strip() else None

    def start(self, name, argv, program, env=None):
        log = (self.root/(name+'.log')).open('a+'); self.logs.append(log)
        process = subprocess.Popen(argv, cwd=program, env=env or self.env, stdout=log, stderr=log)
        self.procs[name] = process; return process

    def stop(self, name):
        process = self.procs.pop(name)
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=30)

    def program_loaded(self,pid,program,filename):
        command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True,timeout=3)
        assert str(program/filename) in command, 'Actual process still points to a different program'
        return True

    def readiness(self):
        def check():
            try:
                return httpx.get(self.gateway_origin+'/healthz', headers=self.proxy_health_headers(),
                                 trust_env=False, timeout=1).status_code == 200
            except httpx.HTTPError:
                return False
        eventually(check)

    def write_config(self, value, name):
        path = self.root/name; path.write_text(json.dumps(value)); path.chmod(0o600); return path

    def reload(self, value, name):
        path = self.write_config(value, name)
        subprocess.run([self.caddy, 'validate', '--config', str(path)], check=True, capture_output=True, timeout=15)
        subprocess.run([self.caddy, 'reload', '--address', f'127.0.0.1:{self.admin_port}', '--config', str(path)],
                       check=True, capture_output=True, timeout=15)

    def metrics(self):
        response = httpx.get(self.admin_origin+'/metrics', trust_env=False, timeout=3)
        response.raise_for_status()
        lines = [line for line in response.text.splitlines() if line.startswith('caddy_http_requests_in_flight')]
        if not lines:
            raise AssertionError('Caddy request activity metrics missing: '+response.text[:1000])
        self.metric_lines = lines
        return sum(float(line.rsplit(' ', 1)[1]) for line in response.text.splitlines()
                   if line.startswith('caddy_http_requests_in_flight{') and 'handler="subroute"' in line
                   and 'server="mio"' in line)

    def accounts(self):
        with sqlite3.connect(self.root/'gateway/gateway.sqlite3') as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT i.*,u.username FROM instances i JOIN users u ON u.id=i.user_id ORDER BY u.username')]

    def enter(self, client, name):
        response = client.post('/api/beta/login', json={'username': name, 'password': 'Mock-rehearsal-password-42'})
        assert response.status_code == 200, response.text
        csrf = client.get('/api/beta/me').json()['csrf']
        response = client.post('/api/beta/enter', headers={'X-CSRF-Token': csrf})
        assert response.status_code == 200, response.text
        client.headers['X-CSRF-Token'] = client.get('/api/auth/me').json()['csrf']

    def execute(self):
        old, older = self.archive(OLD, 'old-5449'), self.archive(OLDER, 'older-b5c0')
        # Genuine old supervisor/Gateway; one older worker then one 5449 worker.
        self.run(old, "from public_beta_store import BetaConfig,GatewayStore;from public_beta_supervisor import Supervisor;"
            "from pathlib import Path;import json; s=Supervisor(GatewayStore(BetaConfig.from_env()));"
            "s.program=Path("+repr(str(older))+");s.register('alice','Mock-rehearsal-password-42','Mock-rehearsal-password-42','fixture');"
            "s.program=Path("+repr(str(old))+");s.register('bob','Mock-rehearsal-password-42','Mock-rehearsal-password-42','fixture')")
        for account in self.accounts():
            private = Path(account['data_root'])/'.auth'
            (private/'credentials').mkdir(exist_ok=True, mode=0o700)
            for name in ('gemini.key', 'kie.key'):
                (private/'credentials'/name).write_text('fake-rehearsal-only'); (private/'credentials'/name).chmod(0o600)
            # Existing shared-network fixture, plus the user's saved personal API.
            policy = configuration(self.mock.server_port)
            for p in policy['providers']:
                for limit in p['models'].values():
                    limit['timeout_seconds'] = 15
            policy['personal_providers'] = []
            (private/'model-access.json').write_text(json.dumps(policy)); (private/'model-access.json').chmod(0o600)
        self.start('supervisor', [sys.executable, str(old/'public_beta_daemon.py')], old)
        eventually(lambda: (self.root/'ipc/supervisor.sock').exists())
        self.start('gateway', [sys.executable, str(old/'public_beta.py')], old); self.readiness()
        gate_root = self.root/'control'; initialize(gate_root); gate = Maintenance(gate_root); set_phase(gate,'open')
        self.start('ingress',[sys.executable,str(ROOT/'public_beta_maintenance_ingress.py'),'--root',str(gate_root),
            '--target',self.gateway_origin,'--public-host',f'127.0.0.1:{self.entry_port}',
            '--port',str(self.ingress_port)],ROOT)
        eventually(lambda: self.ingress_ready())
        unrelated_source = self.root/'unrelated_mock.py'
        unrelated_source.write_text('''import asyncio
async def app(scope, receive, send):
    if scope['type']=='lifespan':
        while True:
            message=await receive()
            await send({'type':message['type']+'.complete'})
            if message['type']=='lifespan.shutdown':return
    elif scope['type']=='websocket':
        await receive();await send({'type':'websocket.accept'})
        while True:
            message=await receive()
            if message['type']=='websocket.disconnect':return
            await send({'type':'websocket.send','text':message.get('text','')})
    else:
        stream=scope['path']=='/stream'
        await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/event-stream' if stream else b'text/plain')]})
        if stream:
            for number in range(15):
                await send({'type':'http.response.body','body':('data: '+str(number)+'\\n\\n').encode(),'more_body':True})
                await asyncio.sleep(.1)
        await send({'type':'http.response.body','body':b'finished','more_body':False})
''')
        self.start('unrelated', [sys.executable,'-m','uvicorn','unrelated_mock:app','--host','127.0.0.1',
            '--port',str(self.unrelated_upstream_port),'--no-access-log','--log-level','warning'],self.root)
        # Separate Mio server label makes metrics specific; an unrelated shared
        # Caddy host remains byte-for-byte unchanged by barrier preparation.
        proxy = {'handler': 'reverse_proxy', 'upstreams': [{'dial': f'127.0.0.1:{self.ingress_port}'}]}
        # Real HTTPS/proxied Origin, Secure cookies and CSRF boundaries, with a
        # private one-day fixture certificate; no system CA installation.
        tls_config=self.root/'fixture-tls.cnf'
        tls_config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n'
                              '[dn]\nCN=127.0.0.1\n[ext]\nsubjectAltName=IP:127.0.0.1\n')
        certificate,key=self.root/'fixture.pem',self.root/'fixture.key'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                        '-config',str(tls_config),'-out',str(certificate),'-keyout',str(key)],
                       check=True,capture_output=True,timeout=15)
        key.chmod(0o600)
        site = {'match': [{'host': ['127.0.0.1']}], 'handle': [{'handler': 'subroute', 'routes': [{'handle': [proxy]}]}], 'terminal': True}
        config = {'admin': {'listen': f'127.0.0.1:{self.admin_port}'}, 'apps': {
            'tls':{'certificates':{'load_files':[{'certificate':str(certificate),'key':str(key)}]}},'http': {
            'metrics': {}, 'servers': {'mio': {'automatic_https': {'disable': True}, 'tls_connection_policies':[{}],
                'listen': [f'127.0.0.1:{self.entry_port}'], 'routes': [site]},
            'unrelated': {'listen': [f'127.0.0.1:{self.unrelated_entry_port}'], 'routes': [{'handle': [
                {'handler': 'reverse_proxy','upstreams':[{'dial':f'127.0.0.1:{self.unrelated_upstream_port}'}]}]}]}}}}}
        path = self.write_config(config, 'caddy-open.json')
        self.start('caddy', [self.caddy, 'run', '--config', str(path)], self.root)
        eventually(lambda: self.caddy_ready())
        client = httpx.Client(base_url=self.origin, headers={'Origin': self.origin}, trust_env=False, verify=False, timeout=25)
        self.clients = [client]; self.enter(client, 'alice')
        # Save a real old personal Provider and canvas, then record raw bytes/ref.
        saved = {'id': 'same-api', 'name': 'Private', 'protocol': 'openai', 'base_url': self.mock.origin,
                 'api_key': 'fake-rehearsal-only', 'image_models': ['gpt-image-2','gpt-image-2.5-flare','gpt-image-2.5-sunburst']}
        response = client.put('/api/instance/provider-settings', json=[saved]); assert response.status_code == 200, response.text
        canvas = client.post('/api/canvases', json={'title': 'Mock preserved canvas'}).json()['canvas']
        canvas_id = canvas['id']; canvas['nodes'] = [{'id': 'original-node', 'type': 'image', 'x': 0, 'y': 0,
            'prompt': 'saved input', 'generationHistory': [{'id': 'old-generation'}]}]
        response = client.put('/api/canvases/'+canvas_id, json=canvas); assert response.status_code == 200, response.text
        # Start the second worker through the existing supervisor management API.
        bob = next(row for row in self.accounts() if row['username']=='bob')
        self.run(old, "from public_beta_store import BetaConfig,GatewayStore;from public_beta_ipc import SupervisorClient;"
            "s=SupervisorClient(GatewayStore(BetaConfig.from_env()));s.call('start',instance_id="+repr(bob['instance_id'])+")")
        before = self.accounts(); provider_file = Path(before[0]['data_root'])/'.auth/model-access.json'
        for account in before:
            self.program_loaded(account['pid'],older if account['username']=='alice' else old,'public_beta_worker.py')
        provider_bytes = provider_file.read_bytes()
        self.record('old_started', gateway_pid=self.procs['gateway'].pid, supervisor_pid=self.procs['supervisor'].pid,
                    workers=[{'pid': row['pid'], 'program': 'b5c0' if row['username']=='alice' else '5449', 'start': row['process_started']} for row in before])
        pool = ThreadPoolExecutor(max_workers=4)
        llm = pool.submit(client.post, '/api/canvas-llm', json={'provider': 'atelier-text', 'model': 'gemini-mock-vision', 'message': 'mock held LLM'})
        assert self.mock.llm_entered.wait(10)
        response = client.post('/api/canvas-image-tasks', json={'provider_id': 'atelier-images','model':'gpt-image-2',
            'prompt': 'WAIT_IMAGE','resolution':'1K','aspect_ratio':'1:1','size':'1024x1024','n':1,
            'canvas_id': canvas_id, 'node_id': 'original-node', 'generation_id': 'old-generation'})
        assert response.status_code == 200, response.text; task_id = response.json()['task_id']
        eventually(lambda: any(kind=='create' for kind,_,_ in self.mock.calls))
        upload_started, upload_finish = threading.Event(), threading.Event()
        def slow_upload_body():
            yield b'--mock-boundary\r\nContent-Disposition: form-data; name="files"; filename="synthetic.png"\r\nContent-Type: image/png\r\n\r\n'
            upload_started.set();assert upload_finish.wait(15)
            yield self.mock.image+b'\r\n--mock-boundary--\r\n'
        slow_upload=pool.submit(client.post,'/api/ai/upload',content=slow_upload_body(),
            headers={'Content-Type':'multipart/form-data; boundary=mock-boundary'})
        assert upload_started.wait(3)
        eventually(lambda: gate.status()['by_phase'].get('upload',0)==1)
        assert gate.status()['active'] >= 1
        old_metric_before=self.metrics()
        # Actual shared Caddy reload effect: finite mock SSE continues; default
        # existing WebSocket closes even though its unrelated route is unchanged.
        from websockets.sync.client import connect
        unrelated_socket = connect(f'ws://127.0.0.1:{self.unrelated_entry_port}/ws', proxy=None, open_timeout=3)
        unrelated_socket.send('before'); assert unrelated_socket.recv(timeout=3)=='before'
        stream_entered = threading.Event()
        def unrelated_stream():
            with httpx.stream('GET',f'http://127.0.0.1:{self.unrelated_entry_port}/stream',trust_env=False,timeout=5) as stream:
                result=[]
                for chunk in stream.iter_text():
                    result.append(chunk);stream_entered.set()
                return ''.join(result)
        shared_stream=pool.submit(unrelated_stream);assert stream_entered.wait(3)
        set_phase(gate,'draining')
        barrier = caddy_barrier(config, '127.0.0.1'); self.reload(barrier, 'caddy-draining.json')
        assert gate.status()['active'] >= 1
        shared_ws_closed=False
        try:
            unrelated_socket.send('after'); unrelated_socket.recv(timeout=3)
        except __import__('websockets').exceptions.ConnectionClosed:
            shared_ws_closed=True
        finally:
            unrelated_socket.close()
        assert shared_ws_closed, 'Expected Caddy default WebSocket unload behaviour changed; review deployment impact'
        assert shared_stream.result(timeout=5).endswith('finished')
        self.record('shared_caddy_reload_effect', existing_sse_completed=True, existing_websocket_closed=True,
                    unrelated_route_unchanged=barrier['apps']['http']['servers']['unrelated']==config['apps']['http']['servers']['unrelated'],
                    business_pids_unchanged=True)
        self.record('barrier_enabled_without_old_restart', original_pids_unchanged=self.accounts()==before,
                    old_llm_counted=gate.status()['active']>=1, caddy_metric_before_reload=old_metric_before,
                    caddy_metric_can_reset=True)
        submits = sum(k in {'create','upload','llm'} for k,_,_ in self.mock.calls)
        for route in ('/api/canvas-llm','/api/canvas-image-tasks','/api/canvas-video','/api/chat/agent',
                      '/api/chat/stream','/api/runninghub/upload-asset','/api/temp-sh/upload','/api/beta/register'):
            response = client.post(route, json={}); assert response.status_code==503, (route,response.status_code)
            assert response.json()=={'detail': DETAIL}
        assert sum(k in {'create','upload','llm'} for k,_,_ in self.mock.calls)==submits
        self.record('new_work_rejected', expected_503=8, unexpected_500_502=0, upstream_calls_from_rejections=0)
        timed_out=wait_for_drain(gate,[Path(row['data_root']) for row in before],.02,
                                 legacy_blockers=('legacy_unobserved_process',))
        assert timed_out['timed_out'] and not timed_out['restart_safe']
        assert self.accounts()==before
        self.record('drain_timeout_stopped_upgrade', active_requests_retained=timed_out['active'],
                    worker_pids_unchanged=True, slow_upload_counted=True, forced_stop=0)
        assert ledger_counts([Path(before[0]['data_root'])])['pending_tasks'] >= 1
        upload_finish.set();uploaded=slow_upload.result(timeout=10);assert uploaded.status_code==200,uploaded.text
        self.mock.llm_finish.set(); response = llm.result(timeout=20); assert response.status_code==200, response.text
        self.mock.finish_waiting = True
        def task_complete():
            response = client.get('/api/canvas-image-tasks/'+task_id); assert response.status_code==200
            value = response.json()
            return value if value['status']=='succeeded' and not value['local_wait_active'] else None
        task = eventually(task_complete); pool.shutdown()
        eventually(lambda: gate.status()['active']==0)
        assert not any(ledger_counts([Path(row['data_root']) for row in before]).values())
        sealed = caddy_barrier(config, '127.0.0.1', sealed=True); self.reload(sealed, 'caddy-sealed.json')
        assert gate.status()['active']==0
        self.record('legacy_drained', old_llm_response_completed=True, runner_local_wait_active=False,
                    original_receipt_downloaded=True, durable_ingress_activity=0, uncertain_submissions=0,
                    scope='all mock requests began after observer installation; no retroactive production claim')
        # Native control remains sealed across business process replacements.
        assert inspect(gate, [Path(row['data_root']) for row in before], seal=True)['restart_safe']
        self.env['MIO_MAINTENANCE_ROOT'] = str(gate_root)
        self.stop('gateway'); self.stop('supervisor')
        self.start('supervisor', [sys.executable,str(ROOT/'public_beta_daemon.py')], ROOT)
        eventually(lambda: (self.root/'ipc/supervisor.sock').exists())
        self.start('gateway', [sys.executable,str(ROOT/'public_beta.py')], ROOT); self.readiness()
        adopted = self.accounts(); assert [r['pid'] for r in adopted]==[r['pid'] for r in before]
        self.program_loaded(self.procs['supervisor'].pid,ROOT,'public_beta_daemon.py')
        self.program_loaded(self.procs['gateway'].pid,ROOT,'public_beta.py')
        target_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        target_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip())
        self.record('new_supervisor_adopted', old_worker_pids_retained=True,loaded_program_verified=True,
                    target_revision=target_revision,target_worktree_dirty=target_dirty)
        rows=[]
        for account in before:
            iid = account['instance_id']
            self.run(ROOT, "from public_beta_store import BetaConfig,GatewayStore;from public_beta_ipc import SupervisorClient;"
                "import json;s=SupervisorClient(GatewayStore(BetaConfig.from_env()));"
                "s.call('stop',instance_id="+repr(iid)+");print(json.dumps(s.call('start',instance_id="+repr(iid)+")))")
            current = next(row for row in self.accounts() if row['instance_id']==iid)
            assert current['pid']!=account['pid']; assert current['data_root']==account['data_root']
            self.program_loaded(current['pid'],ROOT,'public_beta_worker.py')
            assert len([r for r in self.accounts() if r['status']=='running'])<=2
            assert gate.state()['phase']=='sealed'; assert provider_file.read_bytes()==provider_bytes
            rows.append({'before': account['pid'], 'after': current['pid'], 'start':current['process_started'],
                         'loaded_program_verified':True,'target_revision':target_revision,
                         'data_root_preserved': True, 'maintenance': 'sealed'})
        self.record('serial_roll_complete', workers=rows, capacity=2)
        direct_config=json.loads(json.dumps(config))
        direct_config['apps']['http']['servers']['mio']['routes'][0]['handle'][0]['routes'][0]['handle'][0]={
            'handler':'reverse_proxy','upstreams':[{'dial':f'127.0.0.1:{self.gateway_port}'}]}
        self.reload(direct_config, 'caddy-restored-while-native-sealed.json')
        assert client.post('/api/canvas-llm',json={}).status_code==503
        set_phase(gate,'draining')
        self.enter(client,'alice')  # Restart legitimately invalidates worker sessions.
        assert client.get('/api/canvases/'+canvas_id).json()['canvas']['nodes'][0]['id']=='original-node'
        assert any(entry.get('task_id')==task_id for entry in client.get('/api/history').json())
        assert client.get(task['result']['images'][0]).status_code==200
        providers=client.get('/api/instance/provider-settings').json()['providers']
        catalog=client.get('/api/config').json()['api_providers']
        assert len(providers[0]['image_models'])==3
        assert len(next(p for p in catalog if p['id']=='same-api')['image_models'])==3
        assert provider_file.read_bytes()==provider_bytes
        set_phase(gate,'open')
        response=client.post('/api/canvas-llm',json={'provider':'atelier-text','model':'gemini-mock-vision','message':'mock after open'})
        assert response.status_code==200,response.text
        self.record('reopened', preserved_provider_models=3, config_models=3, node_history_media_preserved=True,
                    relogin_required=True, real_models=0, real_mail=0, real_uploads=0)
        # Rollback is code-only, under maintenance; no database restore or replay.
        set_phase(gate,'draining'); eventually(lambda: inspect(gate,[Path(r['data_root']) for r in self.accounts()],seal=True)['restart_safe'])
        self.reload(sealed,'caddy-rollback-sealed.json')
        self.stop('gateway'); self.stop('supervisor')
        self.start('supervisor',[sys.executable,str(old/'public_beta_daemon.py')],old)
        eventually(lambda: (self.root/'ipc/supervisor.sock').exists())
        for account in self.accounts():
            self.run(old,"from public_beta_store import BetaConfig,GatewayStore;from public_beta_ipc import SupervisorClient;"
                "s=SupervisorClient(GatewayStore(BetaConfig.from_env()));s.call('stop',instance_id="+repr(account['instance_id'])+");s.call('start',instance_id="+repr(account['instance_id'])+")")
        self.start('gateway',[sys.executable,str(old/'public_beta.py')],old);self.readiness()
        assert provider_file.read_bytes()==provider_bytes
        assert client.post('/api/canvas-llm',json={}).status_code==503
        assert sum(kind=='create' for kind,_,_ in self.mock.calls)==1, 'Original generation unexpectedly replayed'
        self.record('rollback', old_code_started=True, current_data_preserved=True, external_barrier_retained=True,
                    no_database_restore=True, original_mock_image_submissions=1, generation_replay=0)
        return self.evidence

    def caddy_ready(self):
        try:
            response = httpx.get(self.origin+'/healthz', trust_env=False, verify=False, timeout=1)
            if response.status_code != 200:
                raise AssertionError('Temporary Caddy readiness returned '+str(response.status_code)+': '+response.text[:300])
            return True
        except httpx.HTTPError:
            return False

    def ingress_ready(self):
        try:
            return httpx.get(f'http://127.0.0.1:{self.ingress_port}/healthz',headers={
                **self.proxy_health_headers()},trust_env=False,timeout=1).status_code==200
        except httpx.HTTPError:
            return False

    def proxy_health_headers(self):
        return {'Host':f'127.0.0.1:{self.entry_port}', 'X-Forwarded-Proto':'https','X-Forwarded-For':'127.0.0.1'}

    def close(self):
        self.mock.llm_finish.set(); self.mock.finish_waiting=True
        for client in getattr(self,'clients',[]):
            client.close()
        # Only temp workers and processes. Never use this teardown as drain proof.
        try:
            rows=self.accounts()
            if (self.root/'ipc/supervisor.sock').exists():
                self.run(ROOT,"from public_beta_store import BetaConfig,GatewayStore;from public_beta_ipc import SupervisorClient;"
                    "s=SupervisorClient(GatewayStore(BetaConfig.from_env()));"
                    +';'.join("s.call('stop',instance_id="+repr(r['instance_id'])+")" for r in rows))
        except Exception:
            for row in self.accounts() if (self.root/'gateway/gateway.sqlite3').exists() else []:
                if row['pid'] and process_identity(row['pid'])==row['process_started']:
                    os.kill(row['pid'],signal.SIGTERM)
        for name in list(self.procs):
            self.stop(name)
        self.mock.shutdown();self.mock.server_close();self.thread.join(timeout=3)
        for log in self.logs:
            log.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--caddy',required=True);parser.add_argument('--evidence',required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='mio-first-upgrade-') as temporary:
        rehearsal=Rehearsal(str(Path(args.caddy).resolve()),Path(temporary).resolve())
        try:
            evidence=rehearsal.execute()
            Path(args.evidence).write_text(json.dumps(evidence,indent=2))
        except Exception:
            Path(args.evidence).write_text(json.dumps([*rehearsal.evidence,{
                'stage':'rehearsal_failed','restart_safe':False,'action':'stop_upgrade'}],indent=2))
            for name in ('caddy', 'gateway', 'supervisor', 'ingress'):
                path = rehearsal.root/(name+'.log')
                if path.exists():
                    print(name, path.read_text()[-4000:], file=sys.stderr)
            raise
        finally:
            rehearsal.close()


if __name__=='__main__':
    main()
