const {test}=require('node:test');const assert=require('node:assert/strict');
const {Coordinator,payload}=require('../static/js/smart-save-coordinator.js');
const copy=x=>structuredClone(x),tick=()=>new Promise(r=>setImmediate(r));
function memory(){const m=new Map();return {get length(){return m.size},key:i=>[...m.keys()][i],getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,v),removeItem:k=>m.delete(k)}}
function lab(options={}){
 let server={id:'c',title:'test',icon:'sparkles',nodes:[{id:'p',type:'smart-prompt',text:'A'}],connections:[],viewport:{},logs:[],settings:{},updated_at:100};let draft=copy(server),calls=[],gates=[];
 const storage=options.storage||memory();const state=[];
 const o={id:'c',epoch:options.epoch||'tab1',scope:options.scope||'userA',storage,server:copy(server),capture:()=>draft,apply:s=>{draft=copy(s)},onState:s=>state.push(s),get:async()=>copy(server),debounce:100000,
 put:body=>{calls.push(copy(body));return new Promise(resolve=>gates.push(()=>{
 if(body.base_updated_at<server.updated_at)return resolve({status:409,canvas:copy(server)});
 server={...server,...copy(payload(body)),updated_at:server.updated_at+1};resolve({status:200,canvas:copy(server)});
 }))},...options};
 const c=new Coordinator(o);return {c,storage,calls,gates,state,edit:text=>{draft.nodes[0].text=text;c.mark(false)},mutate:fn=>{fn(draft);c.mark(false)},server:()=>copy(server),remote:fn=>{fn(server)},local:()=>copy(draft)};
}
test('single writer freezes B; new C waits; stale acknowledgement does not clear C draft',async()=>{
 const f=lab();f.edit('B');const p=f.c.flush();f.edit('C');const q=f.c.flush();assert.equal(f.calls.length,1);assert.equal(f.calls[0].nodes[0].text,'B');assert.equal(f.c.state().revision,2);
 f.gates.shift()();await tick();assert.equal(f.calls.length,2);assert.equal(f.calls[1].nodes[0].text,'C');assert.equal(f.calls[1].base_updated_at,101);assert.equal(f.storage.length,1);
 f.gates.shift()();assert.equal(await p,true);await q;assert.equal(f.server().nodes[0].text,'C');assert.equal(f.c.state().status,'saved');assert.equal(f.storage.length,0);
});
test('duplicate close/debounce flush cannot submit same snapshot twice',async()=>{const f=lab();f.edit('B');const p=f.c.flush(),q=f.c.flush();f.gates.shift()();await Promise.all([p,q]);await f.c.flush();assert.equal(f.calls.length,1)});
test('409 keeps C and exact confirmed baseline without automatic resubmit',async()=>{const f=lab();f.edit('C');f.remote(s=>{s.nodes[0].text='B';s.updated_at=101});const p=f.c.flush();f.gates.shift()();assert.equal(await p,false);assert.equal(f.local().nodes[0].text,'C');assert.equal(f.c.version,100);assert.equal(f.c.remote.updated_at,101);assert.equal(f.calls.length,1);assert.equal(f.c.state().status,'conflict');assert.equal(await f.c.flush(),false);});
test('WS/GET while dirty preserves entire graph; clean remote deletes are not resurrected',()=>{const f=lab();f.edit('C');f.remote(s=>{s.nodes=[];s.updated_at++});f.c.observe(f.server());assert.equal(f.local().nodes.length,1);assert.equal(f.c.state().status,'conflict');const g=lab();g.remote(s=>{s.nodes=[];s.updated_at++});g.c.observe(g.server());assert.equal(g.local().nodes.length,0)});
test('new remote version during B flight does not become C write base',async()=>{const f=lab();f.edit('B');const p=f.c.flush();f.edit('C');f.remote(s=>{s.nodes[0].text='D';s.updated_at=105});f.c.observe(f.server());f.gates.shift()();await p;assert.equal(f.c.version,100);assert.equal(f.calls.length,1);assert.equal(f.local().nodes[0].text,'C')});
test('lost response reconciles exact sent snapshot; only then saves newer C',async()=>{const f=lab({put:async()=>{throw Error('lost')}});f.edit('B');await f.c.flush();f.edit('C');assert.equal(f.c.state().status,'uncertain');f.remote(s=>{s.nodes[0].text='B';s.updated_at++});assert.equal(await f.c.check(),true);assert.equal(f.c.version,101);assert.equal(f.c.dirty(),true);assert.equal(f.local().nodes[0].text,'C');});
test('lost response and other tab change never blindly advances baseline',async()=>{const f=lab({put:async()=>{throw Error('lost')}});f.edit('C');await f.c.flush();f.remote(s=>{s.nodes[0].text='D';s.updated_at++});await f.c.check();assert.equal(f.c.version,100);assert.equal(f.c.state().status,'conflict');assert.equal(await f.c.flush(),false)});
test('unchanged server does not prove uncertain request ended',async()=>{const f=lab({put:async()=>{throw Error('lost')}});f.edit('C');await f.c.flush();await f.c.check();assert.equal(f.c.state().status,'uncertain');assert.equal(await f.c.discard(),false)});
test('refresh restores exact draft with Chinese text, model, layout, deletion and edges',async()=>{const storage=memory(),f=lab({storage});f.mutate(s=>{s.title='长标题';s.nodes=[{id:'z',type:'smart-image-generation',text:'中文'.repeat(15000),runSettings:{model:'exact-model'},x:123,y:45}];s.connections=[{from:'z',to:'z'}]});const g=lab({storage,epoch:'tab2'});assert.equal(g.c.state().status,'recovery');assert.equal(g.c.restore(f.c.key),true);assert.equal(g.local().nodes[0].text.length,30000);assert.equal(g.local().nodes[0].x,123);assert.deepEqual(g.local().connections,[{from:'z',to:'z'}]);assert.equal(await g.c.resolveRestored(),true);assert.equal(storage.length,2);const p=g.c.flush();g.gates.shift()();await p;assert.equal(storage.length,0)});
test('A/B identity and branches never mix; active changed recovery branch is not deleted',async()=>{const storage=memory(),a=lab({storage}),b=lab({storage,scope:'userB',epoch:'B'});a.edit('A draft');assert.equal(b.c.readDrafts().length,0);const other=lab({storage,epoch:'other'});other.c.restore(a.c.key);a.edit('new A');await other.c.resolveRestored();const p=other.c.flush();other.gates.shift()();await p;assert.ok(storage.getItem(a.c.key));assert.equal(JSON.parse(storage.getItem(a.c.key)).draft.nodes[0].text,'new A')});
test('storage denied, budget and inline binary cannot claim local protection',()=>{const f=lab({storage:{get length(){return 0},setItem(){throw Error('quota')},getItem(){},key(){}}});f.edit('C');assert.equal(f.c.state().storageError,true);const g=lab();g.mutate(s=>s.nodes[0].url='data:image/png;base64,AAAA');assert.equal(g.c.state().storageError,true);assert.equal(g.storage.length,0)});
test('known rejection keeps draft; maintenance and auth stay distinct',async()=>{for(const [status,maintenance,expected] of [[401,false,'auth'],[503,true,'maintenance'],[413,false,'error']]){const f=lab({put:async()=>({status,maintenance})});f.edit('C');await f.c.flush();assert.equal(f.c.state().status,expected);assert.equal(f.local().nodes[0].text,'C');assert.equal(f.storage.length,1)}});
test('late reply after disposal cannot apply or clear a later recovery',async()=>{const f=lab();f.edit('B');const p=f.c.flush();f.edit('C');f.c.dispose();f.gates.shift()();await p;assert.equal(f.c.version,100);assert.equal(JSON.parse(f.storage.getItem(f.c.key)).draft.nodes[0].text,'C')});
test('mock result and manual edit preserve history identity without a submit',async()=>{const f=lab();f.edit('B');const p=f.c.flush();f.mutate(s=>{s.nodes[0].text='C';s.nodes.push({id:'g',type:'smart-image-generation',images:[{url:'/assets/result.png'}],generationHistory:[{id:'original',taskIds:['original-task'],status:'success'}]})});f.gates.shift()();await tick();f.gates.shift()();await p;assert.equal(f.server().nodes[0].text,'C');assert.equal(f.server().nodes[1].generationHistory.length,1);assert.deepEqual(f.server().nodes[1].generationHistory[0].taskIds,['original-task']);assert.equal(f.calls.length,2)});
test('explicit discard fetches latest server rather than old conflict snapshot',async()=>{const f=lab();f.edit('C');f.remote(s=>{s.nodes[0].text='B';s.updated_at++});f.c.observe(f.server());f.remote(s=>{s.nodes[0].text='D';s.updated_at++});assert.equal(await f.c.discard(),true);assert.equal(f.local().nodes[0].text,'D');assert.equal(f.c.version,102);assert.equal(f.storage.length,0)});
test('log write reserves coordinator; acknowledgement and queued edit cannot overlap',async()=>{
 const f=lab();let release;const p=f.c.externalWrite(async()=>{await new Promise(r=>release=r);f.remote(s=>{s.logs=[];s.updated_at++});f.c.observe(f.server())});await tick();assert.equal(f.c.reserved,true);f.edit('queued');assert.equal(await f.c.flush(),false);assert.equal(f.calls.length,0);release();await p;assert.equal(f.c.state().status,'conflict');assert.equal(f.local().nodes[0].text,'queued');
});
test('same log response already observed over WS can acknowledge without rewriting',async()=>{
 const f=lab();await f.c.externalWrite(async()=>{f.remote(s=>s.updated_at++);assert.equal(f.c.observe(f.server()),true);assert.equal(f.c.observe(f.server()),true)});assert.equal(f.calls.length,0);
});
test('load repairs are pending edits but renderer normalization alone is not',()=>{
 const f=lab();f.c.o.capture=()=>({...f.local(),nodes:[]});f.c.loaded(true);assert.equal(f.c.dirty(),true);f.c.dispose();const g=lab();g.c.o.capture=()=>({...g.local(),viewport:{x:0}});g.c.loaded();assert.equal(g.c.dirty(),false);
});
test('ambiguous auxiliary write cannot unlock queued save or discard on GET alone',async()=>{
 const f=lab();await assert.rejects(f.c.externalWrite(async()=>{f.edit('C');throw Error('lost log response')}));assert.equal(f.c.state().status,'uncertain');assert.equal(await f.c.check(),false);assert.equal(await f.c.discard(),false);assert.equal(f.calls.length,0);
});
test('matching 401 response records known rejection before auth storage is deactivated',async()=>{
 const f=lab();f.edit('C');const p=f.c.flush();f.c.rejectedAuth();assert.equal(JSON.parse(f.storage.getItem(f.c.key)).sent,null);assert.equal(f.c.state().status,'auth');f.c.dispose();f.gates.shift()();await p;
});
test('recovery plus fresh result can explicitly save current branch without deleting old draft',async()=>{
 const storage=memory(),old=lab({storage});old.edit('older');const fresh=lab({storage,epoch:'new'});fresh.mutate(s=>s.nodes[0].result='/assets/recovered.png');assert.equal(fresh.c.restore(old.c.key),false);assert.equal(await fresh.c.keepCurrent(),true);const p=fresh.c.flush();fresh.gates.shift()();await p;assert.equal(fresh.server().nodes[0].result,'/assets/recovered.png');assert.ok(storage.getItem(old.c.key));
});
test('late GET cannot lower an acknowledged version or discard edits made after confirmation',async()=>{
 let resolve;const f=lab({get:()=>new Promise(r=>resolve=r)});f.edit('C');f.c.block='conflict';const discard=f.c.discard();f.edit('newer D');resolve({...f.server(),updated_at:101});assert.equal(await discard,false);assert.equal(f.local().nodes[0].text,'newer D');assert.equal(f.c.version,100);
 const g=lab({get:()=>new Promise(r=>resolve=r)});const check=g.c.check();g.edit('C');const save=g.c.flush();g.gates.shift()();await save;resolve(g.server());assert.equal(await check,false);assert.equal(g.c.version,101);
});
test('invalid remote versions and out-of-order GET do not replace local state',async()=>{
 const f=lab();assert.equal(f.c.observe({...f.server(),updated_at:'broken',nodes:[]}),false);assert.equal(f.local().nodes.length,1);
 let gates=[];const g=lab({get:()=>new Promise(r=>gates.push(r))});const a=g.c.check(),b=g.c.check();gates[1](g.server());await b;gates[0]({...g.server(),updated_at:99});assert.equal(await a,false);assert.equal(g.c.version,100);
});
