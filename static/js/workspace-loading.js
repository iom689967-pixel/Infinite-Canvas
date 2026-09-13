/* One iframe per page, one background navigation at a time. No API calls here. */
(() => {
    'use strict';
    const order = ['canvas', 'asset-manager', 'api-settings', 'online', 'gpt-chat',
        'zimage', 'enhance', 'klein', 'angle', 'comfyui-settings'];
    const states = new Map();
    let current = null, timer = null, idle = null, background = null, indicator = null;
    const gap = 300;
    const spinner = document.getElementById('workspace-opening');
    function cancelSchedule() {
        clearTimeout(timer); timer = null;
        if (idle !== null && window.cancelIdleCallback) window.cancelIdleCallback(idle);
        idle = null;
    }
    function showLoading() {
        clearTimeout(indicator);
        if (spinner) spinner.hidden = true;
        if (current?.status === 'loading') indicator = setTimeout(() => {
            if (spinner && current?.status === 'loading') spinner.hidden = false;
        }, 150);
    }
    function schedule() {
        cancelSchedule();
        if (document.hidden || background || !current || current.status !== 'ready') return;
        timer = setTimeout(() => {
            timer = null;
            const run = () => {
                idle = null;
                if (document.hidden || background || current.status !== 'ready') return;
                const next = order.map(id => states.get(id)).find(s => s && s.status === 'idle' &&
                    (s.id !== 'api-settings' || !window.InstanceSession || window.InstanceSession.can('manage_own_providers')));
                if (next) { background = next; load(next); }
            };
            if (window.requestIdleCallback) idle = window.requestIdleCallback(run, {timeout: 2000});
            else run();
        }, gap);
    }
    function load(state) {
        if (state.status !== 'idle') return;
        state.status = 'loading'; state.frame.dataset.loadState = 'loading';
        state.frame.setAttribute('aria-busy', 'true');
        if (!state.frame.src) state.frame.src = state.frame.dataset.src;
    }
    for (const id of order) {
        const frame = document.getElementById('frame-' + id);
        if (!frame) continue;
        const state = {id, frame, status: 'idle'}; states.set(id, state);
        frame.addEventListener('load', () => {
            if (state.status !== 'loading') return;
            try { if (frame.contentWindow.location.href === 'about:blank') return; } catch (_) {}
            state.status = 'ready'; frame.dataset.loadState = 'ready';
            frame.setAttribute('aria-busy', 'false');
            if (background === state) background = null;
            if (current === state) showLoading();
            schedule();
        });
    }
    document.addEventListener('visibilitychange', schedule);
    window.WorkspaceLoading = Object.freeze({
        activate(id) {
            cancelSchedule(); current = states.get(id);
            if (!current) return;
            for (const state of states.values()) state.frame.setAttribute('aria-hidden', String(state !== current));
            load(current); // Foreground navigation never waits for a background slot.
            showLoading(); schedule();
        }
    });
})();
