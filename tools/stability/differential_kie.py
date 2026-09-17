"""Actual Kie code, identical HTTPS contracts; DNS and HTTP replaced, real sockets denied."""
import pathlib,os
# Reset only the synthetic cache in this audit snapshot, so repeated harness runs
# still measure a cold first upload. Never touch the installed owner's data.
audit_cache=pathlib.Path(os.environ['STABILITY_REPLAY_ROOT']).resolve()/'snapshots/owner/data/kie_reference_cache.json'
assert audit_cache.is_relative_to(pathlib.Path(os.environ['STABILITY_REPLAY_ROOT']).resolve())
audit_cache.unlink(missing_ok=True)
s=pathlib.Path(__file__).with_name('differential_llm.py').read_text().split("ok={'choices'")[0]
s=s.replace("('llm-'+version+'.json')","('kie-'+version+'.json')").replace("os.environ['HOME']=", "os.environ['KIE_API_KEY']='fake-audit-key';os.environ['HOME']=").replace("http://127.0.0.1:54321/v1","https://upstream.mock.example").replace("'protocol':'openai'","'protocol':'kie'").replace("'mode':'mock'","'mode':'live'").replace("'image_models':['gpt-image-2']","'image_models':['gpt-image-2','gpt-image-2.5-flare']").replace("{'id':'gpt-image-2','purpose':'image'}]","{'id':'gpt-image-2','purpose':'image'},{'id':'gpt-image-2.5-flare','purpose':'image'}]")
exec(compile(s,__file__,'exec'))
socket.getaddrinfo=lambda *args,**kwargs:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443))]
from urllib.parse import urlsplit
from email.parser import BytesParser
from email.policy import default
pngbuf=io.BytesIO();Image.new('RGBA',(96,64),(1,2,3,100)).save(pngbuf,'PNG');png=pngbuf.getvalue();counter=0;scenario={}
async def transport(req):
 global counter
 b=req.content
 try:body=json.loads(b)
 except:
  body={'bytes':len(b)}
  if 'multipart/' in req.headers.get('content-type',''):
   msg=BytesParser(policy=default).parsebytes(('Content-Type: '+req.headers['content-type']+'\r\n\r\n').encode()+b);body=[]
   for part in msg.iter_parts():
    value=part.get_payload(decode=True);name=part.get_param('name',header='content-disposition')
    body.append({'name':name,'value':value.decode() if not part.get_filename() else {'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest(),'mime':part.get_content_type()}})
 captures.append({'method':req.method,'url':str(req.url),'body':body,'auth_present':bool(req.headers.get('authorization'))})
 path=urlsplit(str(req.url)).path
 if req.method=='POST' and path=='/api/file-stream-upload':return httpx.Response(200,json={'code':200,'data':{'downloadUrl':'https://cdn.mock.example/ref.png'}},request=req)
 if req.method=='POST' and path=='/api/v1/jobs/createTask':
  if set(body)!={'model','input'} or not body['input'].get('prompt'):raise AssertionError('UNMATCHED_KIE_BODY')
  counter+=1;return httpx.Response(200,json={'code':200,'data':{'taskId':'offline-'+str(counter)}},request=req)
 if req.method=='GET' and path=='/api/v1/jobs/recordInfo':return httpx.Response(200,json={'code':200,'data':{'state':'success','resultJson':json.dumps({'resultUrls':['https://cdn.mock.example/result.png']})}},request=req)
 if path.endswith('result.png') and scenario.get('fail_download'):return httpx.Response(503,json={'error':'offline download error'},request=req)
 if req.method!='GET' or path not in {'/result.png','/ref.png'}:raise AssertionError('UNMATCHED_KIE_CONTRACT')
 return httpx.Response(200,content=png,headers={'content-type':'image/png'},request=req)
async def run():
 results=[]
 for case in ['first','cache_second','duplicate','model25','prompt_over20k','download_failure']:
  captures.clear();client_options.clear();scenario['fail_download']=case=='download_failure'
  payload=main.OnlineImageRequest(provider_id='audit',model='gpt-image-2.5-flare' if case=='model25' else 'gpt-image-2',prompt='x'*20001 if case=='prompt_over20k' else 'OFFLINE Kie prompt',request_id='audit-'+('cache_second' if case=='duplicate' else case),reference_images=[{'url':refs[0]}],aspect_ratio='3:2',resolution='2K')
  try:
   with contextlib.redirect_stdout(io.StringIO()):result=await asyncio.wait_for(main.online_image(payload),20)
   outcome={'status':200,'images':len(result.get('images',[]))}
  except Exception as e:
   handler=main.app.exception_handlers.get(type(e));response=await handler(None,e) if handler else None
   outcome={'status':response.status_code if response else getattr(e,'status_code',500),'exception':type(e).__name__,'detail':response.body.decode() if response else str(getattr(e,'detail',''))[:400]}
  results.append({'case':case,'requests':list(captures),'clients':list(client_options),'outcome':outcome})
  if case=='download_failure' and version=='public':
   with main.INSTANCE_MODELS.db() as db:jobs=[json.loads(r['data']) for r in db.execute('select data from jobs')]
   job=jobs[-1];outcome['ledger_before']={k:job.get(k) for k in ['status','upstream_status','local_result_status','submission_uncertain']}
   captures.clear();scenario['fail_download']=False;main.INSTANCE_MODELS.refresh(job['id']);runner=main.INSTANCE_MODELS.runners.get(job['id'])
   if runner:
    with contextlib.redirect_stdout(io.StringIO()):await runner
   after=main.INSTANCE_MODELS.owned(job['id']);outcome['recovery_status']=after['status'];outcome['recovery_requests']=list(captures)
 report_handle.write(json.dumps(results,ensure_ascii=False,indent=2));report_handle.close();print(version,len(results),'Kie cases completed')
asyncio.run(run())
