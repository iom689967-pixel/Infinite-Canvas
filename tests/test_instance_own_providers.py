"""Personal API management through two actual ASGI instances; fake keys/local mock only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from instance_auth import AuthStore
from instance_model_policy import GuardedClient, OUTBOUND_ENDPOINTS
import test_instance_models as helpers

API = '/api/instance/providers'


class PersonalMock(helpers.ModelMock):
    def do_GET(self):
        if self.path.endswith('/models'):
            if not self.identity():
                return self.reply({}, 401)
            self.server.calls.append(('discover', self.identity(), {}))
            if self.path.startswith('/redirect/'):
                return self.reply(b'', 302, headers={'Location': 'http://127.0.0.1:3000/secret'})
            return self.reply({'models': [{'name': 'models/gemini-personal'}], 'data': [{'id': 'arbitrary-text-v99'}]})
        return super().do_GET()

    def do_POST(self):
        if self.path.endswith('/chat/completions'):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if not self.identity():
                return self.reply({}, 401)
            self.server.calls.append(('openai', self.identity(), data))
            return self.reply({'choices': [{'message': {'content': 'MOCK garment text'}}]})
        return super().do_POST()


class OwnProviderTests(unittest.TestCase):
    stop = helpers.ControlledModelTests.stop
    request = helpers.ControlledModelTests.request
    ok = helpers.ControlledModelTests.ok
    upload = helpers.ControlledModelTests.upload
    image_payload = helpers.ControlledModelTests.image_payload
    wait_job = helpers.ControlledModelTests.wait_job
    cleanup = helpers.ControlledModelTests.cleanup

    def setUp(self):
        with patch.object(helpers, 'ModelMock', PersonalMock):
            helpers.ControlledModelTests.setUp(self)

    def start(self, name):
        if name == 'A':
            AuthStore(self.roots[name], name).set_provider_permission(name, True)
        helpers.ControlledModelTests.start(self, name)

    def item(self, name='A', protocol='openai', **changes):
        model = {'openai':'arbitrary-text-v99', 'gemini':'gemini-personal', 'kie':'gpt-image-2'}[protocol]
        base = self.mock.origin + ('/v1' if protocol == 'openai' else '/v1beta' if protocol == 'gemini' else '')
        return dict(id='personal', name='My API', protocol=protocol, base_url=base, enabled=True,
                    models=[{'id':model,'purpose':'image' if protocol=='kie' else 'llm'}], **changes)

    def save_personal(self, name='A', protocol='openai'):
        item = self.item(name, protocol)
        return self.ok(name,'PUT',API,json=item | {'api_key':self.keys[name]})

    def test_permission_csrf_origin_and_global_routes_remain_denied(self):
        me = self.ok('A','GET','/api/auth/me')
        self.assertIn('manage_own_providers',me['permissions'])
        self.assertEqual(self.request('A','GET','/static/api-settings.html').status_code,200)
        self.assertEqual(self.request('A','GET','/static/js/i18n/api-settings.js').status_code,200)
        for path in (API, '/static/api-settings.html', '/static/js/instance-api-settings.js', '/static/js/i18n/api-settings.js'):
            self.assertEqual(self.request('B','GET',path).status_code,403)
        for headers in ({'X-CSRF-Token':''}, {'Origin':'http://evil.example'}):
            self.assertEqual(self.request('A','PUT',API,headers=headers,json=self.item()).status_code,403)
        with httpx.Client(trust_env=False) as client:
            self.assertEqual(client.get(f'http://127.0.0.1:{self.ports["A"]}'+API).status_code,401)
        for method,path in [('PUT','/api/providers'),('GET','/api/config/token'),('POST','/api/restart'),('POST','/api/update')]:
            self.assertEqual(self.request('A',method,path,json={}).status_code,403)
        self.assertEqual(self.mock.calls,[])

    def test_crud_key_semantics_restart_and_shared_provider_protection(self):
        result=self.save_personal()
        self.assertTrue(result['providers'][0]['has_key'])
        self.assertNotIn(self.keys['A'],json.dumps(result))
        item=self.item(); item['name']='renamed'
        self.ok('A','PUT',API,json=item)
        config=json.loads((self.roots['A']/'.auth/model-access.json').read_text())
        keyfile=self.roots['A']/'.auth'/config['personal_providers'][0]['credential_file']
        self.assertEqual(keyfile.read_text(),self.keys['A'])
        self.stop('A');self.start('A')
        self.assertEqual(self.ok('A','GET',API)['providers'][0]['name'],'renamed')
        self.assertEqual(self.request('A','PUT',API,json=item | {'id':'atelier-text'}).status_code,403)
        self.ok('A','PUT',API,json=item | {'clear_key':True})
        self.assertFalse(keyfile.exists())
        self.assertFalse(self.ok('A','GET',API)['providers'][0]['has_key'])
        self.assertNotIn('arbitrary-text-v99',self.ok('A','GET','/api/models')['chat_models'])
        self.ok('A','PUT',API,json=item | {'api_key':self.keys['A']})
        self.ok('A','PUT',API,json=item | {'enabled':False})
        self.assertEqual(self.request('A','POST','/api/canvas-llm',json={'provider':'personal','model':'arbitrary-text-v99','message':'hi'}).status_code,403)
        self.ok('A','DELETE',API,json={'id':'personal'})
        self.assertEqual(self.ok('A','GET',API)['providers'],[])
        self.assertEqual(self.mock.calls,[])

    def test_two_instances_same_id_and_model_use_only_own_credentials(self):
        self.stop('B');AuthStore(self.roots['B'],'B').set_provider_permission('B',True);self.start('B')
        for n in ('A','B'):
            self.save_personal(n)
            result=self.ok(n,'POST','/api/canvas-llm',json={'provider':'personal','model':'arbitrary-text-v99','message':'garment',
                                                         'base_url':'http://127.0.0.1:3000','instance_id':'other','api_key':'forged'})
            self.assertIn('garment',result['text'])
        self.assertEqual([owner for kind,owner,_ in self.mock.calls if kind=='openai'],['A','B'])
        for n,other in [('A','B'),('B','A')]:
            public=json.dumps(self.ok(n,'GET',API))+json.dumps(self.ok(n,'GET','/api/config'))
            self.assertNotIn(self.keys[other],public)
            self.assertNotIn(self.keys[n],public)
        for field,value in [('instance_id','B'),('credential_file','../B/.auth/credentials/kie.key'),('api_key_env','OWNER_KEY'),('headers',{'Authorization':'forged'})]:
            self.assertEqual(self.request('A','PUT',API,json=self.item() | {field:value}).status_code,400)
        self.ok('A','DELETE',API,json={'id':'personal'})
        self.assertEqual(len(self.ok('B','GET',API)['providers']),1)

    def test_discover_manual_models_native_gemini_reference_and_kie(self):
        for protocol in ('openai','gemini','kie'):
            self.save_personal(protocol=protocol)
            found=self.ok('A','POST',API+'/discover',json={'id':'personal'})
            self.assertTrue(found['models'])
            catalog=self.ok('A','GET','/api/config')['api_providers']
            self.assertTrue(any(p['id']=='personal' for p in catalog))
            ref=self.upload()
            if protocol=='kie':
                task=self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload(provider_id='personal',reference_images=[{'url':ref}]))['task_id']
                self.assertEqual(self.wait_job(task)['status'],'succeeded')
            else:
                result=self.ok('A','POST','/api/canvas-llm',json={'provider':'personal','model':self.item(protocol=protocol)['models'][0]['id'],
                                                               'message':'garment','images':[ref]})
                self.assertIn('garment',result['text'])
        gemini=next(p for kind,_,p in self.mock.calls if kind=='llm')
        self.assertIn('inlineData',json.dumps(gemini))
        self.assertEqual(gemini['generationConfig']['maxOutputTokens'],64)
        self.assertEqual(sum(kind=='create' for kind,_,_ in self.mock.calls),1)
        for n in ('A','B'):
            log=(self.root/f'{n}.log').read_text()
            self.assertNotIn(self.keys['A'],log);self.assertNotIn(self.keys['B'],log)

    def test_target_change_requires_new_key_redirect_and_dangerous_urls_denied(self):
        self.save_personal()
        changed=self.item();changed['base_url']=self.mock.origin+'/redirect/v1'
        self.assertEqual(self.request('A','PUT',API,json=changed).status_code,409)
        self.ok('A','PUT',API,json=changed | {'api_key':self.keys['A']})
        self.assertEqual(self.request('A','POST',API+'/discover',json={'id':'personal'}).status_code,502)
        for url in ('http://127.0.0.1:3000','http://127.0.0.1:'+str(self.ports['B']), 'file:///etc/passwd',
                    'http://169.254.169.254','http://[::1]:3000',self.mock.origin+'/%2e%2e/secret',self.mock.origin+'?key=secret'):
            self.assertEqual(self.request('A','PUT',API,json=self.item() | {'base_url':url,'clear_key':True}).status_code,400)
        self.assertEqual(self.mock.calls,[('discover','A',{})])

    def test_unsupported_models_stored_but_never_run_or_expand_limits(self):
        self.save_personal()
        item=self.item();item['models'] += [{'id':'future-image','purpose':'image'},{'id':'future-video','purpose':'video'}]
        saved=self.ok('A','PUT',API,json=item)
        self.assertFalse(saved['providers'][0]['models'][1]['supported'])
        self.assertNotIn('future-image',self.ok('A','GET','/api/models')['image_models'])
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks',json=self.image_payload(provider_id='personal',model='future-image')).status_code,403)
        self.assertEqual(self.request('A','PUT',API,json=item | {'max_concurrent':8}).status_code,400)
        self.assertEqual(self.request('A','PUT',API,json=item | {'model_limits':{}}).status_code,400)

    def test_running_changes_blocked_old_task_cannot_use_new_provider(self):
        self.save_personal(protocol='kie')
        task=self.ok('A','POST','/api/canvas-image-tasks',json=self.image_payload(provider_id='personal',prompt='WAIT_IMAGE'))['task_id']
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and not any(k=='create' for k,_,_ in self.mock.calls):time.sleep(.03)
        self.assertEqual(self.request('A','DELETE',API,json={'id':'personal'}).status_code,409)
        self.assertEqual(self.request('A','PUT',API,json=self.item(protocol='kie') | {'api_key':self.keys['A']}).status_code,409)
        self.mock.finish_waiting=True
        self.wait_job(task)
        # Terminal failure makes retry-query eligible, but configuration revision must still match.
        import sqlite3
        self.stop('A')
        with sqlite3.connect(self.roots['A']/'.auth/model-tasks.sqlite3') as db:
            row=db.execute('SELECT data FROM jobs WHERE id=?',(task,)).fetchone();job=json.loads(row[0]);job['status']='failed';job['outstanding']=False
            db.execute('UPDATE jobs SET data=? WHERE id=?',(json.dumps(job),task))
        self.start('A')
        self.ok('A','PUT',API,json=self.item(protocol='kie') | {'api_key':self.keys['A']})
        before=len(self.mock.calls)
        self.assertEqual(self.request('A','POST','/api/canvas-image-tasks/'+task+'/refresh').status_code,409)
        self.assertEqual(len(self.mock.calls),before)
        self.assertEqual(sum(k=='create' for k,_,_ in self.mock.calls),1)


class PersonalNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_personal_media_dns_private_ipv6_and_redirect_safe(self):
        p={'personal':True,'base_url':'https://model.example','credential_file':'private','media_origins':[]}
        policy=SimpleNamespace(mode='live',credential=lambda _: 'fake-private-key')
        client=GuardedClient(policy,p)
        try:
            for ip in ('127.0.0.1','10.0.0.1','169.254.169.254','::1','fc00::1','::ffff:127.0.0.1'):
                with patch('socket.getaddrinfo',return_value=[(2,1,6,'',(ip,443))]):
                    with self.assertRaises(Exception):await client.get('https://cdn.example/media.png')
            self.assertEqual(OUTBOUND_ENDPOINTS.get(),frozenset())
        finally:await client.aclose()

    async def test_live_public_discovery_and_media_pin_each_dns_answer_no_redirect(self):
        provider={'personal':True,'base_url':'https://model.example','credential_file':'private'}
        policy=SimpleNamespace(mode='live',credential=lambda _: 'mock-key',private_targets={})
        client=GuardedClient(policy,provider)
        calls=[]
        async def respond(request):
            calls.append((request.url.host,request.headers.get('Authorization'),OUTBOUND_ENDPOINTS.get()))
            return httpx.Response(302,headers={'Location':'https://different.example/leak'})
        await client.client.aclose()
        client.client=httpx.AsyncClient(transport=httpx.MockTransport(respond),follow_redirects=False)
        try:
            with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]):
                with self.assertRaises(Exception):
                    await client.get('https://model.example/v1/models',headers={'Authorization':'Bearer mock-key'})
            self.assertEqual(calls,[('model.example','Bearer mock-key',frozenset({('8.8.8.8',443)}))])
            self.assertEqual(OUTBOUND_ENDPOINTS.get(),frozenset())
            # The HTTP client is never reached when a second DNS answer rebinds private.
            with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
                with self.assertRaises(Exception):await client.get('https://model.example/v1/models',headers={'Authorization':'Bearer mock-key'})
            self.assertEqual(len(calls),1)
        finally:await client.aclose()

    async def test_admin_exact_private_target_not_entire_private_network(self):
        provider={'personal':True,'base_url':'https://lan.example:8443','credential_file':'private'}
        policy=SimpleNamespace(mode='live',credential=lambda _: 'mock-key',private_targets={('https','lan.example',8443):frozenset({'10.20.30.40'})})
        client=GuardedClient(policy,provider)
        await client.client.aclose()
        client.client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={})))
        try:
            with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('10.20.30.40',8443))]):
                self.assertEqual((await client.get('https://lan.example:8443/models',headers={'Authorization':'Bearer mock-key'})).status_code,200)
            for ip in ('10.20.30.41','127.0.0.1','169.254.169.254'):
                with patch('socket.getaddrinfo',return_value=[(2,1,6,'',(ip,8443))]):
                    with self.assertRaises(Exception):await client.get('https://lan.example:8443/models',headers={'Authorization':'Bearer mock-key'})
        finally:await client.aclose()


class PersonalSocketTests(unittest.TestCase):
    def test_socket_audit_rejects_actual_address_outside_dns_pin(self):
        import tempfile
        import subprocess
        import sys
        from pathlib import Path
        from test_instance_isolation import ROOT, instance_env
        with tempfile.TemporaryDirectory(prefix='own-provider-socket-') as directory:
            code = """import instance_paths,sys
from instance_model_policy import OUTBOUND_ENDPOINTS
OUTBOUND_ENDPOINTS.set(frozenset({('8.8.8.8',443)}))
sys.audit('socket.connect',None,('8.8.8.8',443))
for target in [('127.0.0.1',3000),('10.0.0.1',443),('8.8.4.4',443),('::1',443)]:
    try:sys.audit('socket.connect',None,target)
    except Exception:pass
    else:raise AssertionError('unapproved actual address')
"""
            result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=instance_env(Path(directory)/'A','A',43131),capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
