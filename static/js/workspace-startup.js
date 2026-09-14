/* Gateway-authenticated shell: one bounded handoff, no navigation or generation. */
(() => {
    'use strict';
    const identity = JSON.parse(document.getElementById('instance-context').textContent);
    const transport = window.fetch.bind(window);
    let state = 'starting', running = null, controller = null, csrf = null, shown = false;
    let resolveReady;
    // On failure, consumers stay dormant until an explicit retry succeeds. No
    // background polling continues and no queued application writes are retried.
    const ready = new Promise(resolve => { resolveReady = resolve; });
    function render() {
        const area = document.getElementById('workspace-startup');
        if (!area) return;
        area.hidden = state === 'ready' || state === 'cancelled' || (!shown && state === 'starting');
        area.dataset.state = state;
        const message = document.getElementById('workspace-startup-message');
        const retry = document.getElementById('workspace-startup-retry');
        message.hidden = retry.hidden = state !== 'failed';
        retry.onclick = () => start();
    }
    function loginRequired() {
        state = 'cancelled'; controller?.abort(); render();
        window.InstanceStorage?.deactivate();
        window.top.location.replace('/login');
    }
    async function gatewaySession(signal) {
        for (let attempt = 0; ; attempt++) {
            try {
                const response = await transport('/api/beta/me', {credentials: 'same-origin', cache: 'no-store', signal});
                if (response.status === 401) { loginRequired(); throw new Error('Session expired'); }
                if (response.ok) return response.json();
                if (response.status < 500) throw new Error('Session unavailable');
            } catch (error) {
                if (signal.aborted || state === 'cancelled' || attempt === 2) throw error;
            }
            if (attempt === 2) throw new Error('Session unavailable');
            await new Promise(resolve => setTimeout(resolve, 400 * (2 ** attempt)));
        }
    }
    async function handoff(signal) {
        const central = await gatewaySession(signal);
        if (central.username !== identity.username) { loginRequired(); return; }
        csrf = central.csrf;
        // The Supervisor already waits for verified worker health. This POST is
        // never automatically repeated on timeout/network failure.
        const response = await transport('/api/beta/enter', {method: 'POST', credentials: 'same-origin',
            cache: 'no-store', headers: {'X-CSRF-Token': csrf}, signal});
        if (response.status === 401) { loginRequired(); return; }
        if (!response.ok) throw new Error('Workspace unavailable');
        const current = (await response.json()).session;
        if (current?.storage_namespace !== identity.storage_namespace) { loginRequired(); return; }
        return current;
    }
    function start() {
        if (running || state === 'ready' || state === 'cancelled') return running;
        state = 'starting'; shown = false; render();
        controller = new AbortController();
        const signal = controller.signal;
        const indicator = setTimeout(() => { shown = true; render(); }, 300);
        const timeout = setTimeout(() => controller.abort(), 60000);
        running = (async () => {
            try {
                // Same-account tabs share cookies. Serialize handoff so the next
                // tab reuses the first session rather than rotating its CSRF.
                // Process uniqueness remains enforced by Supervisor, not this lock.
                const locks = window.navigator?.locks;
                const current = await (locks ? locks.request('mio-workspace:' + identity.storage_namespace,
                    {signal}, () => handoff(signal)) : handoff(signal));
                if (!current || signal.aborted || state === 'cancelled') return;
                state = 'ready'; resolveReady(current);
            } catch (_) {
                // Fixed UI text only: never expose upstream bodies, URLs or secrets.
                if (state !== 'cancelled') state = 'failed';
            } finally {
                clearTimeout(indicator); clearTimeout(timeout); running = null; render();
            }
        })();
        return running;
    }
    window.WorkspaceStartup = Object.freeze({ready, start, get state() { return state; },
        async logout() {
            state = 'cancelled'; controller?.abort(); render();
            if (!csrf) csrf = (await transport('/api/beta/me', {credentials: 'same-origin', cache: 'no-store'}).then(r => r.json())).csrf;
            const response = await transport('/api/beta/logout', {method: 'POST', credentials: 'same-origin',
                cache: 'no-store', headers: {'X-CSRF-Token': csrf}});
            if (!response.ok && response.status !== 401) throw new Error('Logout unavailable');
        }});
    document.addEventListener('DOMContentLoaded', render, {once: true});
    start();
})();
