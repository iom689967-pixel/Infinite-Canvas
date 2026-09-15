"""Real isolated workers plus the shipped browser coordinator; no production calls."""
import asyncio
import json
import re
import subprocess
import socket
import unittest
from unittest.mock import patch

import test_public_beta as fixtures
from public_beta_store import BetaError


class WorkspaceEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f=fixtures.PublicBetaTests()
        await self.f.asyncSetUp()
        self.f.config.port_start=46000;self.f.config.port_end=46031

    async def asyncTearDown(self):
        await self.f.asyncTearDown()

    async def test_cold_login_serves_full_shell_without_starting_or_private_data(self):
        f=self.f;principal=await f.register()
        self.assertEqual(f.store.instance(principal['id'])['status'],'stopped')
        with patch.object(f.supervisor,'start',side_effect=AssertionError('HTML must not wait for start')):
            for path in ('/','/workspace','/static/index.html'):
                page=await f.client.get(path)
                self.assertEqual(page.status_code,200)
                self.assertIn('id="studioSidebar"',page.text)
                self.assertIn('id="frame-canvas"',page.text)
                self.assertIn('/static/js/workspace-startup.js?v=',page.text)
                self.assertNotIn('正在启动你的工作区',page.text)
                self.assertEqual(page.headers['cache-control'],'no-store, private')
        for form in ('login','register'):
            page=(await f.client.get('/'+form)).text
            if form=='login': self.assertIn("location.replace('/')",page)
            else:
                # The code form enters / only after verification; registration
                # itself stays on this page and never navigates to a startup shell.
                self.assertIn("location.replace('/')",page)
                self.assertIn("/api/beta/verify-email-code",page)
                self.assertIn('showPending(state)',page)
                self.assertIn('masked_email',page)
            self.assertNotIn("location.replace('/workspace')",page)
        private=await f.client.get('/api/canvases')
        self.assertEqual(private.status_code,503)
        self.assertNotIn('canvases',private.json())

    async def test_cold_shell_resources_load_without_worker_auth_or_user_files(self):
        f=self.f;await f.register()
        html=(await f.client.get('/')).text
        urls=re.findall(r'(?:src|href)="(/static/[^"?]+(?:\?[^\"]+)?)"',html)
        for url in urls:
            if not url.split('?')[0].endswith(('.js','.css','.png')):continue
            response=await f.client.get(url)
            self.assertEqual(response.status_code,200,url)
            self.assertNotIn('set-cookie',response.headers)
        for path in ('/static/runninghub/api_providers.json','/assets/unknown.png','/api/history'):
            self.assertNotEqual((await f.client.get(path)).status_code,200)

    async def test_cold_canvas_frame_reuses_framework_but_no_private_data(self):
        f=self.f;await f.register()
        page=await f.client.get('/static/canvas-list.html',headers={'Sec-Fetch-Dest':'iframe'})
        self.assertEqual(page.status_code,200)
        self.assertIn('id="boardProjectName"',page.text)
        self.assertIn('instance-session.js',page.text)
        self.assertNotIn('workspace-startup.js',page.text)
        self.assertEqual(page.headers['cache-control'],'no-store, private')
        self.assertEqual((await f.client.get('/api/canvases')).status_code,503)

    async def test_enter_returns_verified_session_and_keeps_warm_session(self):
        f=self.f;principal=await f.register()
        csrf=(await f.client.get('/api/beta/me')).json()['csrf']
        async def enter():return await f.client.post('/api/beta/enter',headers={'X-CSRF-Token':csrf})
        response=await enter();self.assertEqual(response.status_code,200,response.text)
        me=response.json()['session'];self.assertEqual(me['username'],'alice')
        self.assertEqual(me,(await f.client.get('/api/auth/me')).json())
        pid=f.store.instance(principal['id'])['pid']
        second=await enter();self.assertEqual(second.status_code,200)
        self.assertNotIn('set-cookie',second.headers)
        self.assertEqual(second.json()['session']['csrf'],me['csrf'])
        self.assertEqual(f.store.instance(principal['id'])['pid'],pid)
        self.assertIn('studioSidebar',(await f.client.get('/')).text)

    async def test_multitab_concurrent_enter_starts_only_one_process(self):
        f=self.f;principal=await f.register()
        csrf=(await f.client.get('/api/beta/me')).json()['csrf']
        replies=await asyncio.gather(*(f.client.post('/api/beta/enter',headers={'X-CSRF-Token':csrf}) for _ in range(3)))
        self.assertEqual([r.status_code for r in replies],[200]*3)
        self.assertEqual(len({r.json()['session']['instance_id'] for r in replies}),1)
        with f.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM security_events WHERE event='started' AND user_id=?",(principal['id'],)).fetchone()[0],1)
        self.assertEqual(len(f.supervisor.list_running()),1)

    async def test_stopped_worker_handoff_replaces_stale_instance_cookie(self):
        f=self.f;principal=await f.register();await f.enter()
        await asyncio.to_thread(f.supervisor.stop,principal['id'])
        csrf=(await f.client.get('/api/beta/me')).json()['csrf']
        response=await f.client.post('/api/beta/enter',headers={'X-CSRF-Token':csrf})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['session'],(await f.client.get('/api/auth/me')).json())

    async def test_start_failure_keeps_gateway_session_and_shell_for_retry(self):
        f=self.f;await f.register();me=(await f.client.get('/api/beta/me')).json()
        with patch.object(f.supervisor,'start',side_effect=BetaError('unavailable',503)):
            response=await f.client.post('/api/beta/enter',headers={'X-CSRF-Token':me['csrf']})
        self.assertEqual(response.status_code,503)
        self.assertEqual((await f.client.get('/api/beta/me')).json(),me)
        self.assertIn('studioSidebar',(await f.client.get('/')).text)
        response=await f.client.post('/api/beta/enter',headers={'X-CSRF-Token':me['csrf']})
        self.assertEqual(response.status_code,200,response.text)

    async def test_cold_logout_csrf_origin_and_private_401(self):
        f=self.f;await f.register();csrf=(await f.client.get('/api/beta/me')).json()['csrf']
        self.assertEqual((await f.client.post('/api/beta/logout')).status_code,403)
        self.assertEqual((await f.client.post('/api/beta/logout',headers={'X-CSRF-Token':csrf,'Origin':'https://evil.invalid'})).status_code,403)
        self.assertEqual((await f.client.post('/api/beta/logout',headers={'X-CSRF-Token':csrf})).status_code,200)
        for url in ('/workspace','/api/beta/me','/api/auth/me','/api/canvases','/api/instance/providers','/assets/private.png','/static/js/workspace-startup.js'):
            self.assertEqual((await f.client.get(url)).status_code,401,url)

    async def test_shell_namespaces_and_handoff_are_account_scoped(self):
        f=self.f;a=await f.register();await f.enter()
        response=await f.client.post('/api/canvases',json={'title':'Alice private','kind':'smart'})
        self.assertEqual(response.status_code,200,response.text)
        canvas=response.json()['canvas']
        html_a=(await f.client.get('/')).text
        await f.register('bob');html_b=(await f.client.get('/')).text
        context=lambda html:json.loads(re.search(r'id="instance-context">(.*?)</script>',html)[1])
        self.assertNotEqual(context(html_a)['storage_namespace'],context(html_b)['storage_namespace'])
        self.assertNotIn(context(html_a)['storage_namespace'],html_b)
        await f.enter()
        self.assertEqual((await f.client.get('/api/canvases')).json()['canvases'],[])
        self.assertEqual((await f.client.get('/api/canvases/'+canvas['id'])).status_code,404)
        self.assertEqual(f.store.instance(a['id'])['status'],'running')

    async def test_revoked_login_during_start_never_receives_handoff(self):
        f=self.f;await f.register();csrf=(await f.client.get('/api/beta/me')).json()['csrf']
        original=f.supervisor.start
        def revoke(uid):
            instance=original(uid);f.store.revoke(f.client.cookies.get('mio_beta_session'));return instance
        with patch.object(f.supervisor,'start',side_effect=revoke):
            response=await f.client.post('/api/beta/enter',headers={'X-CSRF-Token':csrf})
        self.assertEqual(response.status_code,401)
        self.assertNotIn('set-cookie',response.headers)


class WorkspaceStartupJavascriptTests(unittest.TestCase):
    def test_shipped_startup_failure_retry_timeout_and_ready_gates(self):
        subprocess.run(['node','tests/test_workspace_startup.js'],check=True,capture_output=True)

    def test_live_listener_remains_unavailable_even_with_reuseaddr(self):
        from public_beta_supervisor import Supervisor
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            server.bind(('127.0.0.1',0));server.listen()
            self.assertFalse(Supervisor.available(server.getsockname()[1]))

    def test_stopped_listener_with_time_wait_can_start_again(self):
        from public_beta_supervisor import Supervisor
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            server.bind(('127.0.0.1',0));server.listen();port=server.getsockname()[1]
            with socket.create_connection(('127.0.0.1',port),timeout=2) as client:
                accepted,_=server.accept()
                accepted.shutdown(socket.SHUT_WR);accepted.close()
                self.assertEqual(client.recv(1),b'')
        self.assertTrue(Supervisor.available(port))
