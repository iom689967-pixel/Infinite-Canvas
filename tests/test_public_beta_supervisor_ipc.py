"""Real UDS, real workers, persistent sessions; only synthetic local accounts."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx
from email_helpers import complete_registration

from public_beta import create_app
from public_beta_daemon import daemon_server, dispatch
from public_beta_ipc import SupervisorClient
from public_beta_store import BetaConfig, GatewayStore, BetaError
from public_beta_supervisor import Supervisor
from instance_maintenance import initialize, Maintenance
from public_beta_maintenance import set_phase
from test_instance_isolation import free_port
from test_public_beta import PASSWORD


class SupervisorIPCTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mio-ipc-',dir='/tmp')
        root=Path(self.temp.name).resolve()
        self.config=BetaConfig(root/'g',root/'i',port=free_port(),min_free_disk=0,
                               mail_mode='mock',register_limit=100,login_limit=100,supervisor_socket=str(root/'g'/'ctl.sock'),
                               maintenance_root=root/'control')
        initialize(self.config.maintenance_root)
        set_phase(Maintenance(self.config.maintenance_root),'open')
        self.store=GatewayStore(self.config)
        self.context=daemon_server(self.config);self.server=self.context.__enter__()
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.02},daemon=True);self.thread.start()
        self.app=create_app(self.config)
        self.client=self.new_client();self.clients=[self.client]

    def new_client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url=self.config.origin,
                                 headers={'Origin':self.config.origin})

    async def asyncTearDown(self):
        for client in self.clients:await client.aclose()
        for instance in self.server.supervisor.list_running():
            await asyncio.to_thread(self.server.supervisor.stop,instance['user_id'])
        await asyncio.to_thread(self.server.shutdown);self.thread.join()
        self.context.__exit__(None,None,None)
        # Reap workers spawned by an earlier test daemon in this Python process.
        for child in getattr(self,'old_children',[]):child.wait(timeout=10)
        self.temp.cleanup()

    async def register(self,name='alice',enter=True):
        response=await complete_registration(self.client,self.app,name,PASSWORD)
        self.assertEqual(response.status_code,200,response.text)
        principal=self.store.principal(self.client.cookies.get('mio_beta_session'))
        if enter:
            csrf=(await self.client.get('/api/beta/me')).json()['csrf']
            response=await self.client.post('/api/beta/enter',headers={'X-CSRF-Token':csrf})
            self.assertEqual(response.status_code,200,response.text)
            self.client.headers['X-CSRF-Token']=(await self.client.get('/api/auth/me')).json()['csrf']
        return self.store.instance(principal['id'])

    async def test_gateway_lifespan_restart_preserves_two_pids_sessions_canvas_and_provider(self):
        a=await self.register()
        canvas=(await self.client.post('/api/canvases',json={'title':'Preserved'})).json()['canvas']
        a_client=self.client
        self.client=self.new_client();self.clients.append(self.client)
        b=await self.register('bob');b_client=self.client
        async with self.app.router.lifespan_context(self.app):pass
        self.assertIsInstance(self.app.state.supervisor,SupervisorClient)
        self.assertFalse(hasattr(self.app.state.supervisor,'children'))
        self.app=create_app(self.config)
        async with self.app.router.lifespan_context(self.app):
            for old,instance in [(a_client,a),(b_client,b)]:
                new=self.new_client();self.clients.append(new);new.cookies.update(old.cookies)
                self.assertEqual((await new.get('/api/auth/me')).status_code,200)
                self.assertEqual((await new.get('/api/instance/providers')).status_code,200)
                self.assertEqual((await new.get('/api/canvases')).status_code,200)
                self.assertEqual(self.store.instance(instance['user_id'])['pid'],instance['pid'])
                status=(await new.get('/api/canvases/'+canvas['id'])).status_code
                self.assertEqual(status,200 if instance==a else 404)

    async def test_daemon_restart_reconciles_without_restarting_worker_or_session(self):
        a=await self.register()
        self.old_children=list(self.server.supervisor.children.values())
        await asyncio.to_thread(self.server.shutdown);self.thread.join();self.context.__exit__(None,None,None)
        self.context=daemon_server(self.config);self.server=self.context.__enter__()
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.02},daemon=True);self.thread.start()
        self.assertEqual(self.store.instance(a['user_id'])['pid'],a['pid'])
        self.assertEqual((await self.client.get('/api/auth/me')).status_code,200)
        result=await asyncio.to_thread(self.app.state.supervisor.call,'status',instance_id=a['instance_id'])
        self.assertEqual(result['pid'],a['pid'])

    async def test_parallel_start_single_process_and_limit_is_daemon_owned(self):
        a=await self.register(enter=False)
        self.config.max_running_instances=1
        def start():return self.app.state.supervisor.start(a['user_id'])['pid']
        pids=await asyncio.gather(*(asyncio.to_thread(start) for _ in range(4)))
        self.assertEqual(len(set(pids)),1)
        b=await self.register('bob',enter=False)
        with self.assertRaises(BetaError):await asyncio.to_thread(self.app.state.supervisor.start,b['user_id'])
        self.assertNotEqual(a['assigned_port'],b['assigned_port'])
        self.assertEqual(len(self.server.supervisor.children),1)

    async def test_admin_disable_revokes_session_and_explicitly_stops_worker(self):
        a=await self.register()
        await asyncio.to_thread(self.app.state.supervisor.account_status,'alice',False)
        self.assertEqual(self.store.instance(a['user_id'])['status'],'stopped')
        self.assertEqual((await self.client.get('/api/auth/me')).status_code,401)
        self.assertTrue(Path(a['data_root']).is_dir())

    async def test_admin_stop_preserves_registry_port_and_data(self):
        a=await self.register()
        await asyncio.to_thread(self.app.state.supervisor.stop,a['user_id'])
        current=self.store.instance(a['user_id'])
        self.assertEqual(current['status'],'stopped');self.assertEqual(current['assigned_port'],a['assigned_port'])
        self.assertTrue(Path(a['data_root']).is_dir())

    async def test_idle_stop_is_opt_in_and_active_request_refreshes_timestamp(self):
        a=await self.register();self.config.idle_seconds=10
        with self.store.db() as db:db.execute('UPDATE instance_activity SET last_seen=0')
        self.assertEqual((await self.client.get('/api/canvases')).status_code,200)
        await asyncio.to_thread(self.server.supervisor.stop_idle)
        self.assertEqual(self.store.instance(a['user_id'])['pid'],a['pid'])
        # A short operator-selected idle timeout must shorten timestamp coalescing too.
        self.config.idle_seconds=3
        with self.store.db() as db:db.execute('UPDATE instance_activity SET last_seen=?',(time.time()-2,))
        began=time.time()
        self.assertEqual((await self.client.get('/api/canvases')).status_code,200)
        with self.store.db() as db:self.assertGreaterEqual(db.execute('SELECT last_seen FROM instance_activity').fetchone()[0],began)
        with self.store.db() as db:db.execute('UPDATE instance_activity SET last_seen=0')
        await asyncio.to_thread(self.server.supervisor.stop_idle)
        self.assertEqual(self.store.instance(a['user_id'])['status'],'stopped')

    async def test_gateway_does_not_expose_ipc_or_other_instance_control(self):
        a=await self.register()
        for path in ['/control','/api/beta/start','/api/beta/stop']:
            response=await self.client.post(path,json={'instance_id':a['instance_id'],'operation':'stop'})
            self.assertIn(response.status_code,{403,404,405})
        self.assertEqual(self.store.instance(a['user_id'])['pid'],a['pid'])
        await self.client.post('/api/auth/logout')
        self.assertEqual((await self.client.get('/api/canvases')).status_code,401)
        self.assertEqual((await self.client.get('/api/instance/providers')).status_code,401)

    async def test_ipc_rejects_arbitrary_command_environment_and_unknown_instance(self):
        a=await self.register()
        with httpx.Client(transport=httpx.HTTPTransport(uds=self.config.supervisor_socket)) as client:
            for payload in [{'operation':'exec','command':'ignored'},
                            {'operation':'start','instance_id':a['instance_id'],'env':{}},
                            {'operation':'start','instance_id':a['instance_id'],'argv':[]},
                            {'operation':'start','instance_id':'../../owner'},
                            {'operation':'stop','instance_id':'0'*32}]:
                response=await asyncio.to_thread(client.post,'http://supervisor/control',json=payload)
                self.assertIn(response.status_code,{400,404})
            response=await asyncio.to_thread(client.post,'http://supervisor/control',content=b'x'*5000,headers={'Content-Type':'application/json'})
            self.assertEqual(response.status_code,413)
        self.assertEqual(os.stat(self.config.supervisor_socket).st_mode&0o777,0o600)

    async def test_second_daemon_cannot_take_authority(self):
        with self.assertRaises(BlockingIOError):
            with daemon_server(self.config):pass
        self.assertEqual((await self.client.get('/healthz')).status_code,200)

    async def test_unauthorized_local_peer_is_rejected_before_control_dispatch(self):
        with patch('public_beta_daemon.peer_allowed',return_value=False),patch('public_beta_daemon.dispatch') as operation:
            with httpx.Client(transport=httpx.HTTPTransport(uds=self.config.supervisor_socket)) as client:
                response=await asyncio.to_thread(client.post,'http://supervisor/control',json={'operation':'list_running'})
                self.assertEqual(response.status_code,403)
                operation.assert_not_called()

    async def test_unknown_user_status_cannot_be_adopted_as_running(self):
        a=await self.register()
        try:
            with self.store.db() as db:db.execute("UPDATE users SET status='unexpected' WHERE id=?",(a['user_id'],))
            with self.assertRaises(BetaError):await asyncio.to_thread(self.server.supervisor.reconcile)
            self.assertEqual(self.store.instance(a['user_id'])['pid'],a['pid'])
        finally:
            with self.store.db() as db:db.execute("UPDATE users SET status='active' WHERE id=?",(a['user_id'],))

    async def test_missing_pid_reconciles_stopped_without_releasing_port(self):
        a=await self.register(enter=False)
        with self.store.db() as db:db.execute("UPDATE instances SET status='running',pid=99999999,process_started='missing'")
        await asyncio.to_thread(self.server.supervisor.reconcile)
        current=self.store.instance(a['user_id'])
        self.assertEqual(current['status'],'stopped');self.assertEqual(current['assigned_port'],a['assigned_port'])

    async def test_mismatched_process_record_fails_closed_without_signalling(self):
        a=await self.register();path=Path(a['data_root'])/'.runtime/process.json';saved=path.read_text()
        try:
            path.write_text(json.dumps({'instance_id':'0'*32,'pid':a['pid'],'host':'127.0.0.1','port':a['assigned_port']}))
            with self.assertRaises(BetaError):await asyncio.to_thread(self.server.supervisor.reconcile)
            self.assertEqual((await self.client.get('/api/auth/me')).status_code,200)
        finally:path.write_text(saved)

    async def test_unknown_live_port_cannot_be_adopted(self):
        a=await self.register(enter=False)
        with socket.socket() as occupied:
            occupied.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            occupied.bind(('127.0.0.1',a['assigned_port']))
            occupied.listen()
            with self.assertRaises(BetaError):await asyncio.to_thread(self.server.supervisor.reconcile)
            with self.assertRaises(BetaError):await asyncio.to_thread(self.app.state.supervisor.start,a['user_id'])

    async def test_gateway_ipc_failure_never_falls_back_to_spawn(self):
        await asyncio.to_thread(self.server.shutdown)
        Path(self.config.supervisor_socket).unlink()
        with patch('public_beta_supervisor.subprocess.Popen') as spawn:
            with self.assertRaises(BetaError):await asyncio.to_thread(self.app.state.supervisor.call,'list_running')
            spawn.assert_not_called()

    async def test_real_gateway_sigterm_crash_and_websocket_reconnect_keep_worker_session(self):
        from websockets.asyncio.client import connect
        a=await self.register()
        env={'PATH':os.defpath,'PYTHONDONTWRITEBYTECODE':'1','PUBLIC_BETA_ROOT':str(self.config.root),
             'PUBLIC_BETA_INSTANCES_ROOT':str(self.config.instances_root),'PUBLIC_BETA_BACKUP_ROOT':str(self.config.backup_root),
             'GATEWAY_PORT':str(self.config.port),'PUBLIC_BETA_SUPERVISOR_SOCKET':self.config.supervisor_socket,
             'MIO_MAINTENANCE_ROOT':str(self.config.maintenance_root),
             'MIN_FREE_DISK_BYTES':'0'}
        program=Path(__file__).resolve().parents[1]
        def spawn():return subprocess.Popen([sys.executable,str(program/'public_beta.py')],cwd=program,env=env,
                                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        process=spawn()
        async with httpx.AsyncClient(base_url=self.config.origin,trust_env=False,cookies=self.client.cookies,timeout=3) as client:
            async def ready():
                for _ in range(100):
                    try:
                        if (await client.get('/api/auth/me')).status_code==200:return
                    except httpx.HTTPError:pass
                    await asyncio.sleep(.05)
                self.fail('Gateway did not recover authenticated routing')
            try:
                await ready()
                headers={'Cookie':'; '.join(k+'='+v for k,v in self.client.cookies.items())}
                uri=self.config.origin.replace('http:','ws:')+'/ws/stats'
                async with connect(uri,origin=self.config.origin,additional_headers=headers,proxy=None) as ws:
                    self.assertTrue(await asyncio.wait_for(ws.recv(),3))
                    process.terminate();await asyncio.to_thread(process.wait,10)
                self.assertEqual(self.store.instance(a['user_id'])['pid'],a['pid'])
                process=spawn();await ready()
                async with connect(uri,origin=self.config.origin,additional_headers=headers,proxy=None) as ws:
                    self.assertTrue(await asyncio.wait_for(ws.recv(),3))
                process.kill();await asyncio.to_thread(process.wait,10)
                self.assertEqual(self.store.instance(a['user_id'])['pid'],a['pid'])
                process=spawn();await ready()
                self.assertEqual((await client.get('/api/canvases')).status_code,200)
                self.assertEqual((await client.get('/api/instance/providers')).status_code,200)
            finally:
                if process.poll() is None:process.terminate()
                await asyncio.to_thread(process.wait,10)


class WorkspaceReconnectTests(unittest.TestCase):
    def test_reconnect_js(self):
        root=Path(__file__).resolve().parents[1]
        subprocess.run(['node',str(root/'tests/test_workspace_socket.js')],cwd=root,check=True,capture_output=True)

    def test_production_entrypoint_cannot_disable_ipc_with_empty_configuration(self):
        with patch.dict(os.environ,{'PUBLIC_BETA_SUPERVISOR_SOCKET':''}):
            with self.assertRaises(ValueError):BetaConfig.from_env()

    def test_systemd_units_preserve_workers_and_bound_combined_resources(self):
        root=Path(__file__).resolve().parents[1]/'deploy/public-beta'
        gateway=(root/'mio-canvas-gateway.service').read_text()
        daemon=(root/'mio-canvas-instance-supervisor.service').read_text()
        self.assertIn('KillMode=process',daemon)
        self.assertNotIn('KillMode=process',gateway)
        self.assertIn('public_beta_daemon.py ready',daemon)
        self.assertIn('Sockets=mio-canvas-gateway.socket',gateway)
        self.assertTrue(all('Slice=mio-canvas.slice' in value for value in [gateway,daemon]))
        self.assertIn('MemoryMax=1200M',(root/'mio-canvas.slice').read_text())
        self.assertNotIn('PartOf=',daemon)
