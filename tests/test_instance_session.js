// Actual shared transport: CSRF, identity changes, logout and cancellation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/instance-session.js', 'utf8');
function boot() {
    const calls = [], redirects = [];
    const identity = {storage_namespace:'A', username:'alice', capabilities:{manage_own_providers:true}};
    let next = () => new Response('{}', {headers:{'X-Instance-Namespace':'A'}});
    const context = {URL, Request, Headers, Response, Error, setInterval() {}, addEventListener() {},
        location:{href:'https://canvas.test/', origin:'https://canvas.test'},
        document:{getElementById:() => ({textContent:JSON.stringify(identity)}), documentElement:{style:{}}},
        InstanceStorage:{active:true, deactivate() { this.active = false; }},
        top:{location:{replace(path) { redirects.push(path); }}},
        async fetch(input, options) {
            calls.push({input, options});
            if (input === '/api/auth/me') return new Response(JSON.stringify({...identity, csrf:'fake-csrf'}));
            return next();
        }};
    context.window = context; vm.runInNewContext(source, context);
    return {context, calls, redirects, respond: fn => { next = fn; }};
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
    const c = boot(); await c.context.InstanceSession.ready; await c.context.InstanceSession.logout();
    assert.equal(c.calls.at(-1).input, '/api/auth/logout');
    assert.equal(c.calls.at(-1).options.headers.get('X-CSRF-Token'), 'fake-csrf');
    assert.deepEqual(c.redirects, ['/login']);
})().catch(error => { console.error(error); process.exitCode = 1; });
