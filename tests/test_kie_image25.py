"""GPT Image 2.5 contracts through the existing adapter, cache, queue and UI code.

All network responses are httpx.MockTransport fixtures. No live credentials/calls.
"""
import asyncio
import contextlib
import gzip
import io
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image
from providers.kie import uploads
from providers.kie.client import KieClient
from providers.kie.models import (
    GPT_IMAGE_25_FLARE as FLARE, GPT_IMAGE_25_SUNBURST as SUNBURST,
    GPT_IMAGE_25_RATIOS, GPT_IMAGE_25_1K_ONLY_RATIOS, GPT_IMAGE_ROUTES,
    KIE_UI_MODELS, KieValidationError, build_create_payload, build_capability_schema,
)

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = (FLARE, SUNBURST)


def function(source, name):
    start = re.search(rf'^(?:async )?function {name}\(', source, re.M).start()
    return source[start:source.index('\n}', start) + 2]


def node_run(source):
    result = subprocess.run(['node', '-e', source], text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(result.stderr[-2000:])
    return json.loads(result.stdout)


class Image25PayloadTests(unittest.TestCase):
    def payload(self, model, refs):
        result, _ = build_create_payload(model, 'studio', refs, '1:1', '1K')
        self.assertEqual(result['model'], GPT_IMAGE_ROUTES[model]['image_to_image' if refs else 'text_to_image'])
        expected = {'prompt':'studio', 'aspect_ratio':'1:1', 'resolution':'1K', 'background':'auto'}
        if refs:
            expected['input_urls'] = refs
        self.assertEqual(result['input'], expected)

    def test_flare_text(self):
        self.payload(FLARE, [])

    def test_flare_edit(self):
        self.payload(FLARE, ['https://mock.test/person.png', 'https://mock.test/coat.png'])

    def test_sunburst_text(self):
        self.payload(SUNBURST, [])

    def test_sunburst_edit(self):
        self.payload(SUNBURST, ['https://mock.test/person.png', 'https://mock.test/coat.png'])

    def test_all_ratios_and_resolutions(self):
        for model in VARIANTS:
            for ratio in GPT_IMAGE_25_RATIOS:
                for resolution in ('1K', '2K', '4K'):
                    with self.subTest(model=model, ratio=ratio, resolution=resolution):
                        if ratio in GPT_IMAGE_25_1K_ONLY_RATIOS and resolution != '1K':
                            with self.assertRaises(KieValidationError):
                                build_create_payload(model, 'studio', [], ratio, resolution)
                        else:
                            payload, _ = build_create_payload(model, 'studio', [], ratio, resolution.lower())
                            self.assertEqual(payload['input']['resolution'], resolution)
                            self.assertEqual(payload['input']['aspect_ratio'], ratio)

    def test_reference_limit_order_and_internal_routes_hidden(self):
        refs = [f'https://mock.test/{i}.png' for i in range(16)]
        for model in VARIANTS:
            payload, _ = build_create_payload(model, 'studio', refs)
            self.assertEqual(payload['input']['input_urls'], refs)
            self.assertEqual(build_capability_schema(model)['reference_image_limit'], 16)
            with self.assertRaises(KieValidationError):
                build_create_payload(model, 'studio', refs + ['https://mock.test/extra.png'])
            for route in GPT_IMAGE_ROUTES[model].values():
                self.assertNotIn(route, KIE_UI_MODELS)
                with self.assertRaises(KieValidationError):
                    build_create_payload(route, 'studio')

    def test_background_extension_keeps_auto_default(self):
        for model in VARIANTS:
            for background in ('auto', 'opaque', 'transparent'):
                result, _ = build_create_payload(model, 'studio', background=background)
                self.assertEqual(result['input']['background'], background)
            with self.assertRaises(KieValidationError):
                build_create_payload(model, 'studio', background='invalid')

    def test_model_whitespace_keeps_the_selected_route(self):
        for model in VARIANTS:
            payload, meta = build_create_payload(' ' + model + ' ', 'studio')
            self.assertEqual(payload['model'], GPT_IMAGE_ROUTES[model]['text_to_image'])
            self.assertEqual(meta['ui_model'], model)


class Image25PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_rejects_before_upload_and_queue(self):
        import main
        for model in VARIANTS:
            invalid = main.OnlineImageRequest(provider_id='kie', model=model, prompt='studio', aspect_ratio='27:16', resolution='4K', reference_images=[{'url':'/assets/mock.png'}])
            with patch.object(main, 'prepare_kie_references', AsyncMock()) as prepare, patch.object(main, 'KieClient') as client:
                for method in (main.create_canvas_image_task, main.build_online_image_result):
                    with self.assertRaises(main.HTTPException) as err:
                        await method(invalid)
                    self.assertEqual(err.exception.status_code, 400)
                prepare.assert_not_awaited()
                client.assert_not_called()

    async def test_real_adapter_upload_cache_poll_download_history_and_queue(self):
        import main
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = io.BytesIO()
            Image.new('RGB', (37, 29), '#123456').save(image, format='PNG')
            png = image.getvalue()
            source = root/'ref.png'; source.write_bytes(png)
            output = root/'output'; output.mkdir()
            cache = uploads.KieReferenceUploadCache(root/'reference-cache.json')
            requests = []; creates = []; current_id = ''; query_counts = {}

            def handler(request):
                nonlocal current_id
                requests.append((request.method, request.url.host, request.url.path))
                if request.url.path == '/api/file-stream-upload':
                    body = json.dumps({'success':True, 'data':{'downloadUrl':'https://mock.test/ref.png'}}).encode()
                    return httpx.Response(200, headers={'content-type':'application/json', 'content-encoding':'gzip'}, content=gzip.compress(body))
                if request.url.path == '/api/v1/jobs/createTask':
                    creates.append(json.loads(request.content))
                    current_id = f'mock-upstream-{len(creates)}'
                    return httpx.Response(200, json={'code':200, 'data':{'taskId':current_id}})
                if request.url.path == '/api/v1/jobs/recordInfo':
                    self.assertEqual(request.url.params['taskId'], current_id)
                    i = query_counts.get(current_id, 0); query_counts[current_id] = i+1
                    state = ('waiting','queuing','generating','success')[min(i,3)]
                    return httpx.Response(200, json={'code':200, 'data':{'state':state, 'resultJson':json.dumps({'resultUrls':['https://mock.test/result.png']})}})
                if request.url.host == 'mock.test':
                    return httpx.Response(200, headers={'content-type':'image/png'}, content=png)
                raise AssertionError('Unexpected upstream endpoint')

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as ref_client:
                async def prepare(api_key, refs, **kwargs):
                    return await uploads.prepare_kie_references(api_key, refs, resolve_local_path=lambda url: str(source), max_bytes=kwargs['max_bytes'], cache=cache, http_client=ref_client)
                client_factory = httpx.AsyncClient
                states = []
                def on_status(status, _raw): states.append(status)
                with contextlib.redirect_stdout(io.StringIO()), patch.object(main, 'provider_env_key_value', return_value='mock-key'), patch.object(main, 'prepare_kie_references', side_effect=prepare), patch.object(main.httpx, 'AsyncClient', side_effect=lambda **kwargs: client_factory(transport=transport, **kwargs)), patch.object(main, 'output_path_for', side_effect=lambda name, category: str(output/name)), patch.object(main, 'output_url_for', side_effect=lambda name, category: '/assets/output/'+name), patch.object(main, 'output_file_from_url', side_effect=lambda url: str(output/Path(url).name)), patch.object(main, 'HISTORY_FILE', str(root/'history.json')), patch.object(main, 'GLOBAL_LOOP', None), patch.dict('os.environ', {'KIE_POLL_INITIAL_INTERVAL':'0.05','KIE_POLL_MAX_INTERVAL':'0.05'}):
                    # Same reference across model switches must upload once, preserving source-fingerprint reuse.
                    for model in VARIANTS:
                        payload = main.OnlineImageRequest(provider_id='kie', model=model, prompt='studio', aspect_ratio='9:8', resolution='1K', reference_images=[{'url':'/assets/ref.png'}])
                        submitted = await main.create_canvas_image_task(payload)
                        local_id = submitted['task_id']
                        runner = main.CANVAS_TASK_RUNNERS[local_id]
                        await runner
                        task = await main.get_canvas_image_task(local_id)
                        self.assertEqual(task['status'], 'succeeded')
                        self.assertEqual(task['model'], model)
                        result = task['result']
                        self.assertTrue(result['images'][0].startswith('/assets/output/'))
                        self.assertEqual(result['image_items'][0]['natural_w'], 37)
                        self.assertEqual(result['image_items'][0]['natural_h'], 29)
                        self.assertEqual(result['params']['model'], model)
                        self.assertTrue((output/Path(result['images'][0]).name).exists())
                        main.CANVAS_TASKS.pop(local_id)
                    # Existing cancellation still prevents createTask.
                    cancelled = asyncio.Event(); cancelled.set()
                    with self.assertRaises(main.HTTPException) as error:
                        await main.generate_kie_provider_image('studio', FLARE, [], {'id':'kie'}, cancel_event=cancelled, status_callback=on_status)
                    self.assertEqual(error.exception.status_code, 499)
                history = json.loads((root/'history.json').read_text())
                self.assertEqual([item['model'] for item in history], [SUNBURST, FLARE])
                self.assertEqual(len(creates), 2)  # Mock requests only, one per generation.
                self.assertEqual(sum(path == '/api/file-stream-upload' for _,_,path in requests), 1)
                self.assertEqual(query_counts, {'mock-upstream-1':4, 'mock-upstream-2':4})
                self.assertEqual(creates[0]['input']['input_urls'], creates[1]['input']['input_urls'])
                self.assertEqual(len(list(output.iterdir())), 2)

    async def test_create_transport_error_is_not_retried(self):
        calls = []
        def handler(request):
            calls.append(request.method)
            raise httpx.ReadTimeout('mock', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(Exception):
                await KieClient('mock', http_client=client).create_task(build_create_payload(FLARE, 'studio')[0])
        self.assertEqual(calls, ['POST'])

    async def test_download_failure_never_returns_temporary_url(self):
        import main
        def handler(request):
            raise httpx.ReadTimeout('mock', request=request)
        factory = httpx.AsyncClient
        with tempfile.TemporaryDirectory() as temp, patch.object(main, 'output_path_for', return_value=str(Path(temp)/'out.png')), patch.object(main.httpx, 'AsyncClient', side_effect=lambda **kwargs: factory(transport=httpx.MockTransport(handler), **kwargs)):
            with self.assertRaises(main.HTTPException) as error:
                await main.save_ai_image_to_output({'type':'url','value':'https://mock.test/result.png'}, require_local=True)
            self.assertEqual(error.exception.status_code, 502)
            self.assertNotIn('https://', error.exception.detail)
            self.assertEqual(list(Path(temp).iterdir()), [])

    async def test_non_image_200_is_not_saved_as_a_completed_image(self):
        import main
        factory = httpx.AsyncClient
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text='<html>not an image</html>'))
        with tempfile.TemporaryDirectory() as temp, patch.object(main, 'output_path_for', return_value=str(Path(temp)/'out.png')), patch.object(main.httpx, 'AsyncClient', side_effect=lambda **kwargs: factory(transport=transport, **kwargs)):
            with self.assertRaises(main.HTTPException) as error:
                await main.save_ai_image_to_output({'type':'url','value':'https://mock.test/result.png'}, require_local=True)
            self.assertEqual(error.exception.status_code, 502)
            self.assertEqual(list(Path(temp).iterdir()), [])


