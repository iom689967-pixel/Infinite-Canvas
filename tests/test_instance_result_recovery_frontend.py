import unittest
from pathlib import Path
from test_smart_generation_polling_incremental_ui import function_source,run_node

class ResultRecoveryFrontendTests(unittest.TestCase):
    def test_detached_completed_binding_never_applies_to_stale_original_node(self):
        value=run_node("""
const canvasId='canvas';const node={id:'original',images:[{url:'/adopted.png'}]};
"""+function_source('assertSmartTaskBinding')+"""
let detached=false;try{assertSmartTaskBinding({status:'succeeded',binding_status:'detached',binding:{canvas_id:'canvas',node_id:'original',generation_id:'generation'}},node,{generationId:'generation'})}catch(e){detached=e.detachedRecovery}
process.stdout.write(JSON.stringify({detached,images:node.images}));
""")
        self.assertTrue(value['detached']);self.assertEqual(value['images'],[{'url':'/adopted.png'}])

    def test_detached_query_notice_does_not_save_or_finalize_canvas(self):
        value=run_node("""
const canvasId='canvas',task={taskId:'local',generationId:'gen',recoverTaskId:'local',controlled:true};
const node={id:'original',images:[{url:'/adopted.png'}],pendingTasks:[task]},nodes=[node];
let saves=0,finalizes=0;const notices=[];
const render=()=>{},scheduleSave=()=>saves++,toast=s=>notices.push(s),smartPendingTasks=n=>n.pendingTasks||[],smartRecoverableImageTask=()=>task,extractUpstreamTaskId=()=>'';
const finalizeSmartPendingTask=()=>finalizes++;
const fetch=async()=>({ok:true,json:async()=>({status:'succeeded',binding_status:'detached',binding:{canvas_id:'canvas',node_id:'original',generation_id:'gen'}})});
"""+function_source('assertSmartTaskBinding')+function_source('querySmartImageTaskNow')+"""
(async()=>{await querySmartImageTaskNow('original','local');process.stdout.write(JSON.stringify({saves,finalizes,notices,images:node.images}));})();
""")
        self.assertEqual(value['saves'],0);self.assertEqual(value['finalizes'],0)
        self.assertEqual(value['images'],[{'url':'/adopted.png'}]);self.assertIn('历史记录',value['notices'][0])

    def test_detached_poll_never_returns_images_to_any_node(self):
        value=run_node("""
const activeSmartTaskPolls=new Map(),activeSmartTaskControllers=new Map();
const smartTaskDelay=async()=>{},fetch=async()=>({ok:true,json:async()=>({status:'succeeded',binding_status:'detached',result:{images:['/original.png']}})});
let updates=0;const updateSmartPendingTaskStatus=()=>updates++;
"""+function_source('pollSmartCanvasTask')+"""
(async()=>{let detached=false;try{await pollSmartCanvasTask('local')}catch(e){detached=e.detachedRecovery}process.stdout.write(JSON.stringify({detached,updates,polls:activeSmartTaskPolls.size,controllers:activeSmartTaskControllers.size}));})();
""")
        self.assertEqual(value,{'detached':True,'updates':0,'polls':0,'controllers':0})

    def test_cancelled_generation_protection_is_unchanged(self):
        value=run_node("""
const liveNodeGenerationState=n=>n,nodeGenerationAttemptForTask=n=>n.generationHistory[0];
"""+function_source('finalizeSmartPendingTask')+"""
const node={id:'original',generationHistory:[{id:'gen',status:'cancelled',outputs:[]}],images:[{url:'/adopted.png'}]};
const before=JSON.stringify(node);finalizeSmartPendingTask(node,'local',[{url:'/recovered.png'}]);process.stdout.write(JSON.stringify({unchanged:before===JSON.stringify(node)}));
""")
        self.assertTrue(value['unchanged'])

    def test_history_notice_uses_detached_enum_not_arbitrary_message_html(self):
        online=(Path(__file__).resolve().parents[1]/'static/online.html').read_text()
        source=online[online.index('function detachedRecoveryNotice('):online.index('function openLightbox(')]
        value=run_node(source+"""
process.stdout.write(JSON.stringify({detached:detachedRecoveryNotice({binding_status:'detached',recovery_message:'<unsafe>'}),attached:detachedRecoveryNotice({binding_status:'attached'})}));
""")
        self.assertIn('不会自动写入其他节点',value['detached']);self.assertNotIn('<unsafe>',value['detached']);self.assertEqual(value['attached'],'')

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
