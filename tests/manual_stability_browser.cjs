/* Real browser + real isolated Instances. Only upstream is replaced. */
const {chromium}=require('playwright'),{spawn}=require('node:child_process'),rl=require('node:readline'),fs=require('node:fs'),assert=require('node:assert/strict');
const out=process.env.MIO_BROWSER_OUTPUT;if(!out)throw Error('MIO_BROWSER_OUTPUT required');fs.mkdirSync(out,{recursive:true});
const fixture=spawn('.venv/bin/python',['tests/manual_stability_browser.py'],{stdio:['pipe','pipe','pipe']});let stderr='';fixture.stderr.on('data',d=>stderr+=d);
fixture.on('exit',code=>{if(code)console.error('fixture failed: '+stderr)});
const queue=[],wait=[];rl.createInterface({input:fixture.stdout}).on('line',l=>{try{const o=JSON.parse(l);wait.length?wait.shift()(o):queue.push(o)}catch(e){stderr+=l}});
const next=()=>queue.length?Promise.resolve(queue.shift()):new Promise((r,j)=>{wait.push(r);setTimeout(()=>j(Error('fixture timeout '+stderr.slice(-500))),30000)});
const cmd=async o=>{fixture.stdin.write(JSON.stringify(o)+'\n');return next()};let browser;const evidence={pages:[],errors:[],real_upstream_calls:0};
(async()=>{
 const cfg=await next();browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}});
 await context.route('**/*',route=>{const u=new URL(route.request().url());return ['127.0.0.1','localhost'].includes(u.hostname)||['data:','blob:'].includes(u.protocol)?route.continue():route.abort()});
 async function login(page,origin=cfg.A,username='A'){await page.goto(origin+'/login');await page.locator('input[name=username]').fill(username);await page.locator('input[name=password]').fill('Mock-browser-stability-2026');await page.locator('button[type=submit]').click();await page.waitForURL(origin+'/');}
 const page=await context.newPage();page.on('dialog',d=>d.dismiss());page.on('pageerror',e=>evidence.errors.push(e.message));await login(page);
 for(const kind of ['normal','smart']){
  const smart=kind==='smart',url=cfg.A+'/static/'+(smart?'smart-canvas':'canvas')+'.html?id='+cfg.canvases[kind];await page.goto(url);
  await page.waitForFunction(()=>typeof apiProviders!=='undefined'&&apiProviders.some(p=>p.id==='same-personal-id')&&typeof canvas!=='undefined'&&canvas);
  await page.evaluate(smart=>{const n={id:'browser-llm',type:smart?'smart-prompt':'llm',x:100,y:100,w:420,h:450,llmEnabled:true,llmProvider:'same-personal-id',llmModel:'novel-model-v2030',model:'novel-model-v2030',text:'反推提示词',llmInstruction:'反推提示词',userInput:'反推提示词'};nodes.push(n);render();},smart);
  const provider=page.locator(smart?'.prompt-llm-provider':'.llm-provider-select').first(),model=page.locator(smart?'.prompt-llm-model':'.llm-model').first();
  await provider.selectOption('same-personal-id');await model.selectOption('novel-model-v2030');
  await page.locator(smart?'.prompt-node-run':'.llm-run').first().click();await page.waitForFunction(smart=>{const n=nodes.find(n=>n.id==='browser-llm');return !n.running&&(smart?n.text:n.outputText)?.includes('MOCK')},smart);
  await page.evaluate(()=>saveCanvas());await page.reload();await page.waitForFunction(()=>typeof nodes!=='undefined'&&nodes.some(n=>n.id==='browser-llm'));
  assert.equal(await page.evaluate(smart=>{const n=nodes.find(n=>n.id==='browser-llm');return (smart?n.text:n.outputText).includes('MOCK')},smart),true);
  await page.evaluate(smart=>{const n=nodes.find(n=>n.id==='browser-llm');n.userInput='BROWSER_FAIL';n.llmInstruction='BROWSER_FAIL';n.text='BROWSER_FAIL';if(smart)render();else refreshNodes([n.id]);},smart);
  const response=page.waitForResponse(r=>r.url().endsWith('/api/canvas-llm')&&r.status()===502);await page.locator(smart?'.prompt-node-run':'.llm-run').first().click();const detail=(await (await response).json()).detail;assert.equal(detail.category,'routing_unavailable');assert.match(detail.event_id,/^[a-f0-9]{32}$/);
  await page.waitForFunction(()=>!nodes.find(n=>n.id==='browser-llm').running);
  const before=await cmd({op:'evidence'});await page.evaluate(smart=>{const n=nodes.find(n=>n.id==='browser-llm');n.llmProvider='deleted-provider';n.userInput='keep input';n.text='keep input';if(smart)render();else refreshNodes([n.id]);},smart);
  await page.locator(smart?'.prompt-node-run':'.llm-run').first().click();await page.waitForFunction(()=>!nodes.find(n=>n.id==='browser-llm').running);const after=await cmd({op:'evidence'});assert.equal(after.calls,before.calls);
  await page.evaluate(()=>saveCanvas());await page.reload();await page.waitForFunction(()=>typeof nodes!=='undefined'&&nodes.some(n=>n.id==='browser-llm'));
  assert.equal(await page.evaluate(()=>nodes.find(n=>n.id==='browser-llm').llmProvider),'deleted-provider');
  await context.clearCookies();await login(page);await page.goto(url);await page.waitForFunction(()=>typeof nodes!=='undefined'&&nodes.some(n=>n.id==='browser-llm'));assert.equal(await page.evaluate(()=>nodes.find(n=>n.id==='browser-llm').llmProvider),'deleted-provider');
  await page.screenshot({path:out+'/'+kind+'.png'});evidence.pages.push({kind,success:true,explicit_failure:detail.category,invalid_no_network:true,save_refresh_relogin:true});
 }

 // Cross-tab save uses the real settings implementation and broadcast.
 const normal=await context.newPage(),smart=await context.newPage(),settingsPage=await context.newPage();
 await normal.goto(cfg.A+'/static/canvas.html?id='+cfg.canvases.normal);await smart.goto(cfg.A+'/static/smart-canvas.html?id='+cfg.canvases.smart);
 for(const tab of [normal,smart])await tab.waitForFunction(()=>typeof apiProviders!=='undefined'&&apiProviders.some(p=>p.id==='same-personal-id'));
 await settingsPage.goto(cfg.A+'/static/api-settings.html');await settingsPage.waitForFunction(()=>typeof providers!=='undefined'&&providers.some(p=>p.id==='same-personal-id'));
 assert.equal(await settingsPage.evaluate(async()=>{selectedId='same-personal-id';providers.find(p=>p.id===selectedId).image_models.push('new-visible-model');renderEditor();return saveProviders()}),true);
 for(const tab of [normal,smart])await tab.waitForFunction(()=>apiProviders.find(p=>p.id==='same-personal-id').image_models.includes('new-visible-model'));
 evidence.cross_tab_save_refresh=true;
 // Invalid existing model remains after real renderer, then user may reselect.
 await normal.evaluate(()=>{const n=nodes.find(n=>n.id==='browser-llm');n.llmProvider='same-personal-id';n.model='deleted-model';render()});assert.equal(await normal.evaluate(()=>nodes.find(n=>n.id==='browser-llm').model),'deleted-model');
 await normal.locator('.llm-model').first().selectOption('novel-model-v2030');assert.equal(await normal.evaluate(()=>nodes.find(n=>n.id==='browser-llm').model),'novel-model-v2030');
 // Submit once, fail local CDN download, recover only original receipt in each canvas.
 for(const [tab,kind] of [[normal,'normal'],[smart,'smart']]){
  await tab.evaluate(()=>saveCanvas());await cmd({op:'download',fail:true});const before=await cmd({op:'evidence'});
  const taskId=await tab.evaluate(async()=>{const r=await fetch('/api/canvas-image-tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider_id:'same-personal-id',model:'novel-model-v2030',prompt:'synthetic recovery',request_id:crypto.randomUUID(),size:'1024x1024'})});if(!r.ok)throw Error(await r.text());return (await r.json()).task_id});
  await tab.waitForFunction(async id=>(await fetch('/api/canvas-image-tasks/'+id).then(r=>r.json())).status==='result_recovery_required',taskId);
  await cmd({op:'download',fail:false});await tab.evaluate(async id=>{const r=await fetch('/api/canvas-image-tasks/'+id+'/refresh',{method:'POST'});if(!r.ok)throw Error(await r.text());},taskId);
  const result=await tab.evaluate(async([id,kind])=>kind==='normal'?waitCanvasImageTaskResult(id):pollSmartCanvasTask(id),[taskId,kind]);assert.ok(result.images[0].startsWith('/'));
  await tab.evaluate(async id=>{await fetch('/api/canvas-image-tasks/'+id+'/refresh',{method:'POST'});},taskId);
  const after=await cmd({op:'evidence'});assert.equal(after.submits,before.submits+1);const media=await tab.request.get(cfg.A+result.images[0]);assert.equal(media.status(),200);
  const history=await tab.evaluate(()=>fetch('/api/history').then(r=>r.json()));fs.writeFileSync(out+'/history-'+kind+'.json',JSON.stringify(history));evidence.pages.find(p=>p.kind===kind).original_result_recovery=true;
 }
 // Actual streaming chat: click send, observe partial output, click stop.
 await page.goto(cfg.A+'/static/gpt-chat.html');await page.waitForFunction(()=>typeof config!=='undefined'&&config.api_providers?.some(p=>p.id==='same-personal-id'));
 await page.evaluate(()=>{provider='same-personal-id';activeChatModel='novel-model-v2030';mode='chat';});await page.locator('#messageInput').fill('BROWSER_SLOW');await page.locator('#sendBtn').click();await page.locator('#messages').getByText(/PARTIAL PARTIAL/).waitFor();await page.locator('#sendBtn').click();
 await page.waitForFunction(()=>activeChatController===null);assert.equal(await page.locator('#messageInput').inputValue(),'BROWSER_SLOW');assert.ok((await page.locator('#messages').innerText()).includes('部分输出'));
 evidence.stream_cancel_preserves_input_and_partial=true;
 await page.screenshot({path:out+'/stream-cancel.png'});
 // Distinct account sees no personal provider inherited from A.
 const other=await browser.newContext();const bp=await other.newPage();await login(bp,cfg.B,'B');assert.equal(await bp.evaluate(async()=>(await fetch('/api/config').then(r=>r.json())).api_providers.some(p=>p.id==='same-personal-id')),false);evidence.ab_no_provider_inheritance=true;await other.close();
 evidence.mock=await cmd({op:'evidence'});assert.deepEqual(evidence.mock.unmatched,[]);
 fs.writeFileSync(out+'/browser.json',JSON.stringify(evidence,null,2));console.log(JSON.stringify(evidence));
})().catch(e=>{fs.writeFileSync(out+'/failure.txt',String(e)+'\n'+stderr.slice(-3000));console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();fixture.stdin.write('{"op":"finish"}\n');fixture.stdin.end()});
