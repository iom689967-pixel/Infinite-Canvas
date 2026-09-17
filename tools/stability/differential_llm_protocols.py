import pathlib
s=pathlib.Path(__file__).with_name('differential_llm.py').read_text().split("ok={'choices'")[0].replace("('llm-'+version+'.json')","('llm-protocols-'+version+'.json')")
exec(compile(s,__file__,'exec'))
async def run():
 results=[]
 for protocol,path in [('openai',''),('openai','/v1'),('gemini','/relay/v1beta'),('volcengine',''),('volcengine','/api/v3'),('runninghub','')]:
  provider.update(protocol=protocol,base_url='http://127.0.0.1:54321'+path)
  if version=='owner':main.save_api_providers([provider])
  else:
   stored.update(protocol=protocol,base_url=provider['base_url'],settings=dict(provider));cfg.write_text(json.dumps({'schema_version':1,'mode':'mock','max_concurrent':2,'providers':[],'personal_providers':[stored]}));main.INSTANCE_MODELS.policy=ModelPolicy(main.PATHS)
  captures.clear();client_options.clear();fixture.clear();fixture.update(body=json.dumps({'candidates':[{'content':{'parts':[{'text':'OFFLINE'}]}}]} if protocol=='gemini' else {'choices':[{'message':{'content':'OFFLINE'}}]}).encode())
  try:
   with contextlib.redirect_stdout(io.StringIO()):result=await main.canvas_llm(main.CanvasLLMRequest(provider='audit',model='gpt-6',message='反推提示词',images=refs[:1]))
   outcome={'status':200,'text':result['text']}
  except Exception as e:outcome={'status':getattr(e,'status_code',500),'exception':type(e).__name__}
  results.append({'protocol':protocol,'base_path':path,'requests':fingerprint(captures),'outcome':outcome})
 report_handle.write(json.dumps(results,ensure_ascii=False,indent=2));report_handle.close();print(version,'protocol cases',len(results))
asyncio.run(run())
