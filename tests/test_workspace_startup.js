const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const flush = async () => { for(let i=0;i<30;i++) await Promise.resolve(); };
function boot(mode='cold') {
    let clock=0, next=0, currentMode=mode, release, centralFailures=mode==='gateway-retry'?2:0;
    const timers=new Map(), calls=[], redirects=[], events={}, connections=[];
    const identity={username:'alice',storage_namespace:'A',public_beta:true,capabilities:{manage_own_providers:true}};
    const elements=Object.fromEntries(['workspace-startup','workspace-startup-message','workspace-startup-retry','workspace-opening'].map(id=>[id,{hidden:true,dataset:{}}]));
    const frames=new Map();let navigations=0;
    frames.set('frame-canvas',{dataset:{src:'/static/canvas-list.html'},src:'',setAttribute(){},addEventListener(){}});
    Object.defineProperty(frames.get('frame-canvas'),'src',{get(){return this._src||'';},set(v){this._src=v;navigations++;}});
    const set=(fn,ms=0)=>{const id=++next;timers.set(id,{fn,at:clock+ms});return id;};
    class Socket {static CLOSED=3;static OPEN=1;constructor(){this.readyState=0;connections.push(this);}close(){this.readyState=3;}}
    const ctx={AbortController,DOMException,Response,Headers,Request,URL,WebSocket:Socket,Map,
        setTimeout:set,clearTimeout:id=>timers.delete(id),setInterval(){},addEventListener(t,f){events[t]=f;},removeEventListener(){},
        location:{origin:'https://canvas.test',href:'https://canvas.test/'},top:{location:{replace(p){redirects.push(p);}}},
        document:{hidden:false,documentElement:{style:{}},addEventListener(t,f){events[t]=f;},
            getElementById(id){return id==='instance-context'?{textContent:JSON.stringify(identity)}:elements[id]||frames.get(id);}},
        async fetch(url,options={}) {
            calls.push({url,options});
            if(url==='/api/beta/me') {
                if(centralFailures-->0)return new Response('{}',{status:503});
                return new Response(JSON.stringify({username:'alice',csrf:'central-test'}));
            }
            if(url==='/api/beta/enter') {
                assert.equal(options.headers['X-CSRF-Token'],'central-test');
                if(currentMode==='failed')return new Response('{}',{status:503});
                if(currentMode==='unauthorized')return new Response('{}',{status:401});
                if(currentMode==='cold') await new Promise((resolve,reject)=>{
                    release=resolve;options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
                });
                return new Response(JSON.stringify({session:{...identity,csrf:'worker-test',storage_namespace:currentMode==='mismatch'?'B':'A'}}));
            }
            return new Response('{}',{headers:{'X-Instance-Namespace':'A'}});
        }};
    ctx.window=ctx;ctx.parent=ctx;
    for(const name of ['workspace-startup','instance-session','workspace-loading','workspace-socket'])vm.runInNewContext(fs.readFileSync('static/js/'+name+'.js','utf8'),ctx);
    ctx.WorkspaceLoading.activate('canvas');
    const ws=new ctx.MioWorkspaceSocket('wss://canvas.test/ws/stats');
    return {ctx,calls,redirects,elements,frames,connections,ws,get navigations(){return navigations;},release(){release();},mode(value){currentMode=value;},failCentral(n){centralFailures=n;},
        async tick(ms){const end=clock+ms;for(;;){const first=[...timers].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!first)break;clock=first[1].at;timers.delete(first[0]);first[1].fn();await flush();}clock=end;await flush();}};
}
(async()=>{
    const cold=boot();await flush();
    const privateRead=cold.ctx.fetch('/api/canvases');
    await cold.ctx.fetch('/static/js/i18n.js'); // Shell program assets never wait for a cold worker.
    assert.equal(cold.navigations,1);assert.equal(cold.connections.length,0);
    await cold.tick(299);assert.equal(cold.elements['workspace-startup'].hidden,true);
    await cold.tick(1);assert.equal(cold.elements['workspace-startup'].hidden,false);
    assert.equal(cold.elements['workspace-startup-retry'].hidden,true);
    assert.equal(cold.calls.filter(x=>x.url==='/api/canvases').length,0);
    cold.ctx.WorkspaceStartup.start();cold.ctx.WorkspaceStartup.start();
    assert.equal(cold.calls.filter(x=>x.url==='/api/beta/enter').length,1);
    cold.release();await privateRead;await flush();
    assert.equal(cold.ctx.WorkspaceStartup.state,'ready');assert.equal(cold.navigations,1);
    assert.equal(cold.connections.length,1);assert.equal(cold.elements['workspace-startup'].hidden,true);
    assert.deepEqual(cold.redirects,[]);
    cold.ctx.WorkspaceLoading.activate('canvas');assert.equal(cold.navigations,1);
    const warm=boot('warm');await flush();await warm.tick(299);
    assert.equal(warm.ctx.WorkspaceStartup.state,'ready');assert.equal(warm.elements['workspace-startup'].hidden,true);
    assert.equal(warm.navigations,1);assert.deepEqual(warm.redirects,[]);
    const failed=boot('failed');await flush();
    assert.equal(failed.ctx.WorkspaceStartup.state,'failed');assert.equal(failed.elements['workspace-startup-retry'].hidden,false);
    await failed.tick(120000);assert.equal(failed.calls.filter(x=>x.url==='/api/beta/enter').length,1);
    assert.equal(failed.navigations,1);assert.equal(failed.connections.length,0);assert.deepEqual(failed.redirects,[]);
    failed.mode('warm');failed.elements['workspace-startup-retry'].onclick();await flush();
    assert.equal(failed.ctx.WorkspaceStartup.state,'ready');assert.equal(failed.navigations,1);assert.equal(failed.connections.length,1);
    const timeout=boot();await flush();await timeout.tick(60000);
    assert.equal(timeout.ctx.WorkspaceStartup.state,'failed');assert.equal(timeout.calls.filter(x=>x.url==='/api/beta/enter').length,1);
    assert.deepEqual(timeout.redirects,[]);
    const reconnect=boot('gateway-retry');await flush();
    assert.equal(reconnect.calls.length,1);await reconnect.tick(399);assert.equal(reconnect.calls.length,1);
    await reconnect.tick(1);assert.equal(reconnect.calls.length,2);await reconnect.tick(800);
    assert.equal(reconnect.ctx.WorkspaceStartup.state,'ready');
    assert.equal(reconnect.calls.filter(x=>x.url==='/api/beta/enter').length,1);
    const denied=boot('unauthorized');await flush();assert.deepEqual(denied.redirects,['/login']);assert.equal(denied.connections.length,0);
    const mismatch=boot('mismatch');await flush();assert.deepEqual(mismatch.redirects,['/login']);assert.equal(mismatch.connections.length,0);
    const logout=boot();await flush();await logout.ctx.InstanceSession.logout();await flush();
    assert.equal(logout.calls.at(-1).url,'/api/beta/logout');assert.deepEqual(logout.redirects,['/login']);assert.equal(logout.connections.length,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
