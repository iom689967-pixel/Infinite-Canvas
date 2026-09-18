"""Loopback-only full Gateway/Instance browser fixture; never uses owner or production data.
stdin JSON: {op:delay,seconds:12}, {op:drop}, {op:status}, {op:finish}.
Delay/drop wraps the real gateway's response AFTER the real Instance writes.
"""
import asyncio,base64,hashlib,json,sys,tempfile,threading,time,socket
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from io import BytesIO
from PIL import Image
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx,uvicorn
from public_beta import create_app
from public_beta_store import BetaConfig
from instance_auth import password_hash
from test_instance_isolation import free_port

root=Path(tempfile.mkdtemp(prefix='smart-save-browser-')).resolve()
port=free_port();origin=f'http://127.0.0.1:{port}'
class SaveMock(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
        allowed=(self.path=='/v1/images/generations' and body.get('model') in ['synthetic-image-a','synthetic-image-b'] and isinstance(body.get('prompt'),str) and self.headers.get('Authorization')=='Bearer fake-isolated-save-a')
        mock.calls.append({'path':self.path,'allowed':allowed,'model':body.get('model'),'at':time.time()})
        if allowed:
            time.sleep(mock.delay)
            result={'data':[{'b64_json':base64.b64encode(mock.image).decode()}]}
        else:result={'error':{'code':'unexpected_mock_request'}}
        data=json.dumps(result).encode();self.send_response(200 if allowed else 400);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
mock=ThreadingHTTPServer(('127.0.0.1',0),SaveMock);mock.calls=[];mock.delay=12
picture=BytesIO();Image.new('RGB',(32,32),'blue').save(picture,format='PNG');mock.image=picture.getvalue()
threading.Thread(target=mock.serve_forever,daemon=True).start()
cfg=BetaConfig(root/'gateway' ,root/'instances',port=port,mail_mode='mock',min_free_disk=0,login_limit=100,max_upload=2*1024**2,storage_quota=4*1024**2,mock_upstreams=f'127.0.0.1:{mock.server_port}')
app=create_app(cfg);trace=[];armed={};lock=threading.Lock()
password='Synthetic-save-test-2026'
for name in ('save-a','save-b'):app.state.supervisor.provision(name,password_hash(password))
async def observer(scope,receive,send):
    save=scope['type']=='http' and scope['method']=='PUT' and scope['path'].startswith('/api/canvases/')
    if not save:return await app(scope,receive,send)
    with lock:mode=dict(armed);armed.clear()
    record={'path':scope['path'],'start':time.time()};trace.append(record);body=bytearray();messages=[]
    if mode.get('reject'):
        # Transport rejection fixture BEFORE the real application: never pretend a write failed after committing it.
        record.update(status=mode['reject'],delivered=time.time(),rejected_before_app=True)
        headers=[(b'content-type',b'application/json')]
        if mode['reject']==503:headers.append((b'x-mio-maintenance',b'1'))
        await send({'type':'http.response.start','status':mode['reject'],'headers':headers})
        await send({'type':'http.response.body','body':b'{"detail":{"code":"maintenance"}}' if mode['reject']==503 else b'{}'})
        return
    async def read():
        m=await receive()
        if m['type']=='http.request':body.extend(m.get('body',b''))
        return m
    async def collect(m):messages.append(m)
    await app(scope,read,collect)
    data=json.loads(body);record.update(base=data.get('base_updated_at'),node_texts=[n.get('text','') for n in data.get('nodes',[])],nodes=len(data.get('nodes',[])),complete=time.time(),status=next(m['status'] for m in messages if m['type']=='http.response.start'))
    if mode.get('seconds'):await asyncio.sleep(mode['seconds'])
    if mode.get('drop'):
        # Real response loss after write: return a truncated response instead of a fake success.
        await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]})
        await send({'type':'http.response.body','body':b'{','more_body':False})
    else:
        for m in messages:await send(m)
    record['delivered']=time.time()
server=uvicorn.Server(uvicorn.Config(observer,host='127.0.0.1',port=port,log_level='error'))
thread=threading.Thread(target=server.run,daemon=True);thread.start()
while not server.started:time.sleep(.05)
clients={};canvases={}
try:
    for name in ('save-a','save-b'):
        c=httpx.Client(base_url=origin,headers={'Origin':origin},trust_env=False,timeout=30);clients[name]=c
        c.post('/api/beta/login',json={'username':name,'password':password}).raise_for_status()
        csrf=c.get('/api/beta/me').json()['csrf'];c.post('/api/beta/enter',headers={'X-CSRF-Token':csrf}).raise_for_status()
        c.headers['X-CSRF-Token']=c.get('/api/auth/me').json()['csrf']
        c.put('/api/instance/provider-settings',json=[dict(id='synthetic-api',name='Synthetic only',protocol='openai',base_url=f'http://127.0.0.1:{mock.server_port}/v1',api_key='fake-isolated-'+name,chat_models=['synthetic-chat'],image_models=['synthetic-image-a','synthetic-image-b'])]).raise_for_status()
        ids={}
        for kind in ('smart','classic'):
            x=c.post('/api/canvases',json={'title':'Save reliability '+kind,'kind':kind}).json()['canvas'];cid=x['id'];ids[kind]=cid
            x.update(nodes=[{'id':'p','type':'smart-prompt' if kind=='smart' else 'prompt','text':'A','x':120,'y':140}],base_updated_at=x['updated_at'])
            c.put('/api/canvases/'+cid,json=x).raise_for_status()
        canvases[name]=ids
    print(json.dumps({'origin':origin,'root':str(root),'users':['save-a','save-b'],'password':password,'canvases':canvases}),flush=True)
    for line in sys.stdin:
        cmd=json.loads(line);op=cmd['op']
        if op=='finish':break
        if op=='model-delay':mock.delay=float(cmd['seconds'])
        if op in ('delay','drop','reject'):
            with lock:armed.update({'seconds':cmd.get('seconds',0),'drop':op=='drop','reject':cmd.get('status') if op=='reject' else None})
        if op=='status':
            result={n:{k:c.get('/api/canvases/'+v).json()['canvas'] for k,v in canvases[n].items()} for n,c in clients.items()}
            (root/'browser-state.json').write_text(json.dumps({'trace':trace,'canvases':result,'mock_calls':mock.calls},ensure_ascii=False,indent=2))
            print(json.dumps({'trace_count':len(trace),'mock_calls':mock.calls,'state_file':str(root/'browser-state.json')},ensure_ascii=False),flush=True)
        else:print(json.dumps({'armed':armed}),flush=True)
finally:
    (root/'trace.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2))
    (root/'mock-calls.json').write_text(json.dumps(mock.calls,indent=2))
    server.should_exit=True;thread.join(timeout=10)
    app.state.supervisor.close()
    for c in clients.values():c.close()
    mock.shutdown();mock.server_close()
