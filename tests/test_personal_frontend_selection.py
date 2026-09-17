"""Run real selector/refresh functions: saved IDs must never cause silent billing changes."""
import unittest
import json
from pathlib import Path
from unittest.mock import patch
import test_smart_generation_polling_incremental_ui as js
from instance_model_policy import ModelPolicy

class PersonalFrontendSelectionTests(unittest.TestCase):
    def test_classic_preserves_missing_exact_ids_and_never_invents_owner_model(self):
        source=(Path(__file__).resolve().parents[1]/'static/js/canvas.js').read_text()
        names=('resolveImageProviderId','resolveVideoProviderId','resolveChatProviderId',
               'providerImageModels','providerVideoModels','providerChatModels',
               'sanitizeImageNodeProviderModel','sanitizeVideoNodeProviderModel',
               'resolveChatModel','capabilityModelOption')
        with patch.object(js,'SMART',source):functions='\n'.join(js.function_source(n) for n in names)
        policy=object.__new__(ModelPolicy)
        policy.providers={'shared':dict(id='shared',protocol='gemini',models={'approved-text':{'max_output_tokens':64}})}
        shared=policy.catalog()['api_providers'][0]
        self.assertTrue(shared['capabilities']['llm']['approved-text']['executable'])
        self.assertEqual(shared['image_models'],[])
        value=js.run_node('const shared='+json.dumps(shared)+';\n'+"""
const personalApiInstance=true,providerConfigError='';
const apiProviders=[{id:'own',image_models:['available'],video_models:['available'],chat_models:['available']},shared];
const imageApiProviders=()=>apiProviders,videoApiProviders=()=>apiProviders,chatApiProviders=()=>apiProviders;
const uniqueModels=x=>[...new Set(x)],allChatModels=()=>[],escapeHtml=x=>String(x);
"""+functions+"""
const image={type:'generator',apiProvider:'own',model:'missing-exact-id'};
const video={type:'video',apiProvider:'deleted-provider',model:'missing-video-id'};
sanitizeImageNodeProviderModel(image);sanitizeVideoNodeProviderModel(video);
const missing=capabilityModelOption('missing-exact-id','missing-exact-id','own','image');
process.stdout.write(JSON.stringify({image,video,chatProvider:resolveChatProviderId('deleted-provider'),emptyChat:resolveChatModel('','deleted-provider'),defaultProvider:resolveImageProviderId('comfly'),missing,sharedOption:capabilityModelOption('approved-text','approved-text','shared','llm')}));
""")
        self.assertEqual(value['image']['model'],'missing-exact-id')
        self.assertEqual(value['video']['apiProvider'],'deleted-provider')
        self.assertEqual(value['video']['model'],'missing-video-id')
        self.assertEqual(value['chatProvider'],'deleted-provider')
        self.assertEqual(value['emptyChat'],'')
        self.assertEqual(value['defaultProvider'],'own')
        self.assertIn('disabled',value['missing']);self.assertIn('尚未配置',value['missing'])
        self.assertNotIn('disabled',value['sharedOption'])

    def test_smart_refresh_selected_prompt_has_no_undefined_call_or_model_rewrite(self):
        functions='\n'.join(js.function_source(n) for n in ('sanitizeSmartApiSelection','refreshSmartConfigFromSettings'))
        value=js.run_node("""
const personalApiInstance=true,calls=[],settings={model:'missing-exact-id',resolution:'auto'};
const loadConfig=async()=>calls.push('load'),renderDynamicParams=()=>calls.push('params');
const selectedNode=()=>({type:'smart-prompt'}),render=()=>calls.push('render');
"""+functions+"""
(async()=>{sanitizeSmartApiSelection(settings);await refreshSmartConfigFromSettings();process.stdout.write(JSON.stringify({calls,settings}));})().catch(e=>{console.error(e);process.exit(1)});
""")
        self.assertEqual(value['calls'],['load','params','render'])
        self.assertEqual(value['settings'],{'model':'missing-exact-id','resolution':'auto'})
