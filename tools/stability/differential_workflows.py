"""Same-input, same-fixture generation/edit/video tests through original public routes."""
import pathlib
source=pathlib.Path(__file__).with_name('differential_llm.py').read_text().split("ok={'choices'")[0]
source=source.replace("'video_models':[]","'video_models':['audit-video']").replace("{'id':'gpt-image-2','purpose':'image'}]","{'id':'gpt-image-2','purpose':'image'},{'id':'audit-video','purpose':'video'}]").replace("('llm-'+version+'.json')","('workflows-'+version+'.json')")
exec(compile(source,__file__,'exec'))
from urllib.parse import urlsplit
from email.parser import BytesParser
from email.policy import default
png=io.BytesIO();Image.new('RGBA',(96,64),(1,2,3,100)).save(png,'PNG');png=png.getvalue()
# Minimal MP4 signature accepted by both existing saving paths; not a playback fixture.
mp4=b'\x00\x00\x00\x18ftypmp42'+b'\0'*128
scenario={};active_tasks={};counter=0
async def transport(req):
 global counter
 raw=req.content
 try:body=json.loads(raw)
 except:
  if req.headers.get('content-type','').startswith('multipart/'):
   msg=BytesParser(policy=default).parsebytes(('Content-Type: '+req.headers['content-type']+'\r\n\r\n').encode()+raw);body=[]
   for part in msg.iter_parts():
    v=part.get_payload(decode=True);name=part.get_param('name',header='content-disposition')
    body.append({'name':name,'value':v.decode() if not part.get_filename() else {'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest(),'mime':part.get_content_type()}})
  else:body={'bytes':len(raw)}
 captures.append({'method':req.method,'url':str(req.url),'body':fingerprint(body),'auth_present':bool(req.headers.get('authorization'))})
 path=urlsplit(str(req.url)).path
 if req.method=='GET' and path in {'/media/result.png','/media/result.mp4'}:
  if scenario.get('download_fail'):return httpx.Response(503,json={'error':'offline download failure'},request=req)
  return httpx.Response(200,content=mp4 if path.endswith('mp4') else png,headers={'content-type':'video/mp4' if path.endswith('mp4') else 'image/png'},request=req)
 if req.method=='GET' and path=='/v1/videos/generations/offline-video':return httpx.Response(200,json={'id':'offline-video','status':'completed','video_url':'http://127.0.0.1:54321/media/result.mp4'},request=req)
 if req.method!='POST' or path not in {'/v1/images/generations','/v1/images/edits','/v1/videos/generations'}:raise AssertionError('UNMATCHED_MEDIA_CONTRACT')
 if scenario.get('status'):return httpx.Response(scenario['status'],json={'error':{'message':'offline rejected'}},request=req)
 if scenario.get('video'):return httpx.Response(200,json={'id':'offline-video','status':'queued'},request=req)
 return httpx.Response(200,json={'data':[{'url':'http://127.0.0.1:54321/media/result.png'}]},request=req)
def local_results(result):
 urls=result.get('images') or ([result.get('video_url')] if result.get('video_url') else result.get('videos',[]));items=[]
 for u in urls:
  if isinstance(u,dict):u=u.get('url','')
  if isinstance(u,str) and u.startswith(('/assets/','/output/')):
   path=main.output_file_from_url(u);b=pathlib.Path(path).read_bytes();items.append({'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
 return {'result_keys':sorted(result),'model':result.get('model'),'media':items,'has_task_id':bool(result.get('task_id'))}
async def run():
 results=[]
 for case,changes in [('image_generate',{}),('image_edit',{'reference_images':[{'url':refs[0]}],'operation':'edit'}),('image_n2',{'n':2}),('image_401',{'_status':401}),('download_failure',{'_download_fail':True}),('video_async',{'_video':True})]:
  scenario.clear();scenario.update({'status':changes.pop('_status',None),'download_fail':changes.pop('_download_fail',False),'video':changes.pop('_video',False)});captures.clear();client_options.clear()
  before=len(json.loads(pathlib.Path(main.HISTORY_FILE).read_text()) if pathlib.Path(main.HISTORY_FILE).exists() else []);outcome={}
  try:
   if scenario['video']:payload=main.CanvasVideoRequest(provider_id='audit',model='audit-video',prompt='OFFLINE exact prompt',request_id='audit-'+case,aspect_ratio='16:9',resolution='1080p',duration=7)
   else:payload=main.OnlineImageRequest(provider_id='audit',model='gpt-image-2',prompt='OFFLINE exact prompt',request_id='audit-'+case,size='1536x1024',aspect_ratio='3:2',resolution='2K',**changes)
   with contextlib.redirect_stdout(io.StringIO()):result=await asyncio.wait_for(main.canvas_video(payload) if scenario['video'] else main.online_image(payload),20)
   outcome={'status':200,**local_results(result)}
  except Exception as e:outcome={'status':getattr(e,'status_code',500),'exception':type(e).__name__,'detail':str(getattr(e,'detail',''))[:500]}
  outcome['history_delta']=len(json.loads(pathlib.Path(main.HISTORY_FILE).read_text()) if pathlib.Path(main.HISTORY_FILE).exists() else [])-before
  if version=='public':
   with main.INSTANCE_MODELS.db() as db:jobs=[json.loads(x['data']) for x in db.execute('SELECT data FROM jobs')]
   outcome['latest_ledger']=[{k:j.get(k) for k in ['status','purpose','submission_uncertain','outstanding','upstream_status','local_result_status']} for j in jobs[-1:]]
  results.append({'case':case,'requests':list(captures),'clients':list(client_options),'outcome':outcome})
 report_handle.write(json.dumps(results,ensure_ascii=False,indent=2));report_handle.close();print(version,'workflow cases',len(results))
asyncio.run(run())
