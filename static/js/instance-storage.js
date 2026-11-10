/* Synchronous, per-account storage before any shared page script runs. No migration of legacy content. */
(() => {
    'use strict';
    const node = document.getElementById('instance-context');
    if (!node) return;
    const context = JSON.parse(node.textContent);
    const prefix = `mio:${context.storage_namespace}:`;
    let active = true;
    const assertActive = () => { if (!active) throw new Error('Session storage is inactive'); };
    for (const name of ['localStorage', 'sessionStorage']) {
        const backing = window[name];
        const keys = () => Array.from({length: backing.length}, (_, i) => backing.key(i))
            .filter(key => key?.startsWith(prefix)).map(key => key.slice(prefix.length));
        const api = {
            getItem(key) { assertActive(); return backing.getItem(prefix + String(key)); },
            setItem(key, value) { assertActive(); backing.setItem(prefix + String(key), String(value)); },
            removeItem(key) { assertActive(); backing.removeItem(prefix + String(key)); },
            clear() { assertActive(); keys().forEach(key => backing.removeItem(prefix + key)); },
            key(index) { assertActive(); return keys()[index] ?? null; },
            get length() { assertActive(); return keys().length; }
        };
        const scoped = new Proxy(api, {
            get(target, key) { return key in target ? Reflect.get(target, key) : typeof key === 'string' ? target.getItem(key) ?? undefined : undefined; },
            set(target, key, value) { target.setItem(key, value); return true; },
            deleteProperty(target, key) { target.removeItem(key); return true; },
            ownKeys() { assertActive(); return keys(); },
            getOwnPropertyDescriptor(target, key) { return keys().includes(key) ? {enumerable: true, configurable: true, value: api.getItem(key)} : undefined; }
        });
        Object.defineProperty(window, name, {value: scoped, configurable: false});
    }
    // Existing same-account cross-tab notifications keep working; other accounts never receive them.
    if (window.BroadcastChannel) {
        const NativeChannel = window.BroadcastChannel;
        window.BroadcastChannel = class extends NativeChannel {
            constructor(name) { super(prefix + String(name)); }
        };
    }
    window.addEventListener('storage', event => {
        if (!event.isTrusted) return;
        event.stopImmediatePropagation();
        if (active && event.key?.startsWith(prefix)) {
            window.dispatchEvent(new StorageEvent('storage', {key: event.key.slice(prefix.length),
                oldValue: event.oldValue, newValue: event.newValue, url: event.url}));
        }
    }, true);
    window.InstanceStorage = Object.freeze({namespace: context.storage_namespace, deactivate() { active = false; }});
})();
