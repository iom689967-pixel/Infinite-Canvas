"""Only loopback HTTP, real isolated workers and synthetic credentials/media."""
import asyncio
import base64
from io import BytesIO
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import hashlib
import socket
from http.server import ThreadingHTTPServer
from test_instance_models import ModelMock
import unittest
from unittest.mock import patch
import zipfile

import httpx
from PIL import Image
from public_beta import create_app
from public_beta_store import BetaConfig, BetaError
from public_beta_handoff import sign_ticket, consume_ticket, instance_key, verify_ticket
from instance_auth import AuthStore
from instance_storage_quota import StorageQuota
from test_instance_isolation import free_port

PASSWORD='Public-beta-test-password-42'


class PublicBetaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mio-beta-test-');self.root=Path(self.temp.name).resolve()
        self.config=BetaConfig(self.root/'gateway',self.root/'instances',port=free_port(),max_users=3,
                              register_limit=100,login_limit=100,max_upload=4096,storage_quota=1024**2)
        self.app=create_app(self.config);self.store=self.app.state.store;self.supervisor=self.app.state.supervisor
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url=self.config.origin,headers={'Origin':self.config.origin})
        image=BytesIO();Image.new('RGB',(16,16),'blue').save(image,'PNG');self.image=image.getvalue()

    async def asyncTearDown(self):
        await self.client.aclose();await asyncio.to_thread(self.supervisor.close);self.temp.cleanup()

    async def register(self,name='alice'):
        r=await self.client.post('/api/beta/register',json={'username':name,'password':PASSWORD,'confirmation':PASSWORD})
        self.assertEqual(r.status_code,200,r.text)
        return self.store.principal(self.client.cookies.get('mio_beta_session'))

    async def enter(self):
        csrf=(await self.client.get('/api/beta/me')).json()['csrf']
        r=await self.client.post('/api/beta/enter',headers={'X-CSRF-Token':csrf})
        self.assertEqual(r.status_code,200,r.text)
        me=(await self.client.get('/api/auth/me')).json()
        self.client.headers['X-CSRF-Token']=me['csrf'];return me

    async def test_register_sso_full_ui_and_empty_private_instance(self):
        principal=await self.register('Alice');me=await self.enter()
        self.assertEqual(me['username'],'alice');self.assertTrue(me['public_beta'])
        instance=self.store.instance(principal['id']);root=Path(instance['data_root'])
        self.assertNotIn('alice',str(root));self.assertEqual(root.parent,self.config.instances_root)
        for name in ('data','assets','output','workflows/custom','runtime','.auth'): self.assertTrue((root/name).is_dir())
        html=(await self.client.get('/')).text
        self.assertIn('studioSidebar',html);self.assertNotIn('instance-workspace.html',html)
        for secret in (str(root),str(instance['assigned_port']),PASSWORD): self.assertNotIn(secret,html)
        self.assertEqual((await self.client.get('/api/canvases')).json()['canvases'],[])
        self.assertEqual((await self.client.get('/api/history')).json(),[])
        self.assertEqual((await self.client.get('/api/instance/providers')).json()['providers'],[])
        self.assertEqual((await self.client.get('/api/auth/login')).status_code,403)

    async def test_duplicate_case_and_minimum_password(self):
        await self.register()
        r=await self.client.post('/api/beta/register',json={'username':'ALICE','password':PASSWORD,'confirmation':PASSWORD})
        self.assertEqual(r.status_code,409)
        for name,pw in [('bad/name',PASSWORD),('a',PASSWORD),('bob','12345')]:
            r=await self.client.post('/api/beta/register',json={'username':name,'password':pw,'confirmation':pw})
            self.assertEqual(r.status_code,400)
        page=(await self.client.get('/register')).text
        self.assertIn('minlength="6"',page);self.assertNotIn('minlength="12"',page)
        r=await self.client.post('/api/beta/register',json={'username':'six','password':'123456','confirmation':'123456'})
        self.assertEqual(r.status_code,200)

    async def test_program_cache_headers_do_not_cache_private_html_api_or_media(self):
        import re
        await self.register();await self.enter()
        html=(await self.client.get('/')).text
        url=re.search(r'src="(/static/js/instance-session.js\?v=[^"]+)"',html)[1]
        script=await self.client.get(url)
        self.assertEqual(script.status_code,200)
        self.assertEqual(script.headers['cache-control'],'public, max-age=31536000, immutable')
        self.assertNotIn('x-instance-namespace',script.headers)
        self.assertNotIn('set-cookie',script.headers)
        cached=await self.client.get(url,headers={'If-None-Match':script.headers['etag']})
        self.assertEqual(cached.status_code,304);self.assertEqual(cached.content,b'')
        weak=await self.client.get(url,headers={'If-None-Match':'W/'+script.headers['etag']})
        self.assertEqual(weak.status_code,304)
        head=await self.client.head(url)
        self.assertEqual(head.status_code,200);self.assertEqual(head.content,b'')
        self.assertEqual(head.headers['content-length'],str(len(script.content)))
        unversioned=await self.client.get('/static/js/instance-session.js')
        self.assertEqual(unversioned.headers['cache-control'],'public, max-age=300, must-revalidate')
        for path in ('/','/static/canvas.html','/static/smart-canvas.html','/static/api-settings.html',
                     '/api/auth/me','/api/beta/me','/api/canvases','/api/instance/provider-settings',
                     '/api/history','/api/projects','/api/conversations','/assets/unknown.png'):
            response=await self.client.get(path)
            self.assertEqual(response.headers['cache-control'],'no-store, private',path)
        await self.client.post('/api/auth/logout')
        for path in (url,'/api/canvases','/api/instance/provider-settings','/assets/unknown.png'):
            response=await self.client.get(path)
            self.assertEqual(response.status_code,401)
            self.assertEqual(response.headers['cache-control'],'no-store, private')

    async def test_public_code_reused_across_users_but_html_namespaces_are_distinct(self):
        await self.register();a=await self.enter()
        html_a=await self.client.get('/')
        script_a=await self.client.get('/static/js/instance-session.js')
        await self.register('bob');b=await self.enter()
        html_b=await self.client.get('/')
        script_b=await self.client.get('/static/js/instance-session.js')
        self.assertEqual(script_a.content,script_b.content)
        self.assertEqual(script_a.headers['etag'],script_b.headers['etag'])
        self.assertNotEqual(a['storage_namespace'],b['storage_namespace'])
        self.assertIn(a['storage_namespace'],html_a.text)
        self.assertNotIn(a['storage_namespace'],html_b.text)
        self.assertEqual(html_b.headers['cache-control'],'no-store, private')

    async def test_capacity_existing_login_still_works(self):
        self.config.max_users=1;await self.register()
        r=await self.client.post('/api/beta/register',json={'username':'bob','password':PASSWORD,'confirmation':PASSWORD})
        self.assertEqual(r.status_code,409);self.assertIn('名额已满',r.text)
        r=await self.client.post('/api/beta/login',json={'username':'alice','password':PASSWORD})
        self.assertEqual(r.status_code,200)

    async def test_ip_limit_ignores_forwarded_headers(self):
        self.config.register_limit=1;await self.register()
        r=await self.client.post('/api/beta/register',headers={'X-Forwarded-For':'203.0.113.9'},json={'username':'bob','password':PASSWORD,'confirmation':PASSWORD})
        self.assertEqual(r.status_code,429)

    async def test_username_conflict_limit(self):
        await self.register()
        for _ in range(2):
            self.assertEqual((await self.client.post('/api/beta/register',json={'username':'alice','password':PASSWORD,'confirmation':PASSWORD})).status_code,409)
        self.assertEqual((await self.client.post('/api/beta/register',json={'username':'alice','password':PASSWORD,'confirmation':PASSWORD})).status_code,429)

    async def test_login_failure_limit_and_disable(self):
        principal=await self.register();await self.enter()
        await asyncio.to_thread(self.supervisor.account_status,'alice',False)
        self.assertEqual((await self.client.get('/api/auth/me')).status_code,401)
        self.assertTrue(Path(self.store.instance(principal['id'])['data_root']).exists())
        self.config.login_limit=2
        for _ in range(2): self.assertEqual((await self.client.post('/api/beta/login',json={'username':'alice','password':PASSWORD})).status_code,401)
        self.assertEqual((await self.client.post('/api/beta/login',json={'username':'alice','password':PASSWORD})).status_code,429)

    async def test_init_failure_rolls_back_only_new_root(self):
        untouched=self.config.instances_root/'existing';untouched.mkdir();(untouched/'keep').write_text('keep')
        with patch('public_beta_supervisor.subprocess.run',side_effect=OSError()):
            r=await self.client.post('/api/beta/register',json={'username':'alice','password':PASSWORD,'confirmation':PASSWORD})
        self.assertEqual(r.status_code,503)
        with self.store.db() as db: self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0],0)
        self.assertEqual(list(self.config.instances_root.iterdir()),[untouched])

    async def test_two_workers_namespace_canvas_media_provider_isolation(self):
        a=await self.register();ame=await self.enter();acookies=dict(self.client.cookies)
        canvas=(await self.client.post('/api/canvases',json={'title':'Alice private','kind':'smart'})).json()['canvas']
        ref=(await self.client.post('/api/ai/upload',files={'files':('tiny.png',self.image,'image/png')})).json()['files'][0]['url']
        b=await self.register('bob');bme=await self.enter()
        ia,ib=self.store.instance(a['id']),self.store.instance(b['id'])
        self.assertNotEqual(ia['pid'],ib['pid']);self.assertNotEqual(ia['assigned_port'],ib['assigned_port'])
        self.assertNotEqual(ame['storage_namespace'],bme['storage_namespace'])
        self.assertEqual((await self.client.get('/api/canvases/'+canvas['id'])).status_code,404)
        self.assertEqual((await self.client.get(ref)).status_code,404)
        self.assertEqual((await self.client.get('/api/history')).json(),[])
        self.assertEqual((await self.client.get('/api/instance/providers')).json()['providers'],[])
        for path in ('/api/update-from-github','/api/update-rollback','/__mio/handoff'):
            self.assertIn((await self.client.post(path,json={})).status_code,(403,404))

    async def test_handoff_one_use_wrong_instance_and_expiration(self):
        a=await self.register();await self.enter();ia=self.store.instance(a['id'])
        token=self.supervisor.ticket(ia)
        auth=AuthStore(ia['data_root'],ia['instance_id']);auth.boot='unit-ticket-boot'
        self.assertIsNotNone(consume_ticket(auth,token));self.assertIsNone(consume_ticket(auth,token))
        key=instance_key(self.store.key,ia['instance_id'])
        self.assertIsNone(verify_ticket(key,token,a['id'],'wrong'))
        expired=sign_ticket(key,a['id'],ia['instance_id'],ttl=1)
        with patch('public_beta_handoff.time.time',return_value=10**12): self.assertIsNone(consume_ticket(auth,expired))

    async def test_port_start_idempotency_and_origin_csrf(self):
        p=await self.register();await self.enter();pid=self.store.instance(p['id'])['pid']
        await self.enter();self.assertEqual(self.store.instance(p['id'])['pid'],pid)
        self.assertEqual((await self.client.post('/api/beta/enter',headers={'X-CSRF-Token':'forged'})).status_code,403)
        self.assertEqual((await self.client.post('/api/canvases',headers={'Origin':'http://evil.invalid'},json={})).status_code,403)
        self.assertEqual((await self.client.get('/',headers={'Host':'evil.invalid'})).status_code,403)

    async def test_upload_limit_quota_delete_and_logout(self):
        p=await self.register();await self.enter()
        r=await self.client.post('/api/ai/upload',files={'files':('too.png',b'x'*4097,'image/png')})
        self.assertEqual(r.status_code,413)
        usage=(await self.client.get('/api/instance/storage')).json();self.assertGreater(usage['used_bytes'],0)
        ref=(await self.client.post('/api/ai/upload',files={'files':('tiny.png',self.image,'image/png')})).json()['files'][0]['url']
        newer=(await self.client.get('/api/instance/storage')).json();self.assertGreater(newer['used_bytes'],usage['used_bytes'])
        r=await self.client.post('/api/auth/logout');self.assertEqual(r.status_code,200)
        for path in ('/api/canvases','/api/instance/providers',ref): self.assertEqual((await self.client.get(path)).status_code,401)

    async def test_no_secrets_in_gateway_storage_or_events(self):
        await self.register();await self.enter()
        raw=self.store.path.read_bytes();self.assertNotIn(PASSWORD.encode(),raw)
        with self.store.db() as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertFalse(tables & {'providers','canvases','history'})
            fields={r[1] for r in db.execute('PRAGMA table_info(security_events)')}
            self.assertEqual(fields,{'id','event','user_id','created_at'})

    async def enable_mock(self):
        mock=ThreadingHTTPServer(('127.0.0.1',0),ModelMock)
        self.mock_key=secrets.token_urlsafe(32)
        mock.calls,mock.jobs,mock.media=[],{},{}
        mock.credentials={hashlib.sha256(self.mock_key.encode()).hexdigest():'A'}
        mock.image=self.image;mock.origin=f'http://127.0.0.1:{mock.server_port}';mock.finish_waiting=False
        thread=threading.Thread(target=mock.serve_forever,daemon=True);thread.start()
        self.addCleanup(mock.server_close);self.addCleanup(mock.shutdown)
        self.config.mock_upstreams=f'127.0.0.1:{mock.server_port}';self.mock=mock

    async def personal_provider(self):
        item={'id':'personal','name':'My mock','protocol':'kie','base_url':self.mock.origin,'enabled':True,
              'models':[{'id':'gpt-image-2','purpose':'image'}]}
        r=await self.client.put('/api/instance/providers',json=item | {'api_key':self.mock_key})
        self.assertEqual(r.status_code,200,r.text)
        return item

    async def test_personal_key_and_full_settings_stay_per_user(self):
        await self.enable_mock();await self.register();await self.enter();await self.personal_provider()
        public=await self.client.get('/api/instance/provider-settings')
        self.assertEqual(public.status_code,200);self.assertNotIn(self.mock_key,public.text)
        self.assertIn('api-settings.js',(await self.client.get('/static/api-settings.html')).text)
        self.assertTrue((await self.client.get('/api/instance/providers')).json()['providers'][0]['has_key'])
        await self.register('bob');await self.enter()
        self.assertEqual((await self.client.get('/api/instance/providers')).json()['providers'],[])
        self.assertNotIn(self.mock_key,self.store.path.read_bytes().decode('latin1'))
        self.assertEqual(self.mock.calls,[])

    async def test_generation_concurrency_is_server_owned(self):
        await self.enable_mock();self.config.concurrency=1
        await self.register();await self.enter();await self.personal_provider()
        payload={'provider_id':'personal','model':'gpt-image-2','prompt':'WAIT_IMAGE','size':'1024x1024',
                 'aspect_ratio':'1:1','resolution':'1K','n':1,'request_id':'one'}
        first=await self.client.post('/api/canvas-image-tasks',json=payload);self.assertEqual(first.status_code,200,first.text)
        second=await self.client.post('/api/canvas-image-tasks',json=payload | {'request_id':'two','prompt':'different','max_concurrent':100})
        self.assertEqual(second.status_code,429,second.text)
        self.assertLessEqual(sum(kind=='create' for kind,_,_ in self.mock.calls),1)

    async def test_generated_result_storage_quota_keeps_original_task_recoverable(self):
        await self.enable_mock();p=await self.register();await self.enter();await self.personal_provider()
        self.mock.image=Image.effect_noise((128,128),100).convert('RGB')
        buffer=BytesIO();self.mock.image.save(buffer,'PNG');self.mock.image=buffer.getvalue()
        instance=self.store.instance(p['id']);root=Path(instance['data_root'])
        await asyncio.to_thread(self.supervisor.stop,p['id'])
        cfgpath=root/'.auth/public-beta.json';cfg=json.loads(cfgpath.read_text())
        used=StorageQuota(root,cfg['storage_quota'],cfg['max_upload']).recount()['used_bytes']
        cfg.update(storage_quota=used+2000,max_upload=1024**2);cfgpath.write_text(json.dumps(cfg))
        await self.enter()
        payload={'provider_id':'personal','model':'gpt-image-2','prompt':'mock garment','size':'1024x1024',
                 'aspect_ratio':'1:1','resolution':'1K','n':1,'request_id':'quota-once'}
        r=await self.client.post('/api/canvas-image-tasks',json=payload);self.assertEqual(r.status_code,200,r.text)
        task=r.json()['task_id']
        for _ in range(100):
            job=(await self.client.get('/api/canvas-image-tasks/'+task)).json()
            if job['status']=='result_recovery_required':break
            await asyncio.sleep(.05)
        self.assertEqual(job['status'],'result_recovery_required');self.assertEqual(job['error_code'],'storage_full')
        self.assertEqual(sum(k=='create' for k,_,_ in self.mock.calls),1)
        await asyncio.to_thread(self.supervisor.stop,p['id'])
        cfg['storage_quota']=1024**2;cfgpath.write_text(json.dumps(cfg));await self.enter()
        self.assertEqual((await self.client.post('/api/canvas-image-tasks/'+task+'/refresh')).status_code,200)
        for _ in range(100):
            job=(await self.client.get('/api/canvas-image-tasks/'+task)).json()
            if job['status']=='succeeded':break
            await asyncio.sleep(.05)
        self.assertEqual(job['status'],'succeeded');self.assertEqual(sum(k=='create' for k,_,_ in self.mock.calls),1)
        self.assertEqual(len((await self.client.get('/api/history')).json()),1)

    async def test_upload_stream_without_content_length_is_bounded(self):
        await self.register();await self.enter()
        boundary='beta-fixture-boundary'
        data=('--'+boundary+'\r\nContent-Disposition: form-data; name="files"; filename="large.png"\r\nContent-Type: image/png\r\n\r\n').encode()+b'x'*5000+('\r\n--'+boundary+'--\r\n').encode()
        async def chunks():
            for i in range(0,len(data),127):yield data[i:i+127]
        r=await self.client.post('/api/ai/upload',content=chunks(),headers={'Content-Type':'multipart/form-data; boundary='+boundary})
        self.assertEqual(r.status_code,413)

    async def test_zip_import_cannot_expand_past_limit(self):
        await self.register();await self.enter()
        buf=BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('workflow.json',json.dumps({'nodes':[],'connections':[],'resources':[{'archive':'big.bin'}]}))
            z.writestr('big.bin',b'x'*6000)
        r=await self.client.post('/api/canvas-workflows/import',files={'file':('workflow.zip',buf.getvalue(),'application/zip')})
        self.assertEqual(r.status_code,413)

    async def test_full_quota_allows_read_delete_and_recount(self):
        p=await self.register();await self.enter()
        ref=(await self.client.post('/api/ai/upload',files={'files':('tiny.png',self.image,'image/png')})).json()['files'][0]['url']
        instance=self.store.instance(p['id']);root=Path(instance['data_root'])
        await asyncio.to_thread(self.supervisor.stop,p['id'])
        path=root/'.auth/public-beta.json';cfg=json.loads(path.read_text());cfg['storage_quota']=1;path.write_text(json.dumps(cfg))
        await self.enter()
        r=await self.client.post('/api/ai/upload',files={'files':('new.png',self.image,'image/png')})
        self.assertEqual(r.status_code,413);self.assertIn('空间已满',r.text)
        self.assertEqual((await self.client.get(ref)).status_code,200)
        exported=await self.client.post('/api/canvas-workflows/export',json={'nodes':[{'id':'tiny','type':'text','text':'kept'}],'include_resources':False})
        self.assertEqual(exported.status_code,200,exported.text[:50] if exported.status_code!=200 else '')
        self.assertTrue(zipfile.is_zipfile(BytesIO(exported.content)))
        r=await self.client.post('/api/storage-files/delete',json={'kind':'upload','items':[ref.rsplit('/',1)[1]]})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['removed'],1)
        self.assertEqual((await self.client.get(ref)).status_code,404)

    async def test_group_export_cannot_duplicate_media_past_quota(self):
        p=await self.register();await self.enter()
        ref=(await self.client.post('/api/ai/upload',files={'files':('tiny.png',self.image,'image/png')})).json()['files'][0]['url']
        root=Path(self.store.instance(p['id'])['data_root'])
        await asyncio.to_thread(self.supervisor.stop,p['id'])
        cfgpath=root/'.auth/public-beta.json';cfg=json.loads(cfgpath.read_text())
        used=StorageQuota(root,cfg['storage_quota'],cfg['max_upload']).recount()['used_bytes']
        cfg['storage_quota']=used+16;cfgpath.write_text(json.dumps(cfg))
        await self.enter()
        reply=await self.client.post('/api/smart-canvas/group-export',json={'group_name':'bounded','items':[{'kind':'image','url':ref}]})
        self.assertEqual(reply.status_code,413,reply.text)
        self.assertIn('空间已满',reply.text)
        self.assertEqual((await self.client.get(ref)).status_code,200)

    async def test_enable_retains_data_and_requires_new_session(self):
        p=await self.register();await self.enter()
        canvas=(await self.client.post('/api/canvases',json={'title':'Retained'})).json()['canvas']
        await asyncio.to_thread(self.supervisor.account_status,'alice',False)
        await asyncio.to_thread(self.supervisor.account_status,'alice',True)
        self.assertEqual((await self.client.get('/api/auth/me')).status_code,401)
        self.assertEqual((await self.client.post('/api/beta/login',json={'username':'alice','password':PASSWORD})).status_code,200)
        await self.enter();self.assertEqual((await self.client.get('/api/canvases/'+canvas['id'])).status_code,200)

    async def test_registration_capacity_is_transactional(self):
        self.config.max_users=1
        def run(name):
            try:self.supervisor.register(name,PASSWORD,PASSWORD,'separate-'+name);return 200
            except BetaError as exc:return exc.status
        statuses=await asyncio.gather(asyncio.to_thread(run,'alice'),asyncio.to_thread(run,'bob'))
        self.assertEqual(sorted(statuses),[200,409])
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM instances').fetchone()[0],1)

    async def test_port_occupied_is_not_adopted(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0));port=occupied.getsockname()[1]
            self.config.port_start=port;self.config.port_end=port
            r=await self.client.post('/api/beta/register',json={'username':'alice','password':PASSWORD,'confirmation':PASSWORD})
            self.assertEqual(r.status_code,503)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0],0)

    async def test_gateway_restart_cleans_incomplete_registration(self):
        from instance_auth import password_hash
        with self.supervisor.locked():
            instance=self.store.reserve('alice',password_hash(PASSWORD),self.supervisor.allocate())
            root=Path(instance['data_root']);root.mkdir()
        await asyncio.to_thread(self.supervisor.recover_incomplete)
        self.assertFalse(root.exists())
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0],0)

    async def test_paths_ports_and_owner_options_are_not_browser_inputs(self):
        r=await self.client.post('/api/beta/register',json={'username':'alice','password':PASSWORD,'confirmation':PASSWORD,'port':3000})
        self.assertEqual(r.status_code,400)
        await self.register();await self.enter()
        self.assertEqual((await self.client.post('/__mio/handoff',json={'ticket':'fake'})).status_code,404)
        self.assertEqual((await self.client.get('/api/beta/instance?port=3000')).status_code,404)


class StorageQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'.auth').mkdir();(self.root/'.runtime/tmp').mkdir(parents=True)
        self.q=StorageQuota(self.root,100,80)

    def test_incremental_quota_and_deletion(self):
        self.q.write(self.root/'a',b'a'*60)
        with self.assertRaises(Exception): self.q.write(self.root/'b',b'b'*50)
        (self.root/'a').unlink();self.q.changed(self.root/'a');self.assertEqual(self.q.usage()['used_bytes'],0)
        self.q.write(self.root/'b',b'b'*50)
        self.assertEqual(self.q.recount()['used_bytes'],50)

    def test_reservations_prevent_concurrent_overcommit(self):
        with self.q.reserve(60):
            with self.assertRaises(Exception):
                with self.q.reserve(50): pass
        self.assertEqual(self.q.reserved,0)

    def test_existing_file_is_not_deleted_on_conflict(self):
        self.q.write(self.root/'a',b'a')
        with self.assertRaises(FileExistsError): self.q.write(self.root/'a',b'b')
        self.assertEqual((self.root/'a').read_bytes(),b'a')

    def test_directory_move_preserves_cached_usage(self):
        (self.root/'old').mkdir();self.q.write(self.root/'old/a',b'a'*60)
        self.q.moved(self.root/'old',self.root/'new');(self.root/'old').rename(self.root/'new')
        self.q.changed(self.root/'old');self.q.changed(self.root/'new')
        self.assertEqual(self.q.usage()['used_bytes'],60)
        with self.assertRaises(Exception):self.q.write(self.root/'b',b'b'*50)
