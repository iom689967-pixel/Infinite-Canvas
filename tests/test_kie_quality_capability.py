"""The real browser request builders, with only external HTTP replaced by fixtures."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_smart_generation_polling_incremental_ui as extracted
from provider_capabilities import resolve


ROOT = Path(__file__).resolve().parents[1]
KIE_MODELS = ('gpt-image-2', 'gpt-image-2.5-flare', 'gpt-image-2.5-sunburst', 'nano-banana-pro')


def functions(path, *names):
    with patch.object(extracted, 'SMART', (ROOT / path).read_text(encoding='utf-8')):
        return '\n'.join(extracted.function_source(name) for name in names)


def providers():
    entries = [dict(id='quality-model', protocol='openai', image_models=['gpt-image-2'],
                    capabilities={'image': {'gpt-image-2': resolve(
                        dict(protocol='openai', image_models=['gpt-image-2']), 'gpt-image-2', 'image')}})]
    entries.append(dict(id='personal-kie', protocol='kie', image_models=list(KIE_MODELS),
                        capabilities={'image': {model: resolve(
                            dict(protocol='kie', image_models=list(KIE_MODELS)), model, 'image')
                            for model in KIE_MODELS}},
                        model_limits={model: {'max_images': 2} for model in KIE_MODELS}))
    return entries


class KieQualityCapabilityTests(unittest.TestCase):
    def test_smart_real_submit_builder_switches_models_without_mutating_saved_quality(self):
        source = functions('static/js/smart-canvas.js', 'isKieProviderId', 'runApiGeneration')
        script = '''
const personalApiInstance=true,providerConfigError='',window=require('./static/js/personal-model-selection.js');
const apiProviders=PROVIDERS,settings={},SMART_REFERENCE_IMAGE_MAX=8,API_RATIO_VALUES={};
const crypto={randomUUID:()=> 'fixture-nonce'},tr=x=>x;
const currentKieCapability=()=>({reference_image_limit:2}),ensureKieCapability=async()=>({reference_image_limit:2});
const normalizeKieSettings=()=>{},validateKieSettings=()=>{},kieCapabilityField=()=>null;
const imageRefsOnly=x=>x,sizeForRun=()=> '1024x1024';
const calls=[];const fetch=async (url,options)=>{if(url!=='/api/canvas-image-tasks')throw Error(url);calls.push(JSON.parse(options.body));return {ok:true,json:async()=>({task_id:'mock-task'})}};
const assertPersonalSelection=(id,model)=>window.PersonalModelSelection.assertAvailable(apiProviders,id,model,'image');
''' + source + '''
(async()=>{const original={quality:'high',resolution:'1K',aspectRatio:'1:1',count:1};
await runApiGeneration('synthetic',[],{...original,provider_id:'quality-model',model:'gpt-image-2'});
for(const model of MODELS)await runApiGeneration('synthetic',[],{...original,provider_id:'personal-kie',model});
await runApiGeneration('synthetic',[],{...original,provider_id:'quality-model',model:'gpt-image-2'});
process.stdout.write(JSON.stringify({calls,original}));})().catch(e=>{console.error(e);process.exit(1)});
'''
        result = extracted.run_node('const PROVIDERS=' + json.dumps(providers()) + ';const MODELS=' + json.dumps(KIE_MODELS) + ';' + script)
        self.assertEqual(len(result['calls']), 6)
        self.assertEqual(result['calls'][0]['quality'], 'high')
        self.assertEqual(result['calls'][-1]['quality'], 'high')
        for model, payload in zip(KIE_MODELS, result['calls'][1:5]):
            self.assertEqual(payload['model'], model)
            self.assertNotIn('quality', payload)
        self.assertEqual(result['original']['quality'], 'high')

    def test_classic_real_generator_builder_omits_stale_quality_and_loras(self):
        source = functions('static/js/canvas.js', 'normalizedImageQuality', 'imageParameterSupported', 'runGenerator')
        script = '''
const personalApiInstance=true,window=require('./static/js/personal-model-selection.js'),apiProviders=PROVIDERS;
const nodes=[{id:'g',type:'generator',apiProvider:'quality-model',model:'gpt-image-2',quality:'high',ratio:'square',resolution:'1k',count:1,personalAdapterParameters:{loras:{old:0.8}}}];
const canvas={id:'fixture-canvas'},crypto={randomUUID:()=> 'fixture-nonce'},API_RATIO_VALUES={square:'1:1'},CANVAS_REFERENCE_IMAGE_MAX=8;
const calls=[],tr=x=>x,assertPersonalSelection=(id,model)=>window.PersonalModelSelection.assertAvailable(apiProviders,id,model,'image');
const cascadeTargetIdFromOptions=()=>'',materializeCanvasReferenceEdges=async()=>[],generatorSources=()=>[{prompt:'synthetic',refs:[]}];
const orderedSources=(_n,s)=>s,uniqueCanvasReferenceImages=x=>x,canvasReferenceLimitForNode=async()=>8;
const outputForNode=()=>null,runSnapshot=()=>({}),resolveImageProviderId=x=>x,resolveImageModel=x=>x,generatorSizeForRun=async()=> '1024x1024';
const setTimeout=()=>0,refreshRunNodes=()=>{},scheduleSave=()=>{},saveCanvas=async()=>{},nowMs=()=>0;
const createCanvasImageTask=async body=>{calls.push(JSON.parse(JSON.stringify(body)));return {task_id:'mock-task'}};
const waitCanvasImageTaskResult=async()=>({images:['/fixture-result.png']}),requestMetaFromResult=()=>({});
const mergeGeneratedOutputs=()=>{},addGenerationLog=()=>{},showErrorModal=message=>{throw Error(message)};
''' + source + '''
(async()=>{await runGenerator('g');nodes[0].apiProvider='personal-kie';
for(const model of MODELS){nodes[0].model=model;await runGenerator('g')}
nodes[0].apiProvider='quality-model';nodes[0].model='gpt-image-2';await runGenerator('g');
process.stdout.write(JSON.stringify({calls,node:nodes[0]}));})().catch(e=>{console.error(e);process.exit(1)});
'''
        result = extracted.run_node('const PROVIDERS=' + json.dumps(providers()) + ';const MODELS=' + json.dumps(KIE_MODELS) + ';' + script)
        self.assertEqual(len(result['calls']), 6)
        self.assertEqual(result['calls'][0]['quality'], 'high')
        self.assertEqual(result['calls'][-1]['quality'], 'high')
        for payload in result['calls'][1:5]:
            self.assertNotIn('quality', payload)
            self.assertNotIn('adapter_parameters', payload)
        self.assertEqual(result['node']['quality'], 'high')

    def test_online_real_submit_builder_filters_hidden_quality(self):
        source = functions('static/online.html', 'submitImage')
        script = '''
const personalApiInstance=true,window=require('./static/js/personal-model-selection.js'),apiProviders=PROVIDERS;
let provider='quality-model',selectedModel='gpt-image-2',quality='high',currentResult=null;
const outputCount=1,refs={},ratio='square',resolution='1k',customRatioWidth='',customRatioHeight='';
const elements=new Map(),element=id=>elements.get(id)||elements.set(id,{value:id==='promptInput'?'synthetic':'',classList:{add(){},remove(){}},disabled:false}).get(id);
const document={getElementById:element},tr=x=>x,currentSize=()=> '1024x1024',renderImageCard=()=>{},alert=message=>{throw Error(message)};
const QUALITY_PROTOCOLS=new Set(),providerProtocol=()=> 'api',calls=[];
const fetch=async(url,options)=>{if(url!=='/api/online-image')throw Error(url);calls.push(JSON.parse(options.body));return {ok:true,json:async()=>({images:['/fixture-result.png']})}};
''' + source + '''
(async()=>{await submitImage();provider='personal-kie';
for(const model of MODELS){selectedModel=model;await submitImage()}
provider='quality-model';selectedModel='gpt-image-2';await submitImage();
process.stdout.write(JSON.stringify({calls,quality}));})().catch(e=>{console.error(e);process.exit(1)});
'''
        result = extracted.run_node('const PROVIDERS=' + json.dumps(providers()) + ';const MODELS=' + json.dumps(KIE_MODELS) + ';' + script)
        self.assertEqual(len(result['calls']), 6)
        self.assertEqual(result['calls'][0]['quality'], 'high')
        self.assertEqual(result['calls'][-1]['quality'], 'high')
        for payload in result['calls'][1:5]:
            self.assertNotIn('quality', payload)
        self.assertEqual(result['quality'], 'high')

    def test_ui_capability_and_fastapi_error_shell(self):
        source = functions('static/js/smart-canvas.js', 'renderQualityControl', 'apiErrorMessage')
        script = '''
const personalApiInstance=true,window=require('./static/js/personal-model-selection.js'),apiProviders=PROVIDERS;
const settings={provider_id:'personal-kie',model:'gpt-image-2',quality:'high'};
const tr=x=>x,escapeHtml=x=>x;
''' + source + '''
const hidden=renderQualityControl();settings.provider_id='quality-model';const shown=renderQualityControl();
const detail='所选 Kie input 模板未实现 quality 字段契约，不会忽略所选质量';
process.stdout.write(JSON.stringify({hidden,shown,error:apiErrorMessage({detail}),wrapped:window.PersonalModelSelection.errorMessage(JSON.stringify({detail}))}));
'''
        result = extracted.run_node('const PROVIDERS=' + json.dumps(providers()) + ';' + script)
        self.assertEqual(result['hidden'], '')
        self.assertIn('quality-control', result['shown'])
        self.assertEqual(result['error'], '当前 Kie 模型不支持 Quality 参数。')
        self.assertEqual(result['wrapped'], result['error'])

    def test_backend_still_rejects_explicit_high_before_upstream(self):
        from test_personal_network_adapters import PersonalNetworkTests
        fixture = PersonalNetworkTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.save(protocol='kie', name='gpt-image-2')
        before = len(fixture.f.mock.calls)
        rejected = fixture.f.request('A', 'POST', '/api/online-image', json={
            'provider_id': 'same-personal-id', 'model': 'gpt-image-2',
            'prompt': 'synthetic', 'quality': 'high'})
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('quality 字段契约', rejected.text)
        self.assertEqual(len(fixture.f.mock.calls), before)


if __name__ == '__main__':
    unittest.main()
