"""Strict real Instance -> loopback upstream contract. No production credentials."""
import json,unittest
from urllib.parse import urlsplit
from unittest.mock import patch
import test_personal_network_adapters as network

class StrictTextMock(network.NetworkMock):
    def do_POST(self):
        path=urlsplit(self.path).path
        raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
        try:body=json.loads(raw)
        except ValueError:return self.reject('non-json')
        expected={'model','messages'}|({'stream'} if body.get('stream') else set())
        if path!='/v1/chat/completions' or set(body)!=expected or body.get('model')!='exact-gpt' or not isinstance(body.get('messages'),list) or not self.identity():return self.reject('request-contract')
        self.server.calls.append(('strict-text',self.identity(),body))
        case=getattr(self.server,'text_case','success')
        status,body={'success':(200,{'choices':[{'message':{'content':'  fixture text  '}}],'usage':{'total_tokens':7}}),'auth':(401,{'error':{'message':'FAKE_KEY prompt Cookie private'}}),'routing':(503,{'error':{'code':'no_available_accounts'}}),'rate':(429,{'error':{}}),'business':(200,{'error':{'code':'bad','message':'PRIVATE_PROMPT'}}),'empty':(200,{'choices':[{'message':{'content':''}}]}),'array':(200,[])}[case]
        return self.reply(body,status)
    def reject(self,reason):
        self.server.unmatched=getattr(self.server,'unmatched',[])+[reason]
        return self.reply({'error':{'code':'unexpected_mock_request'}},400)

class StabilityExecutionTests(unittest.TestCase):
    def setUp(self):
        self.n=network.PersonalNetworkTests()
        with patch.object(network,'NetworkMock',StrictTextMock):self.n.setUp()
        self.addCleanup(self.n.doCleanups);self.f=self.n.f
        self.n.save(purpose='llm',name='exact-gpt',base_url=self.f.mock.origin+'/v1')
        self.addCleanup(lambda:self.assertEqual(getattr(self.f.mock,'unmatched',[]),[]))
    def call(self,**extra):
        return self.f.request('A','POST','/api/canvas-llm',json=dict(provider='same-personal-id',model='exact-gpt',message='fixture text',**extra))
    def test_http_failure_classification_and_private_event(self):
        ids=[]
        for case,category,status in [('auth','upstream_auth',401),('routing','routing_unavailable',503),('rate','rate_limit',429)]:
            self.f.mock.text_case=case;r=self.call(request_id='client-forged-event')
            self.assertEqual(r.status_code,502);d=r.json()['detail'];ids.append(d['event_id'])
            self.assertEqual(d['category'],category);self.assertEqual(d['provider_status'],status);self.assertIsNone(d['upstream_status']);self.assertNotIn('FAKE_KEY',r.text);self.assertNotIn('Cookie',r.text)
        self.assertEqual(len(set(ids)),3);self.assertNotIn('client-forged-event',ids)
        self.assertEqual(len(self.f.mock.calls),3)
    def test_missing_model_or_provider_never_reaches_upstream(self):
        for provider,model in [('gone','exact-gpt'),('same-personal-id','gone')]:
            r=self.f.request('A','POST','/api/canvas-llm',json=dict(provider=provider,model=model,message='keep'))
            self.assertEqual(r.status_code,403)
        self.assertEqual(self.f.mock.calls,[])
