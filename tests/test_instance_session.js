// Actual shared transport: CSRF, identity changes, logout and cancellation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/instance-session.js', 'utf8');
function boot({delayAuth=false, parent=null}={}) {
    const calls = [], redirects = [], rejected = [];
    const identity = {storage_namespace:'A', username:'alice', capabilities:{manage_own_providers:true}};
    let next = () => new Response('{}', {headers:{'X-Instance-Namespace':'A'}});
    let releaseAuth;
    const authGate = new Promise(resolve => {releaseAuth=resolve;});
    const context = {URL, Request, Headers, Response, Error, parent,
        CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail}},
        dispatchEvent(event){rejected.push({...event.detail,storageActive:context.InstanceStorage.active});}, setInterval() {}, addEventListener() {},
        location:{href:'https://canvas.test/', origin:'https://canvas.test'},
        document:{getElementById:() => ({textContent:JSON.stringify(identity)}), documentElement:{style:{}}},
        InstanceStorage:{active:true, deactivate() { this.active = false; }},
        top:{location:{replace(path) { redirects.push(path); }}},
        async fetch(input, options) {
            calls.push({input, options});
            if (input === '/api/auth/me') {
                if(delayAuth) await authGate;
                return new Response(JSON.stringify({...identity, csrf:'fake-csrf'}));
            }
            return next();
        }};
    context.window = context; vm.runInNewContext(source, context);
    return {context, calls, redirects, rejected, releaseAuth, respond: fn => { next = fn; }};
}
(async () => {
    const a = boot(); await a.context.InstanceSession.ready;
    const abort = new AbortController();
    await a.context.fetch('/api/canvases', {method:'PUT', body:'{}', signal:abort.signal});
    assert.equal(a.calls.at(-1).options.headers.get('X-CSRF-Token'), 'fake-csrf');
    assert.equal(a.calls.at(-1).options.signal, abort.signal);
    assert.equal(a.calls.at(-1).options.credentials, 'same-origin');
    await a.context.fetch('https://outside.test/public', {});
    assert.equal(a.calls.at(-1).options.headers, undefined);
    a.respond(() => new Response('{"private":"B"}', {headers:{'X-Instance-Namespace':'B'}}));
    await assert.rejects(a.context.fetch('/api/canvases'));
    assert.deepEqual(a.redirects, ['/login']); assert.equal(a.context.InstanceStorage.active, false);
    const count = a.calls.length; await assert.rejects(a.context.fetch('/api/canvases'));
    assert.equal(a.calls.length, count);
    const b = boot(); await b.context.InstanceSession.ready;
    b.respond(() => new Response('{}', {status:401})); await assert.rejects(b.context.fetch('/api/canvases'));
    assert.deepEqual(b.redirects, ['/login']);
    assert.equal(b.context.InstanceSession.active,false);
    assert.deepEqual(b.rejected,[{method:'GET',path:'/api/canvases',status:401,storageActive:true}]);
    const c = boot(); await c.context.InstanceSession.ready; await c.context.InstanceSession.logout();
    assert.equal(c.calls.at(-1).input, '/api/auth/logout');
    assert.equal(c.calls.at(-1).options.headers.get('X-CSRF-Token'), 'fake-csrf');
    assert.deepEqual(c.redirects, ['/login']);
    const d = boot({delayAuth:true});
    await d.context.fetch('/api/canvases'); // GET must resolve before auth bootstrap.
    await d.context.fetch('/api/canvases', {method:'HEAD'});
    const write=d.context.fetch('/api/canvases', {method:'POST'});
    await Promise.resolve();assert.equal(d.calls.filter(c=>c.options.method==='POST').length,0);
    d.releaseAuth();await write;assert.equal(d.calls.at(-1).options.headers.get('X-CSRF-Token'),'fake-csrf');
    await d.context.fetch('/static/js/theme.js?v=valid');assert.equal(d.calls.at(-1).options.cache,'default');
    await d.context.fetch('/assets/private.js');assert.equal(d.calls.at(-1).options.cache,'no-store');
    const child=boot({parent:d.context});await child.context.InstanceSession.ready;
    assert.equal(child.calls.length,0);await child.context.fetch('/api/canvases',{method:'POST'});
    assert.equal(child.calls[0].options.headers.get('X-CSRF-Token'),'fake-csrf');
    const early=boot({delayAuth:true});early.respond(()=>new Response('{}',{status:401}));
    await assert.rejects(early.context.fetch('/api/canvases'));assert.deepEqual(early.redirects,['/login']);
    early.releaseAuth();await early.context.InstanceSession.ready;
    await assert.rejects(early.context.fetch('/api/canvases',{method:'POST'}));
    const pending=boot({delayAuth:true});pending.context.WorkspaceStartup={state:'starting'};
    const canvas=boot({parent:pending.context});const privateRead=canvas.context.fetch('/api/canvases');
    await Promise.resolve();assert.equal(canvas.calls.length,0);
    await canvas.context.fetch('/static/js/i18n.js');assert.equal(canvas.calls.length,1);
    pending.context.WorkspaceStartup.state='ready';pending.releaseAuth();await privateRead;
    assert.equal(canvas.calls.at(-1).input,'/api/canvases');
    const root=boot();await root.context.InstanceSession.ready;
    const left=boot({parent:root.context}),right=boot({parent:root.context});
    await Promise.all([left.context.InstanceSession.ready,right.context.InstanceSession.ready]);
    left.context.InstanceSession.invalidate(true);right.context.InstanceSession.invalidate(true);
    assert.deepEqual(root.redirects,['/login']);assert.deepEqual(left.redirects,[]);assert.deepEqual(right.redirects,[]);
})().catch(error => { console.error(error); process.exitCode = 1; });