class Image25FrontendTests(unittest.TestCase):
    def test_shared_controls_disable_invalid_combinations_and_deduplicate_fetch(self):
        source = (ROOT/'static/js/kie-image-params.js').read_text()
        schema = build_capability_schema(FLARE)
        result = node_run(source + '\nconst schema='+json.dumps(schema)+';'+r'''
let gets=0;
globalThis.fetch=async()=>{gets++; return {ok:true,json:async()=>schema};};
globalThis.document={createElement:()=>({})};
const select=()=>({replaceChildren(...options){this.options=options;}});
(async()=>{
 const [a,b]=await Promise.all([KieImageParams.load('kie',schema.model),KieImageParams.load('kie',schema.model)]);
 const controls={aspect_ratio:select(),resolution:select()};
 KieImageParams.syncSelects(schema,{aspect_ratio:'27:16',resolution:'1K'},controls);
 const disabled=controls.resolution.options.filter(o=>o.disabled).map(o=>o.value);
 let rejected=0;
 for(const aspect_ratio of ['27:16','16:27','9:8','8:9']) for(const resolution of ['2K','4K']){
  try{KieImageParams.validate(schema,{aspect_ratio,resolution});}catch{rejected++;}
 }
 console.log(JSON.stringify({gets,same:a===b,disabled,rejected}));
})();''')
        self.assertEqual(result, {'gets':1,'same':True,'disabled':['2K','4K'],'rejected':8})

    def test_smart_save_reload_clone_and_generation_keep_model_and_refs(self):
        smart = (ROOT/'static/js/smart-canvas.js').read_text()
        helper = (ROOT/'static/js/kie-image-params.js').read_text()
        selected_functions = '\n'.join(function(smart, name) for name in ('cloneSmartSettings','settingsForStorage','clearSmartNodeTransientRunState','cloneSmartNode','kieCapabilityField','kieFieldValues','normalizeKieSettings','runApiGeneration',
            # runApiGeneration 在提交前检查平台是否仍可用（A1）
            'isUnsetProviderId','resolveSmartProviderFromList','smartProviderProblem','imageProviders'))
        for model in VARIANTS:
            with self.subTest(model=model):
                source = helper+'\nconst schema='+json.dumps(build_capability_schema(model))+';\n'+selected_functions+r'''
const ModelPromptLimits={validate:async()=>{}};
const LEGACY_DEFAULT_PROVIDER_IDS=new Set(['comfly']);
const apiProviders=[{id:'kie',enabled:true,image_models:[schema.model]}];
function tr(key){return key;}
function trf(key){return key;}
function uid(){return 'clone';}
function isKieProviderId(id){return id==='kie';}
function currentKieCapability(){return null;}
let loads=0;
async function ensureKieCapability(){loads++;return schema;}
function sizeForRun(){return '1024x1024';}
function imageRefsOnly(refs){return refs;}
const requests=[];
async function fetch(url,init){requests.push(JSON.parse(init.body));return {ok:true,json:async()=>({task_id:'mock'})};}
const API_RATIO_VALUES={};
const settings={provider_id:'kie',model:schema.model,aspectRatio:'27:16',resolution:'1K',count:1};
(async()=>{
 const original={id:'original',type:'smart-image-generation',runSettings:settingsForStorage(settings)};
 const reloaded=JSON.parse(JSON.stringify(original));
 const copy=cloneSmartNode(reloaded);
 const refs=Array.from({length:16},(_,i)=>({url:'/assets/ref'+i+'.png'}));
 await runApiGeneration('studio',refs,copy.runSettings);
 await runApiGeneration('edit',[],reloaded.runSettings);
 console.log(JSON.stringify({model:copy.runSettings.model,original:reloaded.runSettings.model,loads,requests}));
})();'''
                result=node_run(source)
                self.assertEqual(result['model'], model)
                self.assertEqual(result['original'], model)
                self.assertEqual(result['loads'], 2)
                self.assertEqual(len(result['requests'][0]['reference_images']),16)
                self.assertEqual(result['requests'][0]['reference_images'][15]['url'],'/assets/ref15.png')
                self.assertEqual(result['requests'][1]['model'],model)
                self.assertEqual(result['requests'][1]['reference_images'],[])

    def test_classic_preflight_preserves_model_and_rejects_invalid_choice(self):
        canvas=(ROOT/'static/js/canvas.js').read_text()
        source=(ROOT/'static/js/kie-image-params.js').read_text()
        source+='\nconst schema='+json.dumps(build_capability_schema(SUNBURST))+';\n'
        source+='\n'.join(function(canvas,name) for name in ('isCanvasKieNode','canvasKieValues','canvasKieRequestFields'))
        result=node_run(source+r'''
const apiProviders=[{id:'kie',protocol:'kie'}], API_RATIO_VALUES={square:'1:1'};
function resolveImageModel(model){return model;}
let gets=0;
async function fetch(){gets++;return {ok:true,json:async()=>schema};}
(async()=>{
 const node={apiProvider:'kie',model:schema.model,aspectRatio:'16:27',resolution:'1k'};
 const fields=await canvasKieRequestFields(node,16);
 let rejected=false; node.resolution='4k';
 try{await canvasKieRequestFields(node,16);}catch{rejected=true;}
 console.log(JSON.stringify({fields,rejected,gets,model:node.model}));
})();''')
        self.assertEqual(result['fields']['aspect_ratio'],'16:27')
        self.assertEqual(result['fields']['resolution'],'1K')
        self.assertTrue(result['rejected'])
        self.assertEqual(result['model'],SUNBURST)

    def test_all_entrypoints_include_controls_before_page_script(self):
        for page in ('canvas','smart-canvas','online'):
            html=(ROOT/f'static/{page}.html').read_text()
            self.assertIn('kie-image-params.js?v=',html)
            if page != 'online':
                self.assertLess(html.index('kie-image-params.js'),html.index(f'/js/{page}.js'))
        settings=(ROOT/'static/js/api-settings.js').read_text()
        for model in VARIANTS:
            self.assertIn(model,settings)
        online=(ROOT/'static/online.html').read_text()
        self.assertIn('KieImageParams.validate(schema, input, reqBody.reference_images.length)',online)
        # Inline code is not covered by node --check static/js/*.js.
        scripts=re.findall(r'<script>(.*?)</script>',online,re.S)
        result=subprocess.run(['node','--check'],input='\n'.join(scripts),text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
