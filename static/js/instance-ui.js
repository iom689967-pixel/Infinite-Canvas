/* Account controls and permission presentation only; never grants backend access. */
(() => {
    'use strict';
    const session = window.InstanceSession;
    if (!session) return;
    if(session.identity.public_beta && window === window.top) document.title='Mio Canvas';
    function hide(selector) { document.querySelectorAll(selector).forEach(el => el.setAttribute('data-instance-hidden', '')); }
    function deny(selector) {
        document.querySelectorAll(selector).forEach(el => {
            el.setAttribute('data-instance-denied', ''); el.setAttribute('aria-disabled', 'true');
            el.title = '当前实例未开放此功能';
            if ('disabled' in el) el.disabled = true;
        });
    }
    document.addEventListener('click', event => {
        if (event.target.closest?.('[data-instance-denied]')) { event.preventDefault(); event.stopImmediatePropagation(); }
    }, true);
    hide('#github-entry-btn,#update-now-btn,#project-update-modal,#project-update-confirm-modal');
    if (!session.can('manage_own_providers')) hide('[onclick*="api-settings"]');
    // Machine-local execution stays visibly unavailable, with the original navigation retained.
    const page = location.pathname.split('/').pop();
    if (['zimage.html','enhance.html','klein.html','angle.html','online.html'].includes(page)) {
        deny('#mainGenBtn,#genBtn,input[type="file"]');
        const button = document.querySelector('#mainGenBtn,#genBtn');
        if (button) {
            const notice = document.createElement('p'); notice.className = 'instance-feature-notice';
            notice.textContent = '当前实例未开放此页面的生成能力'; button.after(notice);
        }
    }
    hide('#storageSettingsBtn');
    if (!session.can('local_generation')) {
        deny('#engineSelect option:not([value="api"]),[onclick="menuAdd(\'msgen\')"],[onclick="menuAdd(\'comfy\')"]');
    }
    document.querySelector('[data-instance-machine-settings]')?.closest('.side-card')?.setAttribute('data-instance-hidden', '');
    const actions = document.querySelector('#studioSidebar .side-actions');
    if (actions) {
        const account = document.createElement('div'); account.className = 'instance-account';
        const identity = document.createElement('div'); identity.className = 'side-pill'; identity.id = 'instance-identity';
        const icon = document.createElement('span'); icon.setAttribute('aria-hidden', 'true');
        icon.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/></svg>';
        const label = document.createElement('span'); label.className = 'side-pill-text instance-account-name';
        label.textContent = session.identity.username; identity.title = session.identity.public_beta ? session.identity.username : `${session.identity.username} · ${session.identity.instance_id}`;
        identity.append(icon, label);
        const logout = document.createElement('button'); logout.id = 'instance-logout'; logout.type = 'button';
        logout.className = 'side-pill'; logout.title = '退出登录'; logout.setAttribute('aria-label', '退出登录');
        const arrow = document.createElement('span'); arrow.setAttribute('aria-hidden', 'true');
        arrow.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>';
        const text = document.createElement('span'); text.className = 'side-pill-text'; text.textContent = '退出登录';
        logout.append(arrow, text); logout.addEventListener('click', () => session.logout());
        account.append(identity, logout); actions.append(account);
        if(session.identity.public_beta){
            const storage=document.createElement('div');storage.className='instance-account';storage.id='instance-storage-usage';
            account.append(storage);
            session.ready.then(async()=>{
                const response=await fetch('/api/instance/storage');
                if(!response.ok) return;
                const usage=await response.json();
                storage.textContent=`存储 ${(usage.used_bytes/1024**2).toFixed(1)} MiB / ${(usage.quota_bytes/1024**3).toFixed(1)} GiB`;
            }).catch(()=>{});
        }
    }
})();
