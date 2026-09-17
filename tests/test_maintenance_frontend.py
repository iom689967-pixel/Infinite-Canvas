"""Execute actual UI functions with a mock rejection; never replay or lose drafts."""
from pathlib import Path
import unittest
from unittest.mock import patch
import test_smart_generation_polling_incremental_ui as js

ROOT = Path(__file__).resolve().parents[1]


class MaintenanceFrontendTests(unittest.TestCase):
    def test_classic_chat_restores_exact_draft_and_does_not_append_rejected_user_message(self):
        source=(ROOT/'static/js/canvas.js').read_text()
        with patch.object(js,'SMART',source):
            function=js.function_source('runLLMChat')
        result=js.run_node("""
let calls=0;const nodes=[{id:'n',chatInput:'  原始输入\n ',messages:[{role:'assistant',content:'prior'}]}];
const refreshNodes=()=>{},scheduleSave=()=>{},alert=()=>{};
const callCanvasLLM=async()=>{calls++;throw Object.assign(new Error('维护'),{code:'maintenance'});};
""".replace("原始输入\n", "原始输入\\n")+function+"""
(async()=>{await runLLMChat('n');process.stdout.write(JSON.stringify({calls,node:nodes[0]}));})();
""")
        self.assertEqual(result['calls'],1);self.assertEqual(result['node']['chatInput'],'  原始输入\n ')
        self.assertEqual(result['node']['messages'],[{'role':'assistant','content':'prior'}])
        self.assertFalse(result['node']['running'])

    def test_chat_composer_and_reference_draft_are_restored_without_resubmission(self):
        source=(ROOT/'static/gpt-chat.html').read_text()
        with patch.object(js,'SMART',source):
            function=js.function_source('sendMessage')
        result=js.run_node("""
const input={value:'  原输入  '},btn={disabled:false};let refs=[{url:'/assets/owned.png'}],calls=0;
const document={getElementById:id=>id==='messageInput'?input:id==='sendBtn'?btn:{}};
const personalApiInstance=false,mode='chat';let currentConversation={id:'c',messages:[]};
const chatSizeFromPrompt=()=>'',renderRefs=()=>{},autoGrow=()=>{},scrollBottom=()=>{},
chatResolutionForRequest=()=>'',currentChatAspectRatio=()=>'',renderMessages=()=>{},tr=x=>x;
const addMessageBubble=()=>({bubble:{classList:{add:()=>{}}}});
const streamChatMessage=async()=>{calls++;throw Object.assign(new Error('维护'),{code:'maintenance'});};
"""+function+"""
(async()=>{await sendMessage();process.stdout.write(JSON.stringify({calls,draft:input.value,refs,disabled:btn.disabled}));})();
""")
        self.assertEqual(result,{'calls':1,'draft':'  原输入  ','refs':[{'url':'/assets/owned.png'}],'disabled':False})

    def test_shared_transport_503_is_explicit_and_makes_one_request(self):
        source=(ROOT/'static/js/instance-session.js').read_text()
        result=js.run_node("""
let calls=0;const identity={storage_namespace:'fake',capabilities:{}};
const document={getElementById:()=>({textContent:JSON.stringify(identity)}),documentElement:{}};
const location={href:'http://localhost/workspace',origin:'http://localhost'};
const window={fetch:async()=>{calls++;return new Response(JSON.stringify({detail:{code:'maintenance'}}),
{status:503,headers:{'X-Mio-Maintenance':'1'}})},WorkspaceStartup:{ready:Promise.resolve({...identity,csrf:'fake-csrf'})},
addEventListener:()=>{}};window.parent=window;window.top=window;
const setInterval=()=>{};
"""+source+"""
(async()=>{let error;try{await window.fetch('/api/canvas-llm',{method:'POST',body:'{}'});}catch(e){error={code:e.code,message:e.message};}
process.stdout.write(JSON.stringify({calls,error}));})();
""")
        self.assertEqual(result['calls'],1);self.assertEqual(result['error']['code'],'maintenance')
        self.assertEqual(result['error']['message'],'工作区维护中，暂时不能提交新任务，请稍后再试。')

    def test_sealed_bootstrap_does_not_logout_or_hide_existing_draft(self):
        source=(ROOT/'static/js/instance-session.js').read_text()
        result=js.run_node("""
let calls=0,redirects=0;const identity={storage_namespace:'fake',capabilities:{}};
const document={getElementById:()=>({textContent:JSON.stringify(identity)}),documentElement:{style:{visibility:'visible'}}};
const location={href:'http://localhost/workspace',origin:'http://localhost'};
const window={fetch:async()=>{calls++;return new Response('{}',{status:503,headers:{'X-Mio-Maintenance':'1'}})},
addEventListener:()=>{},location:{replace:()=>{redirects++;}}};window.parent=window;window.top=window;
const setInterval=()=>{};
"""+source+"""
(async()=>{let code;try{await window.InstanceSession.ready;}catch(e){code=e.code;}
process.stdout.write(JSON.stringify({calls,redirects,code,visibility:document.documentElement.style.visibility}));})();
""")
        self.assertEqual(result,{'calls':1,'redirects':0,'code':'maintenance','visibility':'visible'})
