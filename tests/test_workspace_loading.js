const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function boot(useIdle=true) {
    let clock=0, seq=0;const timers=new Map(), frames=new Map(), writes=[], events={};
    const set=(fn,ms=0)=>{const id=++seq;timers.set(id,{fn,at:clock+ms});return id;};
    for(const id of ['canvas','asset-manager','api-settings','online','gpt-chat','zimage','enhance','klein','angle','comfyui-settings']){
        let src='';const listeners={};frames.set(id,{dataset:{src:'/static/'+id+'.html'},contentWindow:{location:{href:'about:blank'}},
            setAttribute(){},addEventListener(t,fn){listeners[t]=fn;},
            get src(){return src;},set src(v){src=v;writes.push({id,at:clock});},
            loaded(){this.contentWindow.location.href=src;listeners.load();}});
    }
    const spinner={hidden:true};const doc={hidden:false,getElementById:id=>id==='workspace-opening'?spinner:frames.get(id.replace('frame-','')),
        addEventListener:(t,fn)=>events[t]=fn};
    const ctx={document:doc,Map,Object,setTimeout:set,clearTimeout:id=>timers.delete(id)};ctx.window=ctx;
    if(useIdle){ctx.requestIdleCallback=fn=>set(fn,1);ctx.cancelIdleCallback=id=>timers.delete(id);}
    vm.runInNewContext(fs.readFileSync('static/js/workspace-loading.js','utf8'),ctx);
    return {ctx,doc,frames,writes,spinner,events,tick(ms){const end=clock+ms;for(;;){const next=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;clock=next[1].at;timers.delete(next[0]);next[1].fn();}clock=end;}};
}
for(const idle of [true,false]) {
    const t=boot(idle), activate=id=>t.ctx.WorkspaceLoading.activate(id);
    activate('canvas');assert.deepEqual(t.writes.map(x=>x.id),['canvas']);
    t.tick(149);assert.equal(t.spinner.hidden,true);t.tick(1);assert.equal(t.spinner.hidden,false);
    t.frames.get('canvas').loaded();assert.equal(t.spinner.hidden,true);
    t.tick(301);assert.equal(t.writes.at(-1).id,'asset-manager');
    t.tick(5000);assert.equal(t.writes.length,2); // Never concurrent background loads.
    activate('gpt-chat');assert.equal(t.writes.at(-1).id,'gpt-chat'); // Click bypasses backlog.
    t.frames.get('gpt-chat').loaded();t.frames.get('asset-manager').loaded();
    t.doc.hidden=true;t.events.visibilitychange();t.tick(1000);assert.equal(t.writes.length,3);
    t.doc.hidden=false;t.events.visibilitychange();t.tick(301);assert.equal(t.writes.at(-1).id,'api-settings');
    const original=t.frames.get('canvas');activate('canvas');activate('asset-manager');activate('canvas');
    assert.equal(t.frames.get('canvas'),original);assert.equal(t.writes.filter(x=>x.id==='canvas').length,1);
    t.tick(150);assert.equal(t.spinner.hidden,true); // Warm switches never flash a loader.
}
const t=boot();t.ctx.WorkspaceLoading.activate('canvas');t.frames.get('canvas').loaded();
for(let i=0;i<9;i++){t.tick(301);t.frames.get(t.writes.at(-1).id).loaded();}
assert.deepEqual(t.writes.map(x=>x.id),['canvas','asset-manager','api-settings','online','gpt-chat','zimage','enhance','klein','angle','comfyui-settings']);
t.tick(5000);assert.equal(t.writes.length,10);
