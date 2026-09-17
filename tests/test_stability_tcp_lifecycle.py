"""Real TCP Gateway -> real isolated Instance -> strict loopback upstream.

Only budgets are scaled for tests; request builders, auth, gate, HTTP clients,
streaming, cancellation and saving remain real. No test-only production routes.
"""
import asyncio,json,time,subprocess,threading
from unittest.mock import patch
import unittest,httpx,uvicorn
import test_maintenance_public as maintenance
import test_public_personal_api as personal
import test_public_beta as beta
from test_stability_execution import StrictTextMock
from model_budgets import Budget
from public_beta_maintenance import inspect

class StreamMock(StrictTextMock):
    protocol_version='HTTP/1.1'
    def do_POST(self):
        raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
        try:body=json.loads(raw)
        except ValueError:return self.reject('json')
        if self.path!='/v1/chat/completions' or body.get('model')!='exact-gpt' or set(body)-{'model','messages','stream'} or not self.identity():return self.reject('contract')
        self.server.calls.append(('strict-text',self.identity(),body));case=body['messages'][-1]['content'][0]['text']
        if not body.get('stream'):
            if case=='slow':time.sleep(.15)
            if case=='cancel':time.sleep(.5)
            return self.reply({'choices':[{'message':{'content':'fixture complete'}}]})
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Transfer-Encoding','chunked');self.end_headers()
        def chunk(data):
            b=data.encode();self.wfile.write(('%x\r\n'%len(b)).encode()+b+b'\r\n');self.wfile.flush()
        try:
            chunk('data: '+json.dumps({'choices':[{'delta':{'content':'part '*30}}]})+'\n\n')
            if case=='idle':time.sleep(.5)
            elif case in {'cancel','total'}:
                for _ in range(15):time.sleep(.1);chunk('data: '+json.dumps({'choices':[{'delta':{'content':'part '*10}}]})+'\n\n')
            if case!='eof':chunk('data: [DONE]\n\n')
            self.wfile.write(b'0\r\n\r\n');self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError):pass

class GatewayStreamLifecycleTests(unittest.IsolatedAsyncioTestCase):
    register=beta.PublicBetaTests.register
    enter=beta.PublicBetaTests.enter
    mock=personal.PublicPersonalAPITests.mock
    async def asyncSetUp(self):
        await maintenance.PublicMaintenanceTests.asyncSetUp(self)
        self.budgets=patch('model_budgets.TEXT',Budget(.2,.25,.65,cleanup=.1,gateway_margin=.2));self.budgets.start();self.addCleanup(self.budgets.stop)
        with patch.object(personal,'NetworkMock',StreamMock):self.upstream=await self.mock()
        original=subprocess.Popen
        def worker(args,*a,**kw):
            if isinstance(args,list) and len(args)==3 and str(args[1]).endswith('public_beta_worker.py') and args[2]=='serve':
                assert str(self.root) in kw['env']['INSTANCE_DATA_ROOT']
                code="import model_budgets,runpy,sys; model_budgets.TEXT=model_budgets.Budget(.2,.25,.65,cleanup=.1,gateway_margin=.2); sys.argv=["+repr(str(args[1]))+",'serve']; runpy.run_path(sys.argv[0],run_name='__main__')"
                args=[args[0],'-c',code]
            return original(args,*a,**kw)
        with patch('subprocess.Popen',worker):
            await self.register();await self.enter()
        r=await self.client.put('/api/instance/provider-settings',json=[dict(id='text',name='Mock',protocol='openai',base_url=self.upstream.origin+'/v1',api_key='fake-alice',chat_models=['exact-gpt'])]);self.assertEqual(r.status_code,200,r.text)
        self.server=uvicorn.Server(uvicorn.Config(self.app,host='127.0.0.1',port=self.config.port,log_level='critical',lifespan='off',timeout_graceful_shutdown=3))
        self.server_task=asyncio.create_task(self.server.serve())
        while not self.server.started:await asyncio.sleep(.01)
        self.tcp=httpx.AsyncClient(base_url=self.config.origin,cookies=self.client.cookies,headers=dict(self.client.headers),trust_env=False,timeout=5)
    async def asyncTearDown(self):
        await self.tcp.aclose();self.server.should_exit=True;await asyncio.wait_for(self.server_task,5)
        await beta.PublicBetaTests.asyncTearDown(self)
    def payload(self,case):return dict(provider='text',model='exact-gpt',message=case)
    async def drained(self):
        for _ in range(100):
            if self.gate.status()['active']==0:return
            await asyncio.sleep(.02)
        self.fail('local activities did not finish')
    async def test_tcp_disconnect_twice_releases_slots_but_retains_uncertainty(self):
        for _ in range(2):
            async with self.tcp.stream('POST','/api/chat/stream',json=self.payload('cancel')) as r:
                async for line in r.aiter_lines():
                    if '"type": "delta"' in line:break
            await self.drained()
        self.assertEqual(inspect(self.gate,[])['unknown_llm_responses'],2)
        replies=await asyncio.gather(*(self.tcp.post('/api/canvas-llm',json=self.payload('slow')) for _ in range(2)))
        self.assertEqual([r.status_code for r in replies],[200,200]);await self.drained()
        self.assertEqual(inspect(self.gate,[])['unknown_llm_responses'],2)
        self.assertEqual(len(self.upstream.calls),4);self.assertEqual(getattr(self.upstream,'unmatched',[]),[])
    async def test_slow_first_token_idle_total_and_eof(self):
        r=await self.tcp.post('/api/canvas-llm',json=self.payload('slow'));self.assertEqual(r.status_code,200,r.text)
        for case,category in [('idle','timeout'),('total','timeout'),('eof','incomplete_stream')]:
            r=await self.tcp.post('/api/chat/stream',json=self.payload(case))
            self.assertIn('"category": "'+category+'"',r.text);self.assertNotIn('"type": "done"',r.text);await self.drained()
        self.assertEqual(inspect(self.gate,[])['unknown_llm_responses'],3)
        listed=(await self.tcp.get('/api/conversations')).json()['conversations']
        self.assertTrue(listed)
        self.assertEqual(len(self.upstream.calls),4)

    async def test_nonstream_tcp_disconnect_keeps_remote_unknown(self):
        task=asyncio.create_task(self.tcp.post('/api/canvas-llm',json=self.payload('cancel')))
        for _ in range(80):
            if self.upstream.calls:break
            await asyncio.sleep(.01)
        self.assertEqual(len(self.upstream.calls),1)
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        await self.drained()
        self.assertEqual(inspect(self.gate,[])['unknown_llm_responses'],1)
        result=await self.tcp.post('/api/canvas-llm',json=self.payload('slow'))
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(len(self.upstream.calls),2)
