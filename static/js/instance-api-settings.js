/* Explicit instances reuse the API settings page/layout, but not owner admin routes.
 * Keys exist only in the password input and one same-origin save request. */
(() => {
    'use strict';
    const endpoint = '/api/instance/providers';
    const byId = id => document.getElementById(id);
    const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    let providers = [], selected = null;
    const status = text => { byId('status').textContent = text; };
    document.querySelectorAll('.dx-os-banner,.api-link-btn,.cli-quick-group').forEach(el => el.remove());
    document.querySelectorAll('body > div:not(.page)').forEach(el => el.remove());
    const subtitle = document.querySelector('.page-head .sub');
    subtitle.removeAttribute('data-i18n');
    subtitle.textContent = '仅管理本实例个人 API。Key 写入私有存储，保存后不回显。保存和模型发现不会生成图片。';

    async function request(method, path = endpoint, body) {
        const response = await fetch(path, {method, headers:{'Content-Type':'application/json'},
            ...(body === undefined ? {} : {body: JSON.stringify(body)})});
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || '请求被拒绝');
        return data;
    }
    function list() {
        byId('providerList').replaceChildren();
        for (const p of providers) {
            const button = document.createElement('button');
            button.className = 'action-btn';
            button.textContent = `${p.name}${p.enabled ? '' : '（停用）'}`;
            button.onclick = () => { selected = p; render(); };
            byId('providerList').append(button);
        }
    }
    function render() {
        list();
        const p = selected;
        if (!p) { byId('settingsContent').innerHTML = '<section class="block">点击“新增平台”配置自己的 API。管理员提供的配置仍只读。</section>'; return; }
        byId('settingsContent').innerHTML = `
            <div class="content-head"><div class="editor-title">${escape(p.name || '新增平台')}</div>
            <div class="content-actions"><button id="ownDelete" class="action-btn danger-btn">删除</button><button id="ownSave" class="action-btn save-btn">保存</button></div></div>
            <section class="block"><div class="form">
            <label class="field full"><span class="label">显示名称</span><div class="field-frame"><input id="ownName" value="${escape(p.name)}" maxlength="100"></div></label>
            <label class="field full"><span class="label">Provider ID（仅本实例）</span><div class="field-frame"><input id="ownId" value="${escape(p.id)}" ${providers.some(v=>v.id===p.id) ? 'disabled' : ''}></div></label>
            <label class="field full"><span class="label">Base URL（公网 HTTPS；本地目标需管理员授权）</span><div class="field-frame"><input id="ownBase" value="${escape(p.base_url)}" placeholder="https://api.example.com/v1"></div></label>
            <label class="field full"><span class="label">协议</span><select id="ownProtocol">${['openai','gemini','kie'].map(v=>`<option value="${v}" ${v===p.protocol?'selected':''}>${v==='openai'?'OpenAI-compatible 文本':v==='gemini'?'Gemini 原生文本 / 图片理解':'Kie 图片'}</option>`).join('')}</select></label>
            <label class="field full"><span class="label">API Key · ${p.has_key?'已配置（不回显）':'未配置'}</span><div class="field-frame"><input id="ownKey" type="password" autocomplete="new-password" placeholder="留空保留；输入新值替换"></div></label>
            <label class="field"><input id="ownClear" type="checkbox">明确清除已保存的 Key</label>
            <label class="field"><input id="ownEnabled" type="checkbox" ${p.enabled?'checked':''}>启用 Provider</label>
            </div><p class="hint">更改地址或协议必须提供匹配的新 Key 或明确清除。运行中/状态未确认的任务会阻止配置修改。</p></section>
            <section class="block"><div class="block-head"><div class="block-title">模型与用途</div><button id="ownDiscover" class="action-btn">获取模型（先保存）</button></div>
            <p class="hint">每行：模型名称 | llm、image 或 video。OpenAI/Gemini 仅支持 llm；Kie 仅支持已适配图片模型。其他用途可保存但不可运行。</p>
            <textarea id="ownModels" rows="9" style="width:100%">${escape((p.models||[]).map(m=>`${m.id} | ${m.purpose}`).join('\n'))}</textarea>
            <div id="ownSupport" class="hint">${(p.models||[]).filter(m=>m.supported===false).map(m=>escape(m.id)+'：'+escape(m.support_note)).join('<br>')}</div></section>`;
        byId('ownSave').onclick = save;
        byId('ownDelete').onclick = remove;
        byId('ownDiscover').onclick = discover;
    }
    function body() {
        const result = {id: byId('ownId').value.trim(), name: byId('ownName').value.trim(),
            base_url: byId('ownBase').value.trim(), protocol: byId('ownProtocol').value,
            enabled: byId('ownEnabled').checked,
            models: byId('ownModels').value.split('\n').map(v=>v.trim()).filter(Boolean).map(v=>{
                const [id, purpose, extra] = v.split('|').map(s=>s.trim());
                if (!id || !purpose || extra !== undefined) throw new Error('模型格式应为：名称 | 用途');
                return {id, purpose};
            }), clear_key: byId('ownClear').checked};
        if (byId('ownKey').value) result.api_key = byId('ownKey').value;
        return result;
    }
    async function save() {
        const button = byId('ownSave'); button.disabled = true;
        try {
            const value = body();
            const data = await request('PUT', endpoint, value);
            providers = data.providers; selected = providers.find(p=>p.id===value.id);
            render(); status('已保存；未发起生成。画布下次加载模型列表时生效。');
            try { const channel = new BroadcastChannel('studio-api'); channel.postMessage({type:'providers-changed'}); channel.close(); } catch (_) { /* Selector reload also fetches current configuration. */ }
            window.parent.postMessage({type:'providers-changed'}, location.origin);
        } catch (e) { status(e.message); button.disabled = false; }
        finally { if(byId('ownKey')) byId('ownKey').value = ''; }
    }
    async function remove() {
        if (!selected || !confirm('删除本实例此 Provider 和个人 Key？')) return;
        try { providers = (await request('DELETE', endpoint, {id:selected.id})).providers; selected = providers[0]; render(); status('已删除'); }
        catch(e) { status(e.message); }
    }
    async function discover() {
        const button = byId('ownDiscover'); button.disabled = true;
        try {
            const data = await request('POST', endpoint+'/discover', {id:selected.id});
            const rows = byId('ownModels').value.split('\n').filter(Boolean);
            const ids = new Set(rows.map(v=>v.split('|')[0].trim()));
            data.models.forEach(m=>{if(!ids.has(m.id)){rows.push(`${m.id} | ${m.purpose}`);ids.add(m.id);}});
            byId('ownModels').value = rows.join('\n'); status(data.source+'；请保存模型变更。');
        } catch(e) { status(e.message); }
        finally { button.disabled = false; }
    }
    window.addProvider = () => { selected = {id:'personal-'+crypto.randomUUID().slice(0,8),name:'',base_url:'',protocol:'openai',enabled:true,models:[]};render();status(''); };
    request('GET').then(data=>{providers=data.providers;selected=providers[0];render();status('');}).catch(e=>status(e.message));
})();
