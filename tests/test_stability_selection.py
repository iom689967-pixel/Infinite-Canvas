import json,pathlib,subprocess,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class SelectionContractTests(unittest.TestCase):
    def test_exact_identity_and_catalog_failure(self):
        script='''const {PersonalModelSelection:s}=require('./static/js/personal-model-selection.js');
const p=[{id:'a',enabled:true,chat_models:['same'],capabilities:{llm:{same:{executable:true}}}},{id:'b',enabled:false,chat_models:['same']}];
const cases=[['a','same',''],['missing','same',''],['b','same',''],['a','gone',''],['a','same','failed']];
console.log(JSON.stringify({reasons:cases.map(([id,m,e])=>s.reason(p,id,m,'llm',e)),kept:s.preserve('old','new'),initial:s.preserve('','new')}));'''
        r=subprocess.run(['node','-e',script],cwd=ROOT,capture_output=True,text=True,check=True);d=json.loads(r.stdout)
        self.assertEqual(d['reasons'][0],'');self.assertTrue(all(d['reasons'][i] for i in range(1,5)));self.assertEqual(d['kept'],'old');self.assertEqual(d['initial'],'new')
    def test_real_selectors_and_execution_preserve_invalid_input(self):
        import test_smart_generation_polling_incremental_ui as extract
        for page in ['canvas','smart-canvas']:
            extract.SMART=(ROOT/'static/js'/f'{page}.js').read_text()
            names=['resolveChatProviderId','providerChatModels','resolveChatModel','assertPersonalSelection']
            if page=='canvas':names+=['uniqueModels','callCanvasLLM']
            else:names+=['runPromptLLMNode']
            source='\n'.join(extract.function_source(n) for n in names)
            setup='''const window=require('./static/js/personal-model-selection.js');const personalApiInstance=true;
const document={getElementById:()=>({})};let providerConfigError='';
const apiProviders=[{id:'first',enabled:true,chat_models:['same'],capabilities:{llm:{same:{executable:true}}}}];
const chatApiProviders=()=>apiProviders,allChatModels=()=>['same'];
let count=0;const fetch=()=>{count++;throw Error('network forbidden')},cascadeFetch=fetch;
const nodes=[{id:'n',type:'smart-prompt',llmProvider:'deleted',llmModel:'same',model:'same',text:'keep',images:['keep-ref']}];
const activePromptLLMRuns=new Map(),promptNodeLLMInputText=n=>n.text,promptLLMRequestId=()=>'fixture';
const render=()=>{},toast=()=>{},tr=x=>x,isPromptLLMAbortError=()=>false;
'''
            call="try{await callCanvasLLM(nodes[0],'keep')}catch(e){}" if page=='canvas' else "await runPromptLLMNode('n');"
            script=setup+source+"\n(async()=>{"+call+"console.log(JSON.stringify({count,node:nodes[0],id:resolveChatProviderId('deleted'),model:resolveChatModel('gone','first')}))})()"
            d=json.loads(subprocess.run(['node','-e',script],cwd=ROOT,text=True,capture_output=True,check=True).stdout)
            self.assertEqual(d['count'],0);self.assertEqual(d['id'],'deleted');self.assertEqual(d['model'],'gone');self.assertEqual(d['node']['text'],'keep');self.assertEqual(d['node']['images'],['keep-ref'])
