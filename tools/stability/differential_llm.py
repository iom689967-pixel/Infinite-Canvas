"""Execute unchanged snapshot business code; replace only HTTP transport. Fake input only."""
import os,sys,json,asyncio,time,pathlib,base64,hashlib,io,gzip,socket,contextlib
from unittest.mock import patch
ROOT=pathlib.Path(os.environ['STABILITY_REPLAY_ROOT']).resolve();version=sys.argv[1];src=ROOT/'snapshots'/version;data=ROOT/'runtime'/(version+'-'+str(os.getpid()));data.mkdir(parents=True,exist_ok=True)
for k in list(os.environ):
 if any(s in k.upper() for s in ['KEY','TOKEN','SECRET','PROXY','INSTANCE_','PROGRAM_ROOT','CODEX_AUTH']):os.environ.pop(k,None)
os.environ['HOME']=str(data/'home');os.environ['API_PROVIDER_AUDIT_KEY']='fake-audit-key';os.chdir(src);sys.path.insert(0,str(src));sys.dont_write_bytecode=True
if version=='public':os.environ.update(INSTANCE_ID='audit',INSTANCE_DATA_ROOT=str(data/'instance'),INSTANCE_HOST='127.0.0.1',INSTANCE_PORT='54322',INSTANCE_AUTH_ALLOW_HTTP_LOOPBACK='1',INSTANCE_MOCK_UPSTREAMS='127.0.0.1:54321')
# No actual outbound sockets are permitted in this harness.
def denied(*a,**kw):raise RuntimeError('AUDIT_REAL_NETWORK_BLOCKED')
socket.socket.connect=denied;socket.socket.connect_ex=denied
import httpx
from PIL import Image
Original=httpx.AsyncClient;captures=[];client_options=[];fixture={}
class Bytes(httpx.AsyncByteStream):
 async def __aiter__(self):
  b=fixture['body']
  for i in range(0,len(b),7):yield b[i:i+7]
