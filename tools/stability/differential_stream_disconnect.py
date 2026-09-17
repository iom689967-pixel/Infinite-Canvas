import pathlib
s=pathlib.Path(__file__).with_name('differential_stream.py').read_text().split('async def run():')[0].replace("('stream-'+version+'.json')","('stream-disconnect-'+version+'.json')")
exec(compile(s,__file__,'exec'))
async def run():
 fixture.update(body=(b'data: {"choices":[{"delta":{"content":"OFFLINE streamed text long enough"}}]}\n\n')*8+b'data: [DONE]\n\n',headers={'content-type':'text/event-stream'})
 scope={'type':'http','asgi':{'spec_version':'2.0'},'headers':[],'method':'POST','path':'/api/chat/stream','client':('127.0.0.1',1)};req=Request(scope);event=asyncio.Event();emitted=[];received=False
 async def receive():
  nonlocal received
  if not received:received=True;return {'type':'http.request','body':b'','more_body':False}
  await event.wait();return {'type':'http.disconnect'}
 async def send(message):
  if message['type']=='http.response.body':
   body=message.get('body',b'').decode();emitted.append(body)
   if '"type": "delta"' in body:event.set()
  await asyncio.sleep(0)
 try:
  with contextlib.redirect_stdout(io.StringIO()):
   response=await main.chat_stream(main.ChatRequest(provider='audit',model='gpt-6',message='反推提示词'),req,'audit-user');await response(scope,receive,send)
  error=None
 except BaseException as e:error=type(e).__name__
 await asyncio.sleep(0)
 out={'version':version,'transport_calls':len(captures),'disconnect_delivered':event.is_set(),'error':error,'llm_active_after':getattr(getattr(main,'INSTANCE_MODELS',None),'llm_active',None),'has_done':any('"type": "done"' in s for s in emitted)}
 report_handle.write(json.dumps(out,indent=2));report_handle.close();print(json.dumps(out))
asyncio.run(run())
