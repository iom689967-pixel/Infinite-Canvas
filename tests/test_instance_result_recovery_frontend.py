import unittest
from test_smart_generation_polling_incremental_ui import function_source,run_node

class ResultRecoveryFrontendTests(unittest.TestCase):
    def test_recoverable_success_is_not_terminal_failure(self):
        value=run_node(function_source('smartTaskHasTerminalFailure')+"""
process.stdout.write(JSON.stringify({pending:smartTaskHasTerminalFailure({status:'failed',resultRecoveryRequired:true}),terminal:smartTaskHasTerminalFailure({status:'failed'}),state:smartTaskHasTerminalFailure({status:'result_recovery_required'})}));
""")
        self.assertEqual(value,{'pending':False,'terminal':True,'state':False})

    def test_original_node_recovery_and_double_click(self):
        sources='\n'.join(function_source(n) for n in ['assertSmartTaskBinding','querySmartImageTaskNow','finalizeSmartPendingTask','completeNodeGenerationAttempt'])
        value=run_node("""
const canvasId='canvas';const task={taskId:'local',generationId:'generation',recoverTaskId:'local',controlled:true,resultRecoveryRequired:true};
const attempt={id:'generation',status:'running',outputs:[],layout:{},createdAt:1};
const node={id:'original',activeGenerationId:'generation',generationHistory:[attempt],images:[{url:'/old.png'}],pendingTasks:[task],pending:1};
const nodes=[node],calls=[];const render=()=>{},scheduleSave=()=>{},toast=()=>{},nowMs=()=>2;
const smartPendingTasks=n=>n.pendingTasks||[],smartRecoverableImageTask=()=>task,extractUpstreamTaskId=()=>'',liveNodeGenerationState=n=>n;
const nodeGenerationAttemptForTask=()=>attempt,nodeGenerationAttempt=()=>attempt;
const resultMediaUrls=x=>x,normalizeNodeGenerationOutputs=x=>x;
const cleanHistoryImages=x=>[...new Map(x.map(i=>[i.url,i])).values()];
const markSmartNodeComplete=()=>{},clearNodeGenerationTerminalState=()=>{};
const applyNodeGenerationAttempt=(n,a)=>{n.images=a.outputs;return true};
const fetch=async (url,options)=>{calls.push({url,method:options.method});return {ok:true,json:async()=>({binding:{canvas_id:'canvas',node_id:'original',generation_id:'generation'}})}};
const pollSmartCanvasTask=async()=>({images:[{url:'/original-result.png'}]});
"""+sources+"""
(async()=>{await Promise.all([querySmartImageTaskNow('original','local'),querySmartImageTaskNow('original','local')]);process.stdout.write(JSON.stringify({calls,nodeCount:nodes.length,images:node.images,versions:node.generationHistory.length,status:attempt.status}));})().catch(e=>{console.error(e);process.exit(1)});
""")
        self.assertEqual(value['calls'],[{'url':'/api/canvas-image-tasks/local/refresh','method':'POST'}])
        self.assertEqual(value['nodeCount'],1);self.assertEqual(value['versions'],1)
        self.assertEqual(value['images'],[{'url':'/original-result.png'}]);self.assertEqual(value['status'],'success')

    def test_page_load_checks_once_without_download_or_generation(self):
        value=run_node("""
const smartRecoveryStateChecks=new Set(),canvasId='canvas',calls=[];
const fetch=async url=>{calls.push(url);return {ok:true,json:async()=>({status:'result_recovery_required',binding:{canvas_id:'canvas',node_id:'node',generation_id:'gen'}})}};
"""+function_source('assertSmartTaskBinding')+function_source('checkSmartRecoveredResult')+"""
(async()=>{const node={id:'node'},task={taskId:'local',generationId:'gen'};await checkSmartRecoveredResult(node,task);await checkSmartRecoveredResult(node,task);process.stdout.write(JSON.stringify(calls));})();
""")
        self.assertEqual(value,['/api/canvas-image-tasks/local'])

    def test_binding_tamper_and_superseded_attempt_do_not_apply(self):
        value=run_node("""
const canvasId='canvas';let applied=false;
const liveNodeGenerationState=n=>n,nodeGenerationAttemptForTask=()=>({status:'success',id:'old'});
"""+function_source('assertSmartTaskBinding')+function_source('finalizeSmartPendingTask')+"""
let denied=false;try{assertSmartTaskBinding({binding:{canvas_id:'canvas',node_id:'other'}},{id:'node'},{})}catch{denied=true}
const node={id:'node',activeGenerationId:'new',images:[{url:'/new.png'}]};
finalizeSmartPendingTask(node,'local',[{url:'/old.png'}]);process.stdout.write(JSON.stringify({denied,images:node.images}));
""")
        self.assertTrue(value['denied']);self.assertEqual(value['images'],[{'url':'/new.png'}])
