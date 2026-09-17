"""Run unchanged selector functions from both snapshots. Only DOM/config fixtures are synthetic."""
import pathlib,sys,json,subprocess,os
ROOT=pathlib.Path(os.environ['STABILITY_REPLAY_ROOT']).resolve();sys.path.insert(0,str(ROOT/'snapshots/public/tests'));import test_smart_generation_polling_incremental_ui as extraction
out=[]
for version in ['owner','public']:
 for canvas in ['canvas','smart-canvas']:
  source=(ROOT/'snapshots'/version/'static/js'/f'{canvas}.js').read_text();extraction.SMART=source
  names=['chatApiProviders','resolveChatProviderId','providerChatModels','resolveChatModel','providerImageModels']
  if canvas=='canvas':names+=['uniqueModels','defaultApiProviders','imageApiProviders','allChatModels','allImageModels','resolveImageProviderId'];names+=['providerPool'] if version=='public' else []
  else:names+=['imageProviders']
  funcs='\n'.join(extraction.function_source(n) for n in names)
  setup="""
let apiProviders=[{id:'custom-api',protocol:'openai',enabled:true,image_models:['gpt-image-2','gpt-image-2.5-flare','gpt-image-2.5-sunburst'],chat_models:['available-chat'],video_models:[]}];
const window={PersonalModelSelection:{preserve:(value,initial)=>value || initial || ''}};let providerConfigError='';
const personalApiInstance=PUBLIC, document={getElementById:()=>PUBLIC ? {}:null},managedProviderId='custom-api';
const imageModels=[],chatModels=[],videoModels=[],localChatModels=[],hasManagedChatModels=false,DEFAULT_VIDEO_MODELS=[];
""".replace('PUBLIC',str(version=='public').lower())
  if version=='public':setup='const window={};'+(ROOT/'snapshots/public/static/js/personal-model-selection.js').read_text().replace("typeof module==='object'?module.exports:window",'window')+setup.replace("const window={PersonalModelSelection:{preserve:(value,initial)=>value || initial || ''}};",'')
  calls="""
const provider=resolveChatProviderId('deleted-provider');const model=resolveChatModel('deleted-model',provider);
console.log(JSON.stringify({imageModels:providerImageModels('custom-api'),deletedProviderResolved:provider,deletedModelResolved:model,missingModelInExistingProvider:resolveChatModel('deleted-model','custom-api')}));
"""
  r=subprocess.run(['node','-e',setup+funcs+calls],text=True,capture_output=True)
  out.append({'version':version,'canvas':canvas,'result':json.loads(r.stdout) if r.returncode==0 else {'test_harness_error':r.stderr[:500]}})
(ROOT/'evidence/frontend-differential.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2))
