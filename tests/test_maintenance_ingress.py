"""Temporary observer never leaks session cookies or treats interrupted work as done."""
import asyncio
from pathlib import Path
import tempfile
import unittest
import httpx
from public_beta_maintenance_ingress import create_ingress
from instance_maintenance import Maintenance, initialize
from public_beta_maintenance import set_phase, inspect


class IngressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='mio-ingress-test-')
        self.root=Path(self.tmp.name).resolve()/'control';initialize(self.root)
        self.gate=Maintenance(self.root);set_phase(self.gate,'open');self.addCleanup(self.tmp.cleanup)

    async def test_upstream_cookie_jar_never_supplies_another_users_session(self):
        seen=[]
        class Done(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"ok":true}'
        def mock(request):
            seen.append(request.headers.get('cookie',''))
            return httpx.Response(200,stream=Done(),headers={'Set-Cookie':'fake-owner=private; Path=/'})
        app=create_ingress(self.root,'http://127.0.0.1:48000',public_host='observer.test',
                           proxied=False,mock=True,transport=httpx.MockTransport(mock))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://observer.test') as client:
            await client.get('/api/config',headers={'Cookie':'user-a=private'})
            client.cookies.clear()
            await client.get('/api/config')
            await client.get('/api/config',headers={'Cookie':'user-b=private'})
        self.assertEqual(seen,['user-a=private','','user-b=private'])
        self.assertEqual(inspect(self.gate,[])['unknown_llm_responses'],0)

    async def test_interrupted_old_stream_retains_uncertainty_after_local_release(self):
        entered=asyncio.Event()
        class Held(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set();yield b'data: first\n\n';await asyncio.Event().wait()
        app=create_ingress(self.root,'http://127.0.0.1:48000',public_host='observer.test',proxied=False,mock=True,
                           transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=Held())))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://observer.test') as client:
            task=asyncio.create_task(client.post('/api/chat/stream',json={'message':'fake'}))
            await entered.wait();self.assertEqual(self.gate.status()['active'],1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        set_phase(self.gate,'draining');status=inspect(self.gate,[],seal=True)
        self.assertEqual(status['active'],0);self.assertEqual(status['unknown_llm_responses'],1)
        self.assertFalse(status['restart_safe'])

    def test_observer_cannot_be_used_as_arbitrary_network_proxy(self):
        for target in ('https://provider.test','http://198.51.100.1:48000','http://127.0.0.1:48000/path','http://user:secret@127.0.0.1:48000'):
            with self.subTest(target=target),self.assertRaises(ValueError):
                create_ingress(self.root,target,public_host='observer.test')
