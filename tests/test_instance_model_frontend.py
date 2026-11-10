"""Execute the real frontend functions without contacting any model."""
import json
import unittest

from test_smart_generation_polling_incremental_ui import function_source, run_node, SMART


class ControlledModelFrontendTests(unittest.TestCase):
    def test_administrator_ids_and_one_batch_submission(self):
        # 运行真实的平台解析函数（A1）；提示词长度与 Kie 参数校验用记录调用的桩代替，并断言确实被调用。
        functions='\n'.join(function_source(n) for n in (
            'trf','imageProviders','isUnsetProviderId','resolveSmartProviderFromList','smartProviderProblem',
            'isKieProviderId','runApiGeneration'))
        result=run_node("""
const apiProviders=[{id:'atelier-images',protocol:'kie',image_models:['gpt-image-2'],model_limits:{'gpt-image-2':{max_images:2}}}];
const LEGACY_DEFAULT_PROVIDER_IDS=new Set(['comfly']);
const checks={prompt:0,kie:0};
const ModelPromptLimits={validate:async()=>{checks.prompt++}};
const KieImageParams={validate:()=>{checks.kie++}};
const settings={}; const calls=[];
const crypto={randomUUID:()=> 'random-nonce'};
const tr=key=>key;
const currentKieCapability=()=>({reference_image_limit:2});
const normalizeKieSettings=()=>{}; const imageRefsOnly=refs=>refs;
const kieCapabilityField=()=>null; const sizeForRun=()=> '1024x1024';
const fetch=async (url,options)=>{calls.push({url,payload:JSON.parse(options.body)});return {ok:true,json:async()=>({task_id:'local-id'})}};
"""+functions+"""
(async()=>{
const settings={provider_id:'atelier-images',model:'gpt-image-2',count:2,resolution:'1K',aspectRatio:'1:1'};
const batch=await runApiGeneration('garment',[{url:'/assets/input/reference.png'}],settings);
let blocked=false;try{await runApiGeneration('garment',[],{...settings,count:3})}catch{blocked=true}
let missingBlocked=false;try{await runApiGeneration('garment',[],{...settings,provider_id:'deleted-provider'})}catch{missingBlocked=true}
process.stdout.write(JSON.stringify({batch,calls,blocked,missingBlocked,checks,isKie:isKieProviderId('atelier-images')}));
})().catch(error=>{console.error(error);process.exit(1)});
""")
        self.assertTrue(result['isKie']);self.assertTrue(result['blocked'])
        self.assertEqual(len(result['calls']),1)
        self.assertEqual(result['calls'][0]['payload']['n'],2)
        self.assertEqual(result['calls'][0]['payload']['request_id'],'random-nonce')
        self.assertEqual(result['batch']['taskIds'],['local-id'])
        # 未知平台不提交任务；每次运行前都做了提示词长度与 Kie 参数校验
        self.assertTrue(result['missingBlocked'])
        self.assertEqual(result['checks']['prompt'],3)
        self.assertEqual(result['checks']['kie'],2)

    def test_recovery_uses_only_local_owned_task_and_preserves_old_result_guards(self):
        query=function_source('querySmartImageTaskNow')
        self.assertIn('/api/canvas-image-tasks/${encodeURIComponent(task.taskId)}/refresh',query)
        self.assertIn('pollSmartCanvasTask(task.taskId, node, task)',query)
        poll=function_source('pollSmartCanvasTask')
        self.assertIn("task.recovery === 'query-existing'",poll)
        self.assertIn('controlledImageTaskRecovery(taskId, task.error)',poll)
        self.assertIn('recoverTaskId:taskId, controlled:true',function_source('controlledImageTaskRecovery'))
        self.assertIn('本地任务查询断开，可手动查询原任务；未重新提交',poll)
        llm=function_source('runPromptLLMNode')
        self.assertIn('activeRun.token !== token',llm)
        self.assertIn('liveNode.running = false',llm)
        self.assertIn('taskResult.controlled ? taskIds.length',SMART)
        self.assertIn('outImages.controlled ? taskIds.length',SMART)
