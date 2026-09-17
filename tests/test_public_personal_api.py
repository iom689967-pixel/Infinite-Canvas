"""New and legacy public accounts share capabilities without owner inheritance."""
import asyncio
import hashlib
from http.server import ThreadingHTTPServer
import threading
import unittest
from pathlib import Path
from instance_auth import AuthStore
import test_public_beta as beta
from test_personal_network_adapters import NetworkMock, SETTINGS

class PublicPersonalAPITests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=beta.PublicBetaTests.asyncSetUp
    asyncTearDown=beta.PublicBetaTests.asyncTearDown
    register=beta.PublicBetaTests.register
    enter=beta.PublicBetaTests.enter

    async def mock(self):
        mock=ThreadingHTTPServer(('127.0.0.1',0),NetworkMock)
        mock.calls,mock.jobs,mock.media=[],{},{}
        mock.credentials={hashlib.sha256(('fake-'+n).encode()).hexdigest():n for n in ('alice','bob')}
        mock.image=self.image;mock.origin=f'http://127.0.0.1:{mock.server_port}';mock.finish_waiting=True
        thread=threading.Thread(target=mock.serve_forever,daemon=True);thread.start()
        self.config.mock_upstreams=f'127.0.0.1:{mock.server_port}'
        self.addCleanup(mock.server_close);self.addCleanup(mock.shutdown)
        return mock

    async def test_new_registration_private_provider_defaults_and_a_b_isolation(self):
        mock=await self.mock()
        for name in ('alice','bob'):
            await self.register(name);me=await self.enter()
            self.assertIn('manage_own_providers',me['permissions'])
            self.assertEqual((await self.client.get(SETTINGS)).json()['providers'],[])
            self.assertEqual((await self.client.get('/api/config')).json()['api_providers'],[])
            payload=dict(id='same-id',name='Private API',protocol='openai',base_url=mock.origin+'/openai',api_key='fake-'+name,chat_models=['same-exact-model'],image_models=['same-exact-model'],video_models=['same-exact-model'])
            response=await self.client.put(SETTINGS,json=[payload]);self.assertEqual(response.status_code,200,response.text)
            response=await self.client.post('/api/canvas-llm',json=dict(provider='same-id',model='same-exact-model',message='garment'))
            self.assertEqual(response.status_code,200,response.text)
        self.assertEqual([o for k,o,_ in mock.calls if k=='text'],['alice','bob'])

    async def test_legacy_permission_and_saved_provider_survive_instance_upgrade(self):
        mock=await self.mock();principal=await self.register();me=await self.enter()
        payload=dict(id='same-id',name='Legacy',protocol='openai',base_url=mock.origin+'/openai',api_key='fake-alice',image_models=['gpt-image-2','gpt-image-2.5-flare','gpt-image-2.5-sunburst'])
        response=await self.client.put(SETTINGS,json=[payload]);self.assertEqual(response.status_code,200,response.text)
        root=Path(self.store.instance(principal['id'])['data_root'])
        old=(root/'.auth/model-access.json').read_bytes()
        await asyncio.to_thread(self.supervisor.stop,principal['id'])
        AuthStore(root,self.store.instance(principal['id'])['instance_id']).set_provider_permission('alice',False)
        await self.enter()
        self.assertEqual((root/'.auth/model-access.json').read_bytes(),old)
        providers=(await self.client.get(SETTINGS)).json()['providers']
        self.assertEqual(providers[0]['image_models'],payload['image_models'])
        catalog=(await self.client.get('/api/config')).json()['api_providers']
        self.assertEqual(catalog[0]['image_models'],payload['image_models'])
        self.assertTrue(all(cap['executable'] for cap in catalog[0]['capabilities']['image'].values()))

    async def test_owned_video_range_headers_survive_gateway_and_do_not_cross_users(self):
        mock=await self.mock();await self.register();await self.enter()
        payload=dict(id='same-id',name='Private API',protocol='openai',base_url=mock.origin+'/openai',api_key='fake-alice',video_models=['future-video'])
        response=await self.client.put(SETTINGS,json=[payload]);self.assertEqual(response.status_code,200)
        response=await self.client.post('/api/canvas-video',json=dict(provider_id='same-id',model='future-video',prompt='video garment'))
        self.assertEqual(response.status_code,200,response.text)
        url=response.json()['videos'][0]
        response=await self.client.get(url,headers={'Range':'bytes=0-31'})
        self.assertEqual(response.status_code,206);self.assertEqual(response.headers['content-range'],'bytes 0-31/1840')
        self.assertEqual(response.headers['accept-ranges'],'bytes');self.assertEqual(len(response.content),32)
        await self.register('bob');await self.enter()
        self.assertEqual((await self.client.get(url,headers={'Range':'bytes=0-31'})).status_code,404)
