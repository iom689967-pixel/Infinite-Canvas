// 页面版本过期检测（后端已更新、页面没有刷新）：普通画布和智能画布共用。
// - 服务端在每个页面里注入 <meta name="ic-client-build">，保存画布时用 X-Client-Build 头回传；
//   构建号不一致时服务端返回 412 client_outdated，/meta 轮询也会带回当前构建号用来提前发现。
// - 过期后：停止保存，显示关不掉的横幅提示刷新；未保存的修改只记“相对上次同步内容的变化”，
//   存进本机浏览器（localStorage，实例模式下按账号隔离）；存不下时提供下载。
// - 刷新后：新页面发现草稿，提示“恢复到画布 / 丢弃”，恢复时合并到服务器上的最新版本再正常保存。
(function(root){
    const DRAFT_PREFIX = 'ic-outdated-draft:';
    const BANNER_ID = 'icOutdatedBanner';
    const RESTORE_ID = 'icOutdatedDraftRestore';
    const isEnglish = () => {
        try { return root.StudioI18n?.lang?.() === 'en'; } catch(e) { return false; }
    };
    const t = (zh, en) => (isEnglish() ? en : zh);

    function clientBuild(){
        try { return root.document?.querySelector('meta[name="ic-client-build"]')?.content || ''; } catch(e) { return ''; }
    }
    function withBuildHeader(headers={}){
        const build = clientBuild();
        return build ? {...headers, 'X-Client-Build':build} : {...headers};
    }
    function isOutdatedPayload(data){
        return data?.detail?.code === 'client_outdated' || data?.code === 'client_outdated';
    }
    function serverBuildDiffers(serverBuild){
        const build = clientBuild();
        return Boolean(build && serverBuild && String(serverBuild) !== build);
    }

    // 每个页面实例一个键（同一画布开了多个过期标签页时互不覆盖）：ic-outdated-draft:<canvasId>:<pageId>
    function draftKey(canvasId, pageId){ return `${DRAFT_PREFIX}${canvasId}:${pageId}`; }
    function saveDraft(canvasId, pageId, draft){
        try {
            root.localStorage.setItem(draftKey(canvasId, pageId), JSON.stringify(draft));
            return true;
        } catch(e) {
            return false;   // 超出配额或存储不可用：由横幅提供下载
        }
    }
    // 返回这张画布的所有草稿（按保存时间从早到晚），每项带上自己的 key，恢复或丢弃后按 key 删除
    function loadDrafts(canvasId){
        const prefix = `${DRAFT_PREFIX}${canvasId}:`;
        const drafts = [];
        try {
            const store = root.localStorage;
            for(let i = 0; i < store.length; i++){
                const key = store.key(i);
                if(!key || !key.startsWith(prefix)) continue;
                try {
                    const draft = JSON.parse(store.getItem(key) || 'null');
                    if(draft && draft.canvasId === canvasId && draft.patch) drafts.push({...draft, key});
                } catch(e) {}
            }
        } catch(e) {}
        return drafts.sort((a, b) => Number(a.savedAt || 0) - Number(b.savedAt || 0));
    }
    function clearDrafts(keys){
        (keys || []).forEach(key => { try { root.localStorage.removeItem(key); } catch(e) {} });
    }
    function downloadDraft(draft){
        const blob = new Blob([JSON.stringify(draft, null, 2)], {type:'application/json'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `canvas-${draft?.canvasId || 'unknown'}-unsaved-${new Date(draft?.savedAt || Date.now()).toISOString().replace(/[:.]/g, '-')}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    function button(label, onClick, primary=false){
        const el = document.createElement('button');
        el.type = 'button';
        el.textContent = label;
        Object.assign(el.style, {
            marginLeft:'10px', padding:'4px 12px', borderRadius:'6px', cursor:'pointer', font:'inherit',
            border:primary ? '1px solid #b45309' : '1px solid rgba(120,53,15,.35)',
            background:primary ? '#b45309' : 'rgba(255,255,255,.85)', color:primary ? '#fff' : '#78350f',
        });
        el.onclick = onClick;
        return el;
    }
    function panel(id, top){
        let el = document.getElementById(id);
        if(el) return el;
        el = document.createElement('div');
        el.id = id;
        el.setAttribute('role', 'alert');
        Object.assign(el.style, {
            position:'fixed', top, left:'50%', transform:'translateX(-50%)', zIndex:'100001',
            maxWidth:'min(760px, calc(100vw - 32px))', padding:'10px 14px', borderRadius:'10px',
            border:'1px solid rgba(180,83,9,.45)', background:'rgba(255,251,235,.98)', color:'#78350f',
            font:'500 13px/1.6 system-ui, -apple-system, sans-serif', boxShadow:'0 6px 20px rgba(0,0,0,.18)',
        });
        document.body.appendChild(el);
        return el;
    }

    // 过期横幅：没有关闭按钮，只能刷新。draftState: 'none' 没有未保存修改 / 'stored' 已暂存 / 'failed' 存不下
    function showOutdatedBanner({draftState='none', draft=null}={}){
        const el = panel(BANNER_ID, '12px');
        el.textContent = '';
        const text = document.createElement('span');
        const head = t('页面版本已过期：服务已经更新，这个页面还是旧代码。为避免覆盖数据，已停止保存。',
                       'This page is out of date: the server was updated. Saving is paused to protect your data.');
        const tail = draftState === 'stored'
            ? t('未保存的修改已暂存在本机浏览器，刷新后会提示恢复。', 'Unsaved changes were kept in this browser; you can restore them after refreshing.')
            : draftState === 'failed'
                ? t('未保存的修改太大，无法暂存在浏览器里，请先下载再刷新。', 'Unsaved changes are too large to keep in the browser. Download them before refreshing.')
                : t('没有未保存的修改。', 'There are no unsaved changes.');
        text.textContent = `${head}${tail}`;
        el.appendChild(text);
        if(draftState === 'failed' && draft) el.appendChild(button(t('下载未保存的修改', 'Download unsaved changes'), () => downloadDraft(draft)));
        el.appendChild(button(t('刷新页面', 'Refresh'), () => root.location.reload(), true));
        return el;
    }

    // 刷新后发现草稿：给出“恢复到画布 / 丢弃”。onRestore 返回 Promise<report>。
    function offerRestore({drafts, count, onRestore, onDiscard}){
        const el = panel(RESTORE_ID, '12px');
        el.textContent = '';
        const latest = (drafts || [])[Math.max(0, (drafts || []).length - 1)];
        const when = new Date(latest?.savedAt || Date.now()).toLocaleString();
        const text = document.createElement('span');
        text.textContent = t(`发现在旧版本页面里未保存的修改（${count} 处，${when}）。`, `Found ${count} unsaved change(s) from an outdated page (${when}).`);
        el.appendChild(text);
        el.appendChild(button(t('丢弃', 'Discard'), () => {
            if(!root.confirm(t('确定丢弃这些未保存的修改吗？丢弃后无法找回。', 'Discard these unsaved changes? This cannot be undone.'))) return;
            el.remove();
            onDiscard?.();
        }));
        el.appendChild(button(t('恢复到画布', 'Restore'), async () => {
            el.querySelectorAll('button').forEach(b => { b.disabled = true; });
            try {
                const report = await onRestore?.();
                el.remove();
                return report;
            } catch(e) {
                el.querySelectorAll('button').forEach(b => { b.disabled = false; });
                text.textContent = t(`恢复失败：${e?.message || e}`, `Restore failed: ${e?.message || e}`);
            }
        }, true));
        return el;
    }

    function restoreSummary(report){
        if(!report) return '';
        const parts = [];
        if(report.applied) parts.push(t(`已恢复 ${report.applied} 处`, `${report.applied} restored`));
        if(report.merged) parts.push(t(`${report.merged} 个节点与服务器上的修改合并`, `${report.merged} merged with server changes`));
        if(report.skippedDeletedOnServer) parts.push(t(`${report.skippedDeletedOnServer} 个节点已在别处删除，未恢复`, `${report.skippedDeletedOnServer} deleted elsewhere, skipped`));
        if(report.keptChangedOnServer) parts.push(t(`${report.keptChangedOnServer} 个节点在别处改过，保留未删`, `${report.keptChangedOnServer} changed elsewhere, kept`));
        if(report.connections) parts.push(t(`连线变化 ${report.connections} 处`, `${report.connections} connection change(s)`));
        return parts.join(t('；', '; '));
    }

    root.StalePage = Object.freeze({
        DRAFT_PREFIX, clientBuild, withBuildHeader, isOutdatedPayload, serverBuildDiffers,
        draftKey, saveDraft, loadDrafts, clearDrafts, downloadDraft, showOutdatedBanner, offerRestore, restoreSummary,
    });
})(typeof window !== 'undefined' ? window : globalThis);
