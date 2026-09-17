"""Temporary A/B browser fixture: fake accounts, strict loopback upstream only."""
import io,json,sys,time,httpx
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_personal_network_adapters as net
from instance_auth import AuthStore
class BrowserMock(net.NetworkMock):
    def reply(self,payload,*a,**kw):
        if isinstance(payload,dict) and isinstance(payload.get('data'),list) and payload['data'] and 'b64_json' in payload['data'][0]:payload={'data':[{'url':self.server.origin+'/media/network.png'}]}
        return super().reply(payload,*a,**kw)
    def do_POST(self):
        if self.path=='/v1/chat/completions':
            raw=self.rfile.read(int(self.headers.get('Content-Length','0')));body=json.loads(raw)
            if set(body)-{'model','messages','stream','max_tokens','temperature'} or body.get('model')!='novel-model-v2030' or not self.identity():return self.reject('text contract')
            text=json.dumps(body,ensure_ascii=False)
            if 'BROWSER_FAIL' in text:
                self.server.calls.append(('text',self.identity(),body));return self.reply({'error':{'code':'no_available_accounts','message':'DO_NOT_DISPLAY_PRIVATE'}} ,503)
            if body.get('stream') and 'BROWSER_SLOW' in text:
                self.server.calls.append(('text',self.identity(),body))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                try:
                    self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{'content':'PARTIAL '*40}}]})+'\n\n').encode());self.wfile.flush();time.sleep(1)
                    self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):pass
                return
            if 'BROWSER_SLOW' in text:time.sleep(1)
            self.rfile=io.BytesIO(raw)
        return super().do_POST()
f=net.PersonalNetworkTests()
try:
    with patch.object(net,'NetworkMock',BrowserMock):f.setUp()
    for owner in ('A','B'):
        store=AuthStore(f.f.roots[owner],owner);store.set_provider_permission(owner,True);store.change_account(owner,password='Mock-browser-stability-2026')
        origin=f'http://127.0.0.1:{f.f.ports[owner]}'
        with httpx.Client(trust_env=False) as client:
            client.post(origin+'/api/auth/login',headers={'Origin':origin},json={'username':owner,'password':'Mock-browser-stability-2026'}).raise_for_status()
            f.f.cookies[owner]=dict(client.cookies);f.f.csrf[owner]=client.get(origin+'/api/auth/me').json()['csrf']
    f.save(base_url=f.f.mock.origin+'/v1',chat_models=['novel-model-v2030'])
    canvases={kind:f.f.ok('A','POST','/api/canvases',json={'title':'Mock '+kind,'kind':kind})['canvas']['id'] for kind in ('normal','smart')}
    print(json.dumps({'A':f'http://127.0.0.1:{f.f.ports["A"]}','B':f'http://127.0.0.1:{f.f.ports["B"]}','canvases':canvases}),flush=True)
    for line in sys.stdin:
        cmd=json.loads(line);op=cmd['op']
        if op=='finish':break
        if op=='download':f.f.mock.download_fail=cmd['fail']
        if op=='catalog':f.save(base_url=f.f.mock.origin+'/v1',chat_models=['novel-model-v2030'],image_models=['novel-model-v2030',*(['new-visible-model'] if cmd['add'] else [])])
        print(json.dumps({'calls':len(f.f.mock.calls),'submits':sum(c[0]=='network-submit' for c in f.f.mock.calls),'text':sum(c[0]=='text' for c in f.f.mock.calls),'unmatched':getattr(f.f.mock,'unmatched',[])}),flush=True)
finally:f.doCleanups()
