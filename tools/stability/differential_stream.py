"""Execute original streaming routes with identical chunked HTTP fixtures."""
import pathlib
source=pathlib.Path(__file__).with_name('differential_llm.py').read_text().split("ok={'choices'")[0].replace("('llm-'+version+'.json')","('stream-'+version+'.json')")
exec(compile(source,__file__,'exec'))
from starlette.requests import Request
class Bytes(httpx.AsyncByteStream):
 async def __aiter__(self):
  b=fixture['body']
  for i in range(0,len(b),7):yield b[i:i+7]
  if fixture.get('drop'):raise httpx.RemoteProtocolError('offline stream cut')
async def run():
 results=[];chunk=b'data: {"choices":[{"delta":{"content":"OFFLINE streamed text content"}}]}\n\n'
 cases={'sse_done':{'body':chunk+b'data: [DONE]\n\n'},'sse_no_done':{'body':chunk},'sse_business_error':{'body':b'data: {"error":{"message":"offline error"}}\n\ndata: [DONE]\n\n'},'sse_cut':{'body':chunk,'drop':True},'sse_cancel':{'body':chunk*4+b'data: [DONE]\n\n'},'json_success':{'body':b'{"choices":[{"message":{"content":"OFFLINE JSON text"}}]}'},'http503':{'body':b'{"error":{"message":"offline unavailable"}}','status':503}}
 for case,config in cases.items():
  captures.clear();client_options.clear();fixture.clear();fixture.update(config);fixture['headers']={'content-type':'application/json' if case in {'json_success','http503'} else 'text/event-stream'}
  payload=main.ChatRequest(provider='audit',model='gpt-6',message='反推提示词',system_prompt='exact system',reference_images=[{'url':refs[0]}]);req=Request({'type':'http','headers':[],'method':'POST','path':'/api/chat/stream','client':('127.0.0.1',1)})
  emitted=[];error=None;conversation_id=None
  try:
   with contextlib.redirect_stdout(io.StringIO()):
    response=await main.chat_stream(payload,req,'audit-user')
    async for s in response.body_iterator:
     emitted.append(s)
     if case=='sse_cancel' and any('"type": "delta"' in e for e in emitted):await response.body_iterator.aclose();break
  except BaseException as e:error=type(e).__name__
  events=[]
  for s in emitted:
   for line in s.splitlines():
    if line.startswith('data: '):
     j=json.loads(line[6:]);conversation_id=(j.get('conversation') or {}).get('id') or conversation_id;events.append({k:j[k] for k in ['type','delta','detail'] if k in j})
  saved=main.load_conversation('audit-user',conversation_id) if conversation_id else {}
  results.append({'case':case,'requests':fingerprint(captures),'clients':list(client_options),'events':events,'error':error,'saved_roles':[m['role'] for m in saved.get('messages',[])],'saved_texts':[m.get('content') for m in saved.get('messages',[])],'llm_active':getattr(getattr(main,'INSTANCE_MODELS',None),'llm_active',None)})
 report_handle.write(json.dumps(results,ensure_ascii=False,indent=2));report_handle.close();print(version,len(results),'stream cases completed')
asyncio.run(run())
