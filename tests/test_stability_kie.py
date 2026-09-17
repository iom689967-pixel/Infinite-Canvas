"""Independent D07 contract assertions; all fixtures synthetic and offline."""
import json,subprocess,unittest
from pathlib import Path
from providers.kie.models import build_create_payload,KieValidationError
from providers.prompt_limits import validate_model_prompt,ModelPromptLimitError
from provider_capabilities import resolve
ROOT=Path(__file__).resolve().parents[1]
class KieStabilityTests(unittest.TestCase):
    def test_25_exact_routes_and_input(self):
        for variant in ('flare','sunburst'):
            for refs in ([],['https://fixture.invalid/owned.png']):
                body,_=build_create_payload('gpt-image-2.5-'+variant,' keep ',refs,'27:16','1K',background='transparent',preserve_prompt=True)
                expected={'prompt':' keep ','aspect_ratio':'27:16','resolution':'1K','background':'transparent'}
                if refs:expected['input_urls']=refs
                self.assertEqual(body,{'model':'gpt-image-2-5-'+variant+('-image-to-image' if refs else '-text-to-image'),'input':expected})
        with self.assertRaises(KieValidationError):build_create_payload('gpt-image-2.5-flare','x',[],'27:16','4K')
    def test_prompt_limits_are_protocol_and_actual_model_scoped(self):
        for model in ('gpt-image-2','nano-banana-pro'):
            validate_model_prompt('kie',model,'🙂'*20000,preserve_prompt=True)
            with self.assertRaises(ModelPromptLimitError) as caught:validate_model_prompt('kie',model,'🙂'*20000+' ',preserve_prompt=True)
            self.assertEqual(caught.exception.details['current_length'],20001)
            self.assertNotIn('🙂',str(caught.exception))
        for protocol,model in [('openai','gpt-image-2'),('kie','gpt-image-2.5-flare'),('kie','new-model')]:
            validate_model_prompt(protocol,model,'x'*100000,preserve_prompt=True)
        body,_=build_create_payload('gpt-image-2','x'*30000,[],prompt_limit_model='new-model',preserve_prompt=True)
        self.assertEqual(len(body['input']['prompt']),30000)
    def test_capability_uses_effective_protocol_not_provider_name(self):
        for protocol,expected in [('openai',False),('kie',True)]:
            cap=resolve(dict(id='kie',protocol=protocol,image_models=['gpt-image-2']), 'gpt-image-2','image')
            self.assertEqual(bool(cap.get('prompt_limits')),expected)
        cap=resolve(dict(protocol='openai',model_protocols={'image|gpt-image-2':'kie'},image_models=['gpt-image-2']),'gpt-image-2','image')
        self.assertEqual(cap['prompt_limits']['text']['limit'],20000)
    def test_frontend_concurrent_wait_and_no_silent_downgrade(self):
        import test_smart_generation_polling_incremental_ui as extract
        extract.SMART=(ROOT/'static/js/smart-canvas.js').read_text()
        funcs='\n'.join(extract.function_source(n) for n in ['isKieProviderId','kieCapabilityKey','ensureKieCapability','kieCapabilityField','kieFieldValues','normalizeKieSettings','validateKieSettings'])
        script='''const assert=require('assert');const settings={provider_id:'Case',model:'gpt-image-2',resolution:'4K',aspectRatio:'9:21'};
const apiProviders=[{id:'Case',protocol:'kie'}],kieCapabilityCache=new Map(),kieCapabilityLoading=new Map();let kieCapabilityEpoch=0,calls=0,release;
const schema={fields:[{key:'resolution',default:'1K',options:[{value:'1K'},{value:'4K'}],aspect_ratio_exclusions:{'4K':['9:21']}},{key:'aspect_ratio',default:'auto',options:[{value:'auto'},{value:'9:21'}]}]};
const fetch=async()=>{calls++;await new Promise(r=>release=r);return {ok:true,json:async()=>schema}};
const scheduleDynamicParamsRefresh=()=>{},toast=()=>{};
'''+funcs+'''
(async()=>{const a=ensureKieCapability(),b=ensureKieCapability();assert.equal(calls,1);release();assert.strictEqual(await a,await b);assert.equal(settings.aspectRatio,'9:21');assert.equal(settings.resolution,'4K');assert.throws(()=>validateKieSettings(schema,settings));assert.notEqual(kieCapabilityKey('Case','m'),kieCapabilityKey('case','m'));console.log('ok')})().catch(e=>{console.error(e);process.exit(1)});
'''
        subprocess.run(['node','-e',script],cwd=ROOT,check=True,capture_output=True,text=True)
    def test_final_prompt_check_matches_python_unicode_count(self):
        script="""const {PersonalModelSelection:s}=require('./static/js/personal-model-selection.js');const p=[{id:'kie',capabilities:{image:{g:{prompt_limits:{text:{limit:20000}}}}}}];s.assertPrompt(p,'kie','g','🙂'.repeat(20000));try{s.assertPrompt(p,'kie','g','🙂'.repeat(20000)+' ');process.exit(2)}catch(e){if(e.code!=='model_prompt_too_long')throw e}console.log('ok')"""
        subprocess.run(['node','-e',script],cwd=ROOT,check=True,capture_output=True,text=True)

class KieOwnedExecutionTests(unittest.TestCase):
    def test_25_real_instance_template_and_protocol_specific_limit(self):
        import test_personal_network_adapters as net
        f=net.PersonalNetworkTests();f.setUp();self.addCleanup(f.doCleanups)
        f.save(protocol='kie',name='gpt-image-2.5-flare',base_url=f.f.mock.origin)
        result=f.f.ok('A','POST','/api/online-image',json=dict(provider_id='same-personal-id',model='gpt-image-2.5-flare',prompt='x'*20001,aspect_ratio='27:16',resolution='1K',quality='auto',adapter_parameters={'background':'transparent'},reference_images=[{'url':f.f.upload()}]))
        self.assertTrue(result['images'][0].startswith('/'))
        submits=[c[2] for c in f.f.mock.calls if c[0]=='create'];self.assertEqual(len(submits),1)
        body=submits[0];self.assertEqual(body['model'],'gpt-image-2-5-flare-image-to-image')
        self.assertEqual(set(body['input']),{'prompt','aspect_ratio','resolution','background','input_urls'})
        self.assertEqual(len(body['input']['prompt']),20001);self.assertEqual(body['input']['background'],'transparent')
        self.assertEqual(body['input']['aspect_ratio'],'27:16');self.assertEqual(len(body['input']['input_urls']),1)
