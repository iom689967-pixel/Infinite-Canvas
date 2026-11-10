const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('static/js/instance-storage.js', 'utf8');
class Storage {
    constructor() { this.data = new Map(); }
    get length() { return this.data.size; }
    key(index) { return Array.from(this.data.keys())[index] ?? null; }
    getItem(k) { return this.data.get(k) ?? null; }
    setItem(k,v) { this.data.set(k, String(v)); }
    removeItem(k) { this.data.delete(k); }
}
const local = new Storage(), session = new Storage();
local.setItem('prompt_presets','LEGACY OWNER');
const boot = namespace => {
    const listeners = {};
    const window = {localStorage: local, sessionStorage: session,
        BroadcastChannel: class {constructor(name) {this.name = name;}},
        addEventListener(type, fn) {listeners[type] = fn;}, dispatchEvent(event) {this.lastEvent = event;}};
    vm.runInNewContext(source,{window, document:{getElementById:() => ({textContent:JSON.stringify({storage_namespace:namespace})})},
        StorageEvent:class {constructor(type, props){Object.assign(this,props);}}});
    return {window,listeners};
};
const a = boot('A'), b = boot('B');
assert.equal(a.window.localStorage.getItem('prompt_presets'),null);
a.window.localStorage.setItem('prompt_presets','A content');
a.window.localStorage.viewport='A viewport';
a.window.sessionStorage.setItem('canvas','A canvas');
assert.equal(b.window.localStorage.getItem('prompt_presets'),null);
assert.equal(b.window.sessionStorage.getItem('canvas'),null);
b.window.localStorage.setItem('prompt_presets','B content');
assert.equal(boot('A').window.localStorage.getItem('prompt_presets'),'A content');
assert.equal(a.window.localStorage.viewport,'A viewport');
assert.deepEqual(Object.keys(a.window.localStorage),['prompt_presets','viewport']);
a.window.localStorage.clear();
assert.equal(b.window.localStorage.getItem('prompt_presets'),'B content');
assert.equal(local.getItem('prompt_presets'),'LEGACY OWNER');
assert.notEqual(new a.window.BroadcastChannel('studio-api').name,new b.window.BroadcastChannel('studio-api').name);
a.listeners.storage({isTrusted:true,key:'mio:B:prompt_presets',stopImmediatePropagation(){}});
assert.equal(a.window.lastEvent,undefined);
a.listeners.storage({isTrusted:true,key:'mio:A:prompt_presets',newValue:'updated',stopImmediatePropagation(){}});
assert.equal(a.window.lastEvent.key,'prompt_presets');
a.window.InstanceStorage.deactivate();
assert.throws(() => a.window.localStorage.getItem('prompt_presets'));
assert.throws(() => a.window.sessionStorage.setItem('canvas','late write'));
console.log('Shared-origin storage isolation passed');
