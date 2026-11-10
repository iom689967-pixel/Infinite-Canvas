/* Shared authenticated transport for the official workspace and all embedded pages. */
(() => {
    'use strict';
    const node = document.getElementById('instance-context');
    if (!node) return;
    const identity = Object.freeze(JSON.parse(node.textContent));
    const originalFetch = window.fetch.bind(window);
    let session = null;
    let loggedOut = false;
    const channel = window.BroadcastChannel ? new BroadcastChannel('session') : null;
    function loginRequired(broadcast = false) {
        if (loggedOut) return;
        loggedOut = true;
        if (broadcast) channel?.postMessage({type: 'logout'});
        window.InstanceStorage?.deactivate();
        document.documentElement.style.visibility = 'hidden';
        window.top.location.replace('/login');
    }
    channel?.addEventListener('message', event => { if (event.data?.type === 'logout') loginRequired(); });
    const ready = originalFetch('/api/auth/me', {credentials: 'same-origin', cache: 'no-store'})
        .then(async response => {
            if (!response.ok) { loginRequired(); throw new Error('请先登录'); }
            const current = await response.json();
            if (current.storage_namespace !== identity.storage_namespace) {
                loginRequired(); throw new Error('登录身份已变化');
            }
            session = current;
            return current;
        });
    ready.catch(() => {});
    window.fetch = async (input, options = {}) => {
        const url = new URL(input instanceof Request ? input.url : String(input), location.href);
        if (url.origin !== location.origin) return originalFetch(input, options);
        await ready;
        if (loggedOut) throw new Error('请先登录');
        const method = String(options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
        const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
        if (!['GET', 'HEAD'].includes(method)) headers.set('X-CSRF-Token', session.csrf);
        const response = await originalFetch(input, {...options, headers, credentials: 'same-origin', cache: 'no-store'});
        const namespace = response.headers.get('X-Instance-Namespace');
        if (response.status === 401 || (namespace && namespace !== identity.storage_namespace)) {
            loginRequired(true); throw new Error('登录已失效或身份已变化');
        }
        return response;
    };
    window.InstanceSession = Object.freeze({identity, ready,
        can: capability => identity.capabilities[capability] === true,
        async logout() {
            try { await window.fetch('/api/auth/logout', {method: 'POST'}); }
            finally { loginRequired(true); }
        }});
    window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
    setInterval(() => {
        originalFetch('/api/auth/me', {credentials: 'same-origin', cache: 'no-store'})
            .then(async response => {
                if (response.status === 401) return loginRequired(true);
                if (response.ok && (await response.json()).storage_namespace !== identity.storage_namespace) loginRequired();
            }).catch(() => {});
    }, 15000);
})();
