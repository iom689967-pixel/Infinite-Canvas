const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {Coordinator}=require('../static/js/smart-save-coordinator.js');
// Execute the actual page renderer, not a duplicate visibility predicate.
const source=fs.readFileSync(require.resolve('../static/js/smart-canvas.js'),'utf8');
const renderer=source.slice(source.indexOf('let smartDraftPanelOpen='),source.indexOf('function exportSmartUnsavedDraft('));
function ui(){
 const roots=[];const toasts=[];
 class Element {
  constructor(tag){this.tag=tag;this.dataset={};this.children=[];this.textContent='';this.hidden=false;this.attrs={};const names=new Set();this.classList={toggle:(n,on)=>on?names.add(n):names.delete(n),contains:n=>names.has(n)};}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getAttribute(k){return this.attrs[k];}
  set innerHTML(_){this.parts={'[role=status]':new Element('div'),small:new Element('small'),'.smart-save-actions':new Element('div'),'.smart-save-collapse':new Element('button')};}
  querySelector(s){return this.parts[s];}
  appendChild(el){this.children.push(el);}
  replaceChildren(){this.children=[];}
  remove(){roots.splice(roots.indexOf(this),1);}
 }
 const toggle=new Element('button');toggle.id='smartDraftToggle';toggle.hidden=true;toggle.parts={'.smart-draft-label':new Element('span'),'.smart-draft-count':new Element('b')};
 const calls=[];
 const context={document:{getElementById:id=>id==='smartDraftToggle'?toggle:roots.find(e=>e.id===id),createElement:tag=>new Element(tag),body:{appendChild:e=>roots.push(e)}},
  canvasSyncInFlight:false,toast:s=>toasts.push(s),exportSmartUnsavedDraft(){calls.push('export')},saveCanvas(){calls.push('save')},confirm(){calls.push('confirm');return false},
  smartSaveCoordinator:{restore(){calls.push('restore')},resolveRestored(){calls.push('resolve')},keepCurrent(){calls.push('keep')},check(){calls.push('check')},discard(){calls.push('discard')}},Date};
 vm.createContext(context);vm.runInContext(renderer,context);
 return {render:context.renderSmartSaveState,context,toasts,toggle,calls,panel:()=>roots[0],labels:()=>roots[0]?.querySelector('.smart-save-actions').children.map(c=>c.textContent)};
}
const state=(status,extra={})=>({status,inFlight:status==='saving',storageError:false,hasDraft:status!=='saved',recoveries:[],...extra});
for(const status of ['unsaved','saving','saved'])test(`normal ${status} creates no panel or toast`,()=>{
 const u=ui();u.render(state(status));assert.equal(u.panel(),undefined);assert.equal(u.toggle.hidden,true);assert.deepEqual(u.toasts,[]);assert.equal(u.context.canvasSyncInFlight,status==='saving');
});
test('current actionable errors stay visible while recovery starts collapsed',()=>{
 for(const s of ['conflict','error','uncertain','maintenance','auth']){
  const u=ui();u.render(state(s,{recoveries:[{key:'draft',at:1}]}));assert.equal(u.panel().dataset.state,s);assert.ok(u.panel().querySelector('[role=status]').textContent);
  assert.equal(u.toggle.hidden,false);assert.equal(u.toggle.parts['.smart-draft-label'].textContent,'草稿待处理');assert.ok(u.labels().includes('导出当前草稿'));
 }
 const u=ui();u.render(state('recovery',{recoveries:[{key:'draft',at:1}]}));assert.equal(u.panel(),undefined);assert.equal(u.toggle.hidden,false);
 assert.equal(u.toggle.parts['.smart-draft-label'].textContent,'草稿待处理');assert.equal(u.toggle.getAttribute('aria-expanded'),'false');assert.equal(u.toggle.classList.contains('attention'),true);
});
test('draft entry opens full actions, closes explicitly and does not invoke persistence actions',()=>{
 const u=ui(),recovery=state('recovery',{recoveries:[{key:'one',at:1},{key:'two',at:2}]});u.render(recovery);
 assert.equal(u.toggle.parts['.smart-draft-count'].textContent,'2');u.toggle.onclick();
 assert.equal(u.toggle.getAttribute('aria-expanded'),'true');assert.equal(u.panel().dataset.drafts,'open');assert.ok(u.labels().includes('恢复本机草稿'));assert.ok(u.labels().includes('导出当前草稿'));assert.ok(u.labels().includes('核对服务器'));assert.deepEqual(u.calls,[]);
 u.panel().querySelector('.smart-save-collapse').onclick();assert.equal(u.panel(),undefined);assert.equal(u.toggle.getAttribute('aria-expanded'),'false');assert.deepEqual(u.calls,[]);
 u.render(recovery);assert.equal(u.panel(),undefined);
});
test('actual draft exporter keeps current content and strips task and credential bindings',async()=>{
 const exporter=source.slice(source.indexOf('function exportSmartUnsavedDraft('),source.indexOf('function initializeSmartSave('));let savedBlob,clicked=false,revoked=false;
 const context={smartSaveSnapshot:()=>({id:'canvas',updated_at:12,title:'copy',nodes:[{id:'p',text:'C-导出验收',taskId:'task',pendingTasks:[{task_id:'remote'}],apiKey:'secret'}]}),
  URL:{createObjectURL(blob){savedBlob=blob;return 'blob:test'},revokeObjectURL(){revoked=true}},Blob,
  document:{createElement(){return {click(){clicked=true}}}},setTimeout:fn=>fn()};
 vm.createContext(context);vm.runInContext(exporter,context);context.exportSmartUnsavedDraft();const body=JSON.parse(await savedBlob.text());
 assert.equal(clicked,true);assert.equal(revoked,true);assert.equal(body.kind,'smart-draft-copy');assert.equal(body.canvas.id,undefined);assert.equal(body.canvas.updated_at,undefined);
 assert.equal(body.canvas.nodes[0].text,'C-导出验收');assert.equal(body.canvas.nodes[0].taskId,undefined);assert.equal(body.canvas.nodes[0].pendingTasks,undefined);assert.equal(body.canvas.nodes[0].apiKey,undefined);
});
test('redraws preserve a user collapse and a new page renderer starts collapsed',()=>{
 const recovery=state('recovery',{recoveries:[{key:'draft',at:1}]});const first=ui();first.render(recovery);first.toggle.onclick();first.panel().querySelector('.smart-save-collapse').onclick();
 for(let i=0;i<5;i++)first.render({...recovery,revision:i});assert.equal(first.panel(),undefined);
 const reloaded=ui();reloaded.render(recovery);assert.equal(reloaded.panel(),undefined);assert.equal(reloaded.toggle.hidden,false);
});
test('storage protection failure overrides silence in every normal state',()=>{
 const u=ui();for(const s of ['unsaved','saving','saved']){
  u.render(state(s,{storageError:true}));assert.equal(u.panel().querySelector('[role=status]').textContent,'本机草稿保护失败');
  assert.match(u.panel().querySelector('small').textContent,/不要关闭页面/);assert.ok(u.labels().includes('导出当前草稿'));
  u.render(state(s));assert.equal(u.panel(),undefined);
 }
});
function storage(){const m=new Map();return {get length(){return m.size},key:i=>[...m.keys()][i],getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,v),removeItem:k=>m.delete(k)}}
function lab(put){
 const u=ui(),s=storage();let draft={id:'c',updated_at:10,nodes:[{id:'p',text:'A'}]},server=structuredClone(draft);
 const c=new Coordinator({id:'c',epoch:'tab1',scope:'A',server,storage:s,capture:()=>draft,apply:x=>draft=x,
  onState:u.render,put:put|| (async b=>({status:200,canvas:server={...b,id:'c',updated_at:server.updated_at+1}})),get:async()=>server});
 return {u,c,s,edit:text=>{draft.nodes[0].text=text;c.mark(false)},server:()=>server};
}
test('real B-to-C serialized saves stay silent and protected until C acknowledged',async()=>{
 const gates=[],calls=[];let version=10;
 const f=lab(b=>new Promise(resolve=>{calls.push(b);gates.push(()=>resolve({status:200,canvas:{...b,id:'c',updated_at:++version}}));}));
 f.edit('B');assert.equal(f.c.protected(),true);assert.equal(f.u.panel(),undefined);
 const p=f.c.flush();f.edit('C');assert.equal(calls.length,1);assert.equal(f.u.panel(),undefined);
 gates.shift()();await new Promise(r=>setImmediate(r));assert.equal(calls.length,2);assert.equal(calls[1].nodes[0].text,'C');assert.equal(f.c.protected(),true);assert.equal(f.u.panel(),undefined);
 gates.shift()();await p;assert.equal(f.c.ack,f.c.rev);assert.equal(f.c.draft.nodes[0].text,'C');assert.equal(f.c.protected(),false);assert.equal(f.u.panel(),undefined);assert.deepEqual(f.u.toasts,[]);
});
test('actual HTTP rejection and network failure states still surface',async()=>{
 for(const [put,expected] of [[async()=>({status:409,canvas:{id:'c',updated_at:11}}),'conflict'],[async()=>({status:413}),'error'],[async()=>({status:401}),'auth'],[async()=>({status:503,maintenance:true}),'maintenance'],[async()=>{throw Error('network')},'uncertain']]){
  const f=lab(put);f.edit('C');await f.c.flush();assert.equal(f.u.panel().dataset.state,expected);assert.equal(f.c.draft.nodes[0].text,'C');assert.equal(f.c.protected(),true);assert.equal(f.s.length,1);
 }
});
test('refresh recovery stays available but collapsed with preserved draft and baseline',()=>{
 const f=lab();f.edit('C');const u=ui();const c=new Coordinator({...f.c.o,epoch:'tab2',onState:u.render});
 assert.equal(c.state().status,'recovery');assert.equal(u.panel(),undefined);assert.equal(u.toggle.hidden,false);u.toggle.onclick();assert.ok(u.labels().includes('恢复本机草稿'));assert.equal(c.protected(),true);assert.equal(c.version,10);assert.equal(JSON.parse(f.s.getItem(f.c.key)).draft.nodes[0].text,'C');
});
test('actual beforeunload handler still guards a silent dirty draft and releases after acknowledgement',async()=>{
 const f=lab();let beforeunload;
 const handler=source.slice(source.indexOf("window.addEventListener('beforeunload'"),source.indexOf("window.addEventListener('pagehide'"));
 vm.runInNewContext(handler,{window:{InstanceSession:{active:true},addEventListener:(_,fn)=>beforeunload=fn},smartSaveCoordinator:f.c});
 f.edit('pending');assert.equal(f.u.panel(),undefined);
 const blocked={preventDefault(){this.prevented=true}};beforeunload(blocked);assert.equal(blocked.prevented,true);assert.equal(blocked.returnValue,'');
 await f.c.flush();const clean={preventDefault(){this.prevented=true}};beforeunload(clean);assert.equal(clean.prevented,undefined);assert.equal(clean.returnValue,undefined);f.c.dispose();
});
