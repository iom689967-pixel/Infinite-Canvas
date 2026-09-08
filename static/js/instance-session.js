(() => {
    'use strict';
    const originalFetch = window.fetch.bind(window);
    let session = null;
    function loginRequired() { window.top.location.replace('/login'); }
    const ready = originalFetch('/api/auth/me', {credentials: 'same-origin', cache: 'no-store'})
        .then(async response => {
            if (!response.ok) { loginRequired(); throw new Error('请先登录'); }
            session = await response.json();
            return session;
        });
    // Session IDs are HttpOnly. CSRF stays only in this page's memory, never localStorage.
    window.fetch = async (input, options = {}) => {
        const url = new URL(input instanceof Request ? input.url : String(input), location.href);
        if (url.origin !== location.origin) return originalFetch(input, options);
        await ready;
        const method = String(options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
        const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
        if (!['GET', 'HEAD'].includes(method)) headers.set('X-CSRF-Token', session.csrf);
        const response = await originalFetch(input, {...options, headers, credentials: 'same-origin', cache: 'no-store'});
        if (response.status === 401) loginRequired();
        return response;
    };
    window.addEventListener('DOMContentLoaded', async () => {
        try {
            await ready;
            const identity = document.getElementById('instance-identity');
            if (identity) identity.textContent = `${session.username} · ${session.instance_id}`;
            const settings = document.getElementById('instance-api-settings');
            if (settings) settings.hidden = !session.permissions?.includes('manage_own_providers');
            document.getElementById('instance-logout')?.addEventListener('click', async () => {
                const response = await window.fetch('/api/auth/logout', {method: 'POST'});
                if (response.ok || response.status === 401) loginRequired();
            });
            document.querySelectorAll('[data-instance-page]').forEach(button => {
                button.addEventListener('click', () => {
                    document.getElementById('instance-frame').src = button.dataset.instancePage;
                });
            });
        } catch (_) { /* Redirect handled above; do not log credentials/session responses. */ }
    });
    // Back/forward cache must not resurrect a logged-out private page.
    window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
    setInterval(() => {
        originalFetch('/api/auth/me', {credentials: 'same-origin', cache: 'no-store'})
            .then(response => { if (response.status === 401) loginRequired(); }).catch(() => {});
    }, 15000);
})();
