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
    session.ready.then(() => {
        if (!session.can('manage_own_providers')) hide('[onclick*="api-settings"]');
    }).catch(() => {});
    if(['canvas.html','smart-canvas.html'].includes(location.pathname.split('/').pop())){
        async function refreshPersonalRh(){
            const response=await fetch('/api/config');if(!response.ok)return;
            const config=await response.json();
            const providers=(config.api_providers||[]).filter(p=>p.personal && p.protocol==='runninghub' && p.enabled);
            document.querySelector('[data-personal-rh-picker]')?.remove();
            delete document.documentElement.dataset.personalRhProviderId;
            if(!providers.length)return;
            document.documentElement.dataset.personalRhProviderId=providers.find(p=>p.id===sessionStorage.getItem('personalRhProviderId'))?.id||providers[0].id;
            if(providers.length>1){
                const label=document.createElement('label');label.dataset.personalRhPicker='';label.className='instance-feature-notice';label.textContent='RunningHub 平台 ';
                const select=document.createElement('select');
                for(const p of providers){const option=document.createElement('option');option.value=p.id;option.textContent=p.name||p.id;select.append(option);}
                select.value=document.documentElement.dataset.personalRhProviderId;select.onchange=()=>{sessionStorage.setItem('personalRhProviderId',select.value);document.documentElement.dataset.personalRhProviderId=select.value;};
                label.append(select);document.body.prepend(label);
            }
        }
        session.ready.then(refreshPersonalRh).catch(()=>{});
        window.addEventListener('message',event=>{if(event.origin===location.origin && event.data?.type==='providers-changed')refreshPersonalRh().catch(()=>{});});
        try{const channel=new BroadcastChannel('studio-api');channel.onmessage=event=>{if(event.data?.type==='providers-changed')refreshPersonalRh().catch(()=>{});};}catch(_){}
    }
    // Machine-local execution stays visibly unavailable, with the original navigation retained.
    const page = location.pathname.split('/').pop();
    if (page === 'canvas-list.html') {
        // An early framework is not evidence of an empty account. Keep only the
        // program layout visible until its own authenticated session is ready.
        document.documentElement.setAttribute('data-instance-loading', '');
        session.ready.then(() => document.documentElement.removeAttribute('data-instance-loading')).catch(() => {});
    }
    if (['enhance.html','klein.html'].includes(page)) {
        deny('#mainGenBtn,#genBtn,input[type="file"]');
        const button = document.querySelector('#mainGenBtn,#genBtn');
        if (button) {
            const notice = document.createElement('p'); notice.className = 'instance-feature-notice';
            notice.textContent = '当前实例未开放此页面的生成能力'; button.after(notice);
        }
    }
    if(['zimage.html','angle.html'].includes(page)){
        deny('#modeLocal');
        const button=document.querySelector('#mainGenBtn,#genBtn');
        const panel=document.createElement('div');panel.className='instance-feature-notice';
        const label=document.createElement('p');label.textContent='使用个人 ModelScope API 模型；模型 ID 原样提交';
        const picker=document.createElement('select');picker.setAttribute('aria-label','个人 ModelScope 模型');panel.append(label,picker);button?.before(panel);
        let choices=[];
        window.PersonalModelscopeSelection={get(){const selected=choices.find(c=>c.value===picker.value);if(!selected)throw new Error(label.textContent);return {provider_id:selected.provider_id,model:selected.model};}};
        async function refreshPersonalMs(){
            try{
                const response=await fetch('/api/config');if(!response.ok)throw new Error();const config=await response.json();
                choices=(config.api_providers||[]).flatMap(p=>(p.image_models||[]).map(model=>({value:p.id+'|'+model,provider_id:p.id,model,label:(p.name||p.id)+' · '+model,cap:p.capabilities?.image?.[model]}))).filter(c=>c.cap?.adapter==='modelscope-async');
                const old=picker.value;picker.replaceChildren();
                choices.forEach(c=>{const option=document.createElement('option');option.value=c.value;option.textContent=c.label+(c.cap.executable?'':' · '+c.cap.reason);option.disabled=!c.cap.executable;picker.append(option);});
                if(choices.some(c=>c.value===old))picker.value=old;
                if(!choices.length){label.textContent='尚未配置个人 ModelScope 模型；请在 API 设置选择 ModelScope 异步适配器';}
                else label.textContent='使用个人 ModelScope API 模型；模型 ID 原样提交';
                if(typeof window.switchEngine==='function')window.switchEngine('cloud');
                if(button)button.disabled=!choices.some(c=>c.cap.executable);
            }catch{label.textContent='模型配置加载失败，请刷新或重新登录';if(button)button.disabled=true;}
        }
        session.ready.then(refreshPersonalMs).catch(()=>{});
        window.addEventListener('message',event=>{if(event.origin===location.origin && event.data?.type==='providers-changed')void refreshPersonalMs();});
        try{new BroadcastChannel('studio-api').addEventListener('message',event=>{if(event.data?.type==='providers-changed')void refreshPersonalMs();});}catch{}
    }
    hide('#storageSettingsBtn');
    if (!session.can('local_generation')) {
        deny('#engineSelect option[value="comfy"],[onclick="menuAdd(\'comfy\')"]');
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
