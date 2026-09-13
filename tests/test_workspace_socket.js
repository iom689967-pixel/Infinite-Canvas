const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const connections = [], timers = new Map(), listeners = {};
let next = 0;
class Socket {
    static OPEN = 1; static CLOSED = 3;
    constructor(url) { this.url = url; this.readyState = 0; connections.push(this); }
    close() { this.readyState = 3; }
    send() {}
}
const window = {addEventListener(name, fn) {listeners[name] = fn;}, removeEventListener(name) {delete listeners[name];}};
vm.runInNewContext(fs.readFileSync('static/js/workspace-socket.js', 'utf8'), {
    window, WebSocket: Socket, setTimeout(fn, delay) {timers.set(++next, {fn, delay});return next;},
    clearTimeout(id) {timers.delete(id);}
});
const client = new window.MioWorkspaceSocket('ws://local/ws/stats');
let messages = 0; client.onmessage = () => messages++;
connections[0].onclose({code:1012});
assert.equal(timers.size, 1);
const [id, timer] = [...timers][0];assert.equal(timer.delay,500);timers.delete(id);timer.fn();
assert.equal(connections.length,2);
connections[1].onopen({});connections[1].onmessage({});assert.equal(messages,1);
connections[1].onclose({code:1008});assert.equal(timers.size,0);
const before=connections.length;listeners.pagehide();assert.equal(connections.length,before);
assert.equal(timers.size,0);