async def transport(req):
 body=req.content
 try:body=json.loads(body)
 except:body={'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
 captures.append({'method':req.method,'url':str(req.url),'body':body,'auth_present':bool(req.headers.get('authorization') or req.headers.get('x-goog-api-key'))})
 if fixture.get('exception'):raise getattr(httpx,fixture['exception'])('offline synthetic failure',request=req)
 return httpx.Response(fixture.get('status',200),headers=fixture.get('headers',{'content-type':'application/json'}),stream=Bytes(),request=req)
class Client(Original):
 def __init__(self,*a,**kw):
  client_options.append({k:str(kw.get(k,'default')) for k in ['timeout','trust_env','follow_redirects']});kw['transport']=httpx.MockTransport(transport);super().__init__(*a,**kw)
httpx.AsyncClient=Client
if version=='public':
 from instance_auth import AuthStore
 r=data/'instance';r.mkdir(exist_ok=True);(r/'.instance.json').write_text(json.dumps({'instance_id':'audit','data_root':str(r)}))
 store=AuthStore(r,'audit',initialize=True)
 with store.connect() as c: count=c.execute('SELECT count(*) FROM accounts').fetchone()[0]
 if not count:store.create_account('audit','fake-audit-password')
 (r/'.auth/gateway-handoff.json').write_text('{}')
 (r/'.auth/public-beta.json').write_text(json.dumps({'storage_quota':5368709120,'min_free_disk_bytes':0,'max_upload':33554432,'max_concurrent_generations':2}))
report_handle=open(ROOT/'evidence'/('llm-'+version+'.json'),'w')
with contextlib.redirect_stdout(io.StringIO()):import main
provider={'id':'audit','name':'Audit','base_url':'http://127.0.0.1:54321/v1','protocol':'openai','enabled':True,'image_request_mode':'openai','image_edit_route':'general','chat_models':['gpt-6'],'image_models':['gpt-image-2'],'video_models':[],'model_protocols':{}}
if version=='owner':main.save_api_providers([provider])
else:
 from instance_model_policy import ModelPolicy
 from instance_auth import PRINCIPAL
 PRINCIPAL.set({'subject':'audit-user','username':'audit','own_providers':True})
 auth=main.PATHS.data_root/'.auth';(auth/'credentials').mkdir(exist_ok=True,mode=0o700);ref='credentials/personal-'+'1'*32+'.key';(auth/ref).write_text('fake-audit-key');(auth/ref).chmod(0o600)
 stored={k:provider[k] for k in ['id','name','base_url','protocol','enabled']};stored.update(models=[{'id':'gpt-6','purpose':'llm'},{'id':'gpt-image-2','purpose':'image'}],credential_file=ref,revision='audit-revision',settings=provider,secret_refs={'api_key':ref})
 cfg=auth/'model-access.json';cfg.write_text(json.dumps({'schema_version':1,'mode':'mock','max_concurrent':2,'providers':[],'personal_providers':[stored]}));cfg.chmod(0o600);main.INSTANCE_MODELS.policy=ModelPolicy(main.PATHS)
# Both receive identical source bytes and URL names.
refs=[]
for name,mode,size,color in [('wide','RGB',(2048,1024),'blue'),('alpha','RGBA',(1600,900),(255,0,0,90))]:
 path=pathlib.Path(main.output_path_for(name+'.png','output'));path.parent.mkdir(parents=True,exist_ok=True);Image.new(mode,size,color).save(path,'PNG');refs.append(main.output_url_for(name+'.png','output'))
def fingerprint(v):
 if isinstance(v,dict):return {k:fingerprint(x) for k,x in v.items()}
 if isinstance(v,list):return [fingerprint(x) for x in v]
 if isinstance(v,str) and v.startswith('data:') and ';base64,' in v:
  h,b=v.split(';base64,',1);raw=base64.b64decode(b);im=Image.open(io.BytesIO(raw));return {'data_mime':h[5:],'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'size':list(im.size),'mode':im.mode}
 return v
ok={'choices':[{'message':{'content':'  OFFLINE RESULT  '}}],'usage':{'total_tokens':17}}
fixtures={'json_success':{'body':json.dumps(ok).encode()},'gzip_success':{'body':gzip.compress(json.dumps(ok).encode()),'headers':{'content-type':'application/json','content-encoding':'gzip','transfer-encoding':'chunked'}},'http401':{'status':401,'body':b'{"error":{"code":"invalid_api_key","message":"fake rejected"}}'},'http503':{'status':503,'body':b'{"error":{"message":"No available upstream"}}'},'business_error_200':{'body':b'{"error":{"code":"invalid_request","message":"fake business error"}}'},'empty_text':{'body':b'{"choices":[{"message":{"content":""}}]}'},'non_json':{'body':b'<html>synthetic failure</html>'},'empty_body':{'body':b''},'sse_instead_json':{'body':b'data: {"choices":[{"delta":{"content":"OFFLINE"}}]}\n\ndata: [DONE]\n\n','headers':{'content-type':'text/event-stream'}},'timeout':{'exception':'ReadTimeout','body':b''},'disconnect':{'exception':'RemoteProtocolError','body':b''},'json_list':{'body':b'[]'}}
async def run():
 results=[]
 for case,imgs in [('text',[]),('single',refs[:1]),('multiple',refs)]:
  for f in (fixtures if case=='single' else ['json_success']):
   fixture.clear();fixture.update(fixtures[f]);captures.clear();client_options.clear()
   payload=main.CanvasLLMRequest(provider='audit',model='gpt-6',message='反推提示词',images=imgs,system_prompt='  系统原文  ',messages=[{'role':'user','content':'历史问题'},{'role':'assistant','content':'历史回复'}])
   try:
    with contextlib.redirect_stdout(io.StringIO()):result=await main.canvas_llm(payload)
    outcome={'status':200,'result':result}
   except Exception as e:outcome={'status':getattr(e,'status_code',500),'exception':type(e).__name__,'detail':str(getattr(e,'detail',''))}
   results.append({'case':case,'fixture':f,'requests':fingerprint(captures),'clients':list(client_options),'outcome':outcome})
 # Explicit parameter handling and URL normalization probes use same real route.
 for case,changes in [('parameters',{'max_output_tokens':777,'temperature':0.2}),('long_prompt',{'message':'x'*12000})]:
  fixture.clear();fixture.update(fixtures['json_success']);captures.clear();client_options.clear()
  try:
   payload=main.CanvasLLMRequest(provider='audit',model='gpt-6',message=changes.pop('message','反推提示词'),**changes)
   with contextlib.redirect_stdout(io.StringIO()):result=await main.canvas_llm(payload)
   outcome={'status':200,'result':result}
  except Exception as e:outcome={'status':getattr(e,'status_code',400),'exception':type(e).__name__,'detail':str(getattr(e,'detail',''))[:500]}
  results.append({'case':case,'fixture':'json_success','requests':fingerprint(captures),'clients':list(client_options),'outcome':outcome})
 report_handle.write(json.dumps(results,ensure_ascii=False,indent=2));report_handle.close();print(version,len(results),'cases completed')
asyncio.run(run())
