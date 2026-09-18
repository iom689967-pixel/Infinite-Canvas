const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {Coordinator}=require('../static/js/smart-save-coordinator.js');
// Execute the actual page renderer, not a duplicate visibility predicate.
const source=fs.readFileSync(require.resolve('../static/js/smart-canvas.js'),'utf8');
const renderer=source.slice(source.indexOf('function renderSmartSaveState('),source.indexOf('function exportSmartUnsavedDraft('));
function ui(){
 const roots=[];const toasts=[];
 class Element {
  constructor(tag){this.tag=tag;this.dataset={};this.children=[];this.textContent='';}
  setAttribute(){}
  set innerHTML(_){this.parts={'[role=status]':new Element('div'),small:new Element('small'),'.smart-save-actions':new Element('div')};}
  querySelector(s){return this.parts[s];}
  appendChild(el){this.children.push(el);}
  replaceChildren(){this.children=[];}
  remove(){roots.splice(roots.indexOf(this),1);}
 }
 const context={document:{getElementById:id=>roots.find(e=>e.id===id),createElement:tag=>new Element(tag),body:{appendChild:e=>roots.push(e)}},
  canvasSyncInFlight:false,toast:s=>toasts.push(s),exportSmartUnsavedDraft(){},smartSaveCoordinator:{},Date};
 vm.createContext(context);vm.runInContext(renderer,context);
 return {render:context.renderSmartSaveState,context,toasts,panel:()=>roots[0],labels:()=>roots[0]?.querySelector('.smart-save-actions').children.map(c=>c.textContent)};
}
const state=(status,extra={})=>({status,inFlight:status==='saving',storageError:false,hasDraft:status!=='saved',recoveries:[],...extra});
for(const status of ['unsaved','saving','saved'])test(`normal ${status} creates no panel or toast`,()=>{
 const u=ui();u.render(state(status));assert.equal(u.panel(),undefined);assert.deepEqual(u.toasts,[]);assert.equal(u.context.canvasSyncInFlight,status==='saving');
});
test('all actionable states remain visible and returning to normal removes stale alerts',()=>{
 const u=ui();for(const s of ['conflict','error','uncertain','maintenance','auth','recovery']){
  u.render(state(s,{recoveries:[{key:'draft',at:1}]}));assert.equal(u.panel().dataset.state,s);assert.ok(u.panel().querySelector('[role=status]').textContent);
  assert.ok(u.labels().includes('导出当前草稿'));if(s==='recovery')assert.ok(u.labels().includes('恢复本机草稿'));
  u.render(state('saved'));assert.equal(u.panel(),undefined);
 }
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
test('refresh recovery remains visible with preserved draft and baseline',()=>{
 const f=lab();f.edit('C');const u=ui();const c=new Coordinator({...f.c.o,epoch:'tab2',onState:u.render});
 assert.equal(c.state().status,'recovery');assert.ok(u.labels().includes('恢复本机草稿'));assert.equal(c.protected(),true);assert.equal(c.version,10);assert.equal(JSON.parse(f.s.getItem(f.c.key)).draft.nodes[0].text,'C');
});
test('actual beforeunload handler still guards a silent dirty draft and releases after acknowledgement',async()=>{
 const f=lab();let beforeunload;
 const handler=source.slice(source.indexOf("window.addEventListener('beforeunload'"),source.indexOf("window.addEventListener('pagehide'"));
 vm.runInNewContext(handler,{window:{InstanceSession:{active:true},addEventListener:(_,fn)=>beforeunload=fn},smartSaveCoordinator:f.c});
 f.edit('pending');assert.equal(f.u.panel(),undefined);
 const blocked={preventDefault(){this.prevented=true}};beforeunload(blocked);assert.equal(blocked.prevented,true);assert.equal(blocked.returnValue,'');
 await f.c.flush();const clean={preventDefault(){this.prevented=true}};beforeunload(clean);assert.equal(clean.prevented,undefined);assert.equal(clean.returnValue,undefined);f.c.dispose();
});
