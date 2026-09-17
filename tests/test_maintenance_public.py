"""Real temporary Gateway/worker requests, fake credentials and loopback upstream."""
import asyncio
import httpx
import unittest
import socket
from unittest.mock import patch
from public_beta import create_app
from instance_maintenance import initialize, Maintenance, DETAIL
from public_beta_maintenance import set_phase, inspect, coverage_blockers
import test_public_beta as beta
import test_public_personal_api as personal
from test_personal_network_adapters import SETTINGS
from test_personal_network_adapters import NetworkMock


class DropTextMock(NetworkMock):
    def do_POST(self):
        if self.path.endswith('/chat/completions') and getattr(self.server,'drop_text',False):
            self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
        return super().do_POST()


class PublicMaintenanceTests(unittest.IsolatedAsyncioTestCase):
    register=beta.PublicBetaTests.register
    enter=beta.PublicBetaTests.enter
    mock=personal.PublicPersonalAPITests.mock
    asyncTearDown=beta.PublicBetaTests.asyncTearDown

    async def asyncSetUp(self):
        await beta.PublicBetaTests.asyncSetUp(self)
        await self.client.aclose()
        self.config.maintenance_root=self.root/'control';initialize(self.config.maintenance_root)
        self.gate=Maintenance(self.config.maintenance_root);set_phase(self.gate,'open')
        self.app=create_app(self.config);self.store=self.app.state.store;self.supervisor=self.app.state.supervisor
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url=self.config.origin,
                                     headers={'Origin':self.config.origin})

    async def test_gate_covers_logged_in_users_and_native_runners_finish_during_drain(self):
        mock=await self.mock();mock.finish_waiting=False
        principal=await self.register();await self.enter()
        payload=dict(id='own',name='Private',protocol='kie',base_url=mock.origin,api_key='fake-alice',
                     image_models=['gpt-image-2'])
        response=await self.client.put(SETTINGS,json=[payload]);self.assertEqual(response.status_code,200,response.text)
        response=await self.client.post('/api/canvas-image-tasks',json=dict(provider_id='own',model='gpt-image-2',
            prompt='WAIT_IMAGE',resolution='1K',aspect_ratio='1:1',size='1024x1024',n=1))
        self.assertEqual(response.status_code,200,response.text);task=response.json()['task_id']
        for _ in range(100):
            if any(k=='create' for k,_,_ in mock.calls):
                break
            await asyncio.sleep(.02)
        set_phase(self.gate,'draining')
        self.assertEqual(self.gate.status()['by_phase'],{'runner_image':1}, (await self.client.get('/api/canvas-image-tasks/'+task)).json())
        self.assertFalse(inspect(self.gate,[self.supervisor.root(self.store.instance(principal['id']))],seal=True)['restart_safe'])
        before=len(mock.calls)
        for route in ('/api/canvas-image-tasks','/api/canvas-video-tasks','/api/canvas-video','/api/canvas-llm',
                      '/api/chat/stream','/api/chat/agent','/api/local-assets/caption','/api/local-assets/classify',
                      '/api/temp-sh/upload','/api/cloud-video/upload','/api/runninghub/upload-asset',
                      '/api/instance/provider-settings/test-connection','/api/instance/provider-settings/probe-async',
                      '/api/beta/register'):
            response=await self.client.post(route,json={})
            self.assertEqual(response.status_code,503,route);self.assertEqual(response.json(),{'detail':DETAIL})
        self.assertFalse(any(k in {'text','create','upload','network-submit','network-upload'} for k,_,_ in mock.calls[before:]))
        self.assertEqual((await self.client.get('/api/config')).status_code,200)
        mock.finish_waiting=True
        for _ in range(150):
            response=await self.client.get('/api/canvas-image-tasks/'+task);value=response.json()
            if value['status']=='succeeded' and not value['local_wait_active']:
                break
            await asyncio.sleep(.02)
        self.assertEqual(value['status'],'succeeded')
        self.assertEqual(coverage_blockers(self.gate,self.store.path),())
        root=self.supervisor.root(self.store.instance(principal['id']))
        self.assertTrue(inspect(self.gate,[root],seal=True)['restart_safe'])
        response=await self.client.post('/api/canvas-image-tasks/'+task+'/refresh',json={})
        self.assertEqual(response.status_code,503)
        await asyncio.to_thread(self.supervisor.stop,principal['id'])
        await asyncio.to_thread(self.supervisor.start,principal['id'])
        self.assertEqual(self.gate.state()['phase'],'sealed')
        self.assertEqual((await self.client.post('/api/canvas-llm',json={})).status_code,503)
        set_phase(self.gate,'draining');await self.enter()
        self.assertEqual((await self.client.get('/api/canvas-image-tasks/'+task)).json()['status'],'succeeded')
        set_phase(self.gate,'open')

    async def test_untrusted_admission_header_cannot_bypass_gateway_gate(self):
        await self.register();await self.enter();set_phase(self.gate,'draining')
        response=await self.client.post('/api/canvas-llm',json={},headers={'X-Mio-Maintenance-Parent':'invented'})
        self.assertEqual(response.status_code,503);self.assertEqual(response.json(),{'detail':DETAIL})

    async def test_full_json_sse_and_unknown_llm_submit_use_actual_controlled_client(self):
        with patch.object(personal,'NetworkMock',DropTextMock):
            mock=await self.mock()
        await self.register();await self.enter()
        response=await self.client.put(SETTINGS,json=[dict(id='text',name='Private',protocol='openai',
            base_url=mock.origin+'/openai',api_key='fake-alice',chat_models=['exact-future-id'])])
        self.assertEqual(response.status_code,200,response.text)
        for route in ('/api/canvas-llm','/api/chat/stream'):
            response=await self.client.post(route,json=dict(provider='text',model='exact-future-id',message='mock'))
            self.assertEqual(response.status_code,200,response.text)
            self.assertIn('MOCK',response.text)
            self.assertEqual(inspect(self.gate,[])['unknown_llm_responses'],0)
        mock.drop_text=True
        response=await self.client.post('/api/canvas-llm',json=dict(provider='text',model='exact-future-id',message='mock'))
        self.assertEqual(response.status_code,502,response.text)
        set_phase(self.gate,'draining');result=inspect(self.gate,[],seal=True)
        self.assertEqual(result['active'],0);self.assertEqual(result['unknown_llm_responses'],1)
        self.assertFalse(result['restart_safe'])
