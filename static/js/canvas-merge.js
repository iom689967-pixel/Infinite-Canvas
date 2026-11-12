// 画布同步的纯函数：三方合并（共同基线 base、本地 local、服务器 remote）。
// 普通画布（canvas.js）与智能画布（smart-canvas.js）共用。
// 这里的函数不访问 DOM、网络或页面全局状态，也不修改传入的对象，可在 Node 中单独测试
// （tests/test_canvas_merge.py）。合并规则与抽取前完全一致，页面里只保留把结果写回实时对象的那一步。
(function(root){
    'use strict';

    function jsonEqual(a, b){
        if(a === b) return true;
        try { return JSON.stringify(a) === JSON.stringify(b); } catch(e) { return false; }
    }

    // 忽略对象字段顺序的深比较（数组仍按顺序比较）。jsonEqual 用 JSON.stringify 比较，
    // 同样的内容只要字段顺序不同就算“不相等”；判断“相对基线没改过”时要用这个。
    function stableEqual(a, b){
        if(a === b) return true;
        if(typeof a !== typeof b || a === null || b === null || typeof a !== 'object') return false;
        if(Array.isArray(a) !== Array.isArray(b)) return false;
        if(Array.isArray(a)) return a.length === b.length && a.every((item, i) => stableEqual(item, b[i]));
        const keysA = Object.keys(a).filter(k => a[k] !== undefined);
        const keysB = Object.keys(b).filter(k => b[k] !== undefined);
        if(keysA.length !== keysB.length) return false;
        return keysA.every(k => Object.prototype.hasOwnProperty.call(b, k) && stableEqual(a[k], b[k]));
    }

    function hasOwn(obj, key){
        return Boolean(obj) && Object.prototype.hasOwnProperty.call(obj, key);
    }

    // 字段级三方合并：相对 base，本地改过的字段保留本地值；否则采用 remote 的值（remote 删掉的字段也删掉）。
    // 数组、对象字段按“整体一个值”比较，不做元素级合并。没有 base 时以 remote 为准（智能画布的旧行为）。
    // skipKeys 中的字段原样保留本地值，由调用方另行处理（智能画布的 images 取并集）。
    function threeWayMergeFields(local, remote, base, options={}){
        if(!base) return {...remote};
        const skip = new Set(options.skipKeys || []);
        const merged = {...local};
        const keys = new Set([...Object.keys(local || {}), ...Object.keys(remote || {}), ...Object.keys(base || {})]);
        keys.forEach(key => {
            if(skip.has(key)) return;
            if(!jsonEqual(local?.[key], base[key])) return;
            if(hasOwn(remote, key)) merged[key] = remote[key];
            else delete merged[key];
        });
        return merged;
    }

    // 按 key 合并列表：一方删除（base 有、该方没有）就删除；任一方新增就保留；双方都有时用本地对象。
    // 顺序：先按 remote 的顺序，再追加本地独有项。
    function mergeKeyedLists(localList, remoteList, baseList, keyOf){
        const baseKeys = new Set((baseList || []).map(keyOf).filter(Boolean));
        const localByKey = new Map((localList || []).map(item => [keyOf(item), item]));
        const remoteKeys = new Set((remoteList || []).map(keyOf).filter(Boolean));
        const out = [];
        const seen = new Set();
        (remoteList || []).forEach(item => {
            const key = keyOf(item);
            if(!key || seen.has(key)) return;
            if(baseKeys.has(key) && !localByKey.has(key)) return;
            seen.add(key);
            out.push(localByKey.get(key) || item);
        });
        (localList || []).forEach(item => {
            const key = keyOf(item);
            if(!key || seen.has(key)) return;
            if(baseKeys.has(key) && !remoteKeys.has(key)) return;
            seen.add(key);
            out.push(item);
        });
        return out;
    }

    // ---------------- 智能画布 ----------------

    // 图片按 url 取并集：先本地（保留本地无 url 的项），再追加 remote 独有的 url。
    function mergeImageLists(localImgs, remoteImgs){
        const out = [];
        const seen = new Set();
        (localImgs || []).forEach(img => {
            const u = img && img.url;
            if(u && seen.has(u)) return;
            if(u) seen.add(u);
            out.push(img);
        });
        (remoteImgs || []).forEach(img => {
            const u = img && img.url;
            if(!u || seen.has(u)) return;
            seen.add(u);
            out.push(img);
        });
        return out;
    }

    // 节点列表按 id 取并集，不判断删除：只在一方存在的节点保留，双方都有的交给 mergeNode。
    // 智能画布已改用 mergeNodeListsWithBase（C8）；这里保留并集版本，供没有基线时回退和差分测试对照。
    function mergeNodeListsUnion(localNodes, remoteNodes, baseNodes, mergeNode){
        const baseById = new Map((baseNodes || []).filter(n => n && n.id).map(n => [n.id, n]));
        const localById = new Map((localNodes || []).map(n => [n.id, n]));
        const remoteById = new Map((remoteNodes || []).map(n => [n.id, n]));
        const order = [];
        const seen = new Set();
        (localNodes || []).forEach(n => { if(!seen.has(n.id)){ seen.add(n.id); order.push(n.id); } });
        (remoteNodes || []).forEach(n => { if(!seen.has(n.id)){ seen.add(n.id); order.push(n.id); } });
        return order.map(id => {
            const local = localById.get(id);
            const remote = remoteById.get(id);
            if(local && !remote) return local;
            if(remote && !local) return remote;
            return mergeNode(local, remote, baseById.get(id) || null);
        }).filter(Boolean);
    }

    // 连线取并集，按 from->to:kind 去重，并丢掉指向不存在节点的连线（没有基线时的回退，见 mergeConnectionsWithBase）。
    function mergeConnectionsUnion(localConns, remoteConns, nodeIds){
        const out = [];
        const seen = new Set();
        [...(localConns || []), ...(remoteConns || [])].forEach(c => {
            if(!c || !nodeIds.has(c.from) || !nodeIds.has(c.to)) return;
            const key = `${c.from}->${c.to}:${c.kind || 'flow'}`;
            if(seen.has(key)) return;
            seen.add(key);
            out.push(c);
        });
        return out;
    }

    // ---------------- 按基线判断删除（C8） ----------------

    // 按 key 对两个列表做三方合并，逐元素处理：
    // - 两边都有：元素是对象且有基线时做字段级三方合并，否则用本地的元素；
    // - 只有一方有、基线里也有（另一方删掉了）：这一方相对基线没改过就删除；改过就保留（修改优先于删除）；
    // - 只有一方有、基线里没有：新增，保留。
    // 顺序：先按本地顺序，再追加对方独有的元素。没有 key 的元素原样保留本地的。
    // unchanged(item, baseItem) 判断“相对基线没改过”，keep(item) 为 true 时不删除（例如正在生成中的节点）。
    function mergeListWithBase(localList, remoteList, baseList, keyOf, options={}){
        const unchanged = options.unchanged || jsonEqual;
        const keep = options.keep || (() => false);
        const mergeItem = options.mergeItem || ((local, remote, base) => (
            base && local && remote && typeof local === 'object' && typeof remote === 'object' && !Array.isArray(local)
                ? threeWayMergeFields(local, remote, base) : local));
        const baseByKey = new Map();
        (baseList || []).forEach(item => { const key = keyOf(item); if(key && !baseByKey.has(key)) baseByKey.set(key, item); });
        const remoteByKey = new Map();
        (remoteList || []).forEach(item => { const key = keyOf(item); if(key && !remoteByKey.has(key)) remoteByKey.set(key, item); });
        const localKeys = new Set();
        const out = [];
        (localList || []).forEach(item => {
            const key = keyOf(item);
            if(!key){ out.push(item); return; }
            if(localKeys.has(key)) return;
            localKeys.add(key);
            const remote = remoteByKey.get(key);
            const base = baseByKey.get(key);
            if(remote !== undefined){ out.push(mergeItem(item, remote, base || null)); return; }
            if(base !== undefined && !keep(item) && unchanged(item, base)) return;   // 对方删了，本地没改
            out.push(item);
        });
        (remoteList || []).forEach(item => {
            const key = keyOf(item);
            if(!key || localKeys.has(key)) return;
            localKeys.add(key);
            const base = baseByKey.get(key);
            if(base !== undefined && !keep(item) && unchanged(item, base)) return;   // 本地删了，对方没改
            out.push(item);
        });
        return out;
    }

    // 智能画布节点：有基线时按基线判断删除；没有基线时退回并集（旧行为）。
    // 双方都有的节点交给 mergeNode；unchanged / keep 见 mergeListWithBase。
    function mergeNodeListsWithBase(localNodes, remoteNodes, baseNodes, mergeNode, options={}){
        if(!Array.isArray(baseNodes)) return mergeNodeListsUnion(localNodes, remoteNodes, baseNodes, mergeNode);
        const baseById = new Map(baseNodes.filter(n => n && n.id).map(n => [n.id, n]));
        return mergeListWithBase(localNodes, remoteNodes, baseNodes, n => n?.id || '', {
            unchanged:options.unchanged,
            keep:options.keep,
            mergeItem:(local, remote) => mergeNode(local, remote, baseById.get(local.id) || null),
        }).filter(Boolean);
    }

    const smartConnectionKey = c => (c ? `${c.from}->${c.to}:${c.kind || 'flow'}` : '');

    // 智能画布连线：有基线时按基线判断删除，否则取并集；最后丢掉指向不存在节点的连线。
    function mergeConnectionsWithBase(localConns, remoteConns, baseConns, nodeIds){
        if(!Array.isArray(baseConns)) return mergeConnectionsUnion(localConns, remoteConns, nodeIds);
        return mergeListWithBase(localConns, remoteConns, baseConns, smartConnectionKey)
            .filter(c => c && nodeIds.has(c.from) && nodeIds.has(c.to));
    }

    // ---------------- 普通画布：追加型数组字段 ----------------

    // 这些字段是“不断往后追加”的媒体列表，按 url 逐元素合并，而不是整个数组当一个值。
    const CLASSIC_APPEND_LIST_KEYS = ['images', 'generatedOutputs'];
    const mediaItemKey = item => (typeof item === 'string' ? item : (item && (item.url || item.path || item.src))) || '';

    function mergeMediaList(localList, remoteList, baseList){
        return mergeListWithBase(localList, remoteList, baseList, mediaItemKey);
    }

    // ---------------- 普通画布 ----------------

    const classicConnectionKey = c => c?.id || (c ? `${c.from}->${c.to}` : '');
    const classicLogKey = l => l?.id || '';

    // 普通画布单个节点：普通字段做字段级三方合并；images 这类追加型数组逐元素合并（C8）。
    function mergeClassicNode(localNode, remoteNode, baseNode){
        const merged = threeWayMergeFields(localNode, remoteNode, baseNode, {skipKeys:CLASSIC_APPEND_LIST_KEYS});
        CLASSIC_APPEND_LIST_KEYS.forEach(key => {
            const lists = [localNode, remoteNode, baseNode].map(n => n?.[key]);
            if(!lists.some(Array.isArray)){
                // 三方都不是数组：按普通字段处理（与旧行为相同）
                if(jsonEqual(localNode?.[key], baseNode?.[key])){
                    if(hasOwn(remoteNode, key)) merged[key] = remoteNode[key]; else delete merged[key];
                }
                return;
            }
            if(!hasOwn(localNode, key) && !hasOwn(remoteNode, key)){ delete merged[key]; return; }
            merged[key] = mergeMediaList(lists[0], lists[1], lists[2]);
        });
        return merged;
    }

    // 普通画布的整体合并。local.nodes 必须是序列化后的节点（不含运行时临时字段）。
    // 返回：
    //   nodes       有序列表，每项 {id, source:'local'|'remote', node}。source=local 时 node 是合并后的新对象，
    //               调用方用 syncNodeFields 写回实时节点；source=remote 时 node 就是 remote 的节点。
    //   connections / logs / title / icon  合并后的值（logs 按 createdAt 新到旧，最多 500 条）。
    // 规则：
    //   - 双方都有的节点：有基线时字段级三方合并；没有基线时保留本地。
    //   - 对方删除了节点：本地没改过就删除，本地改过就保留。
    //   - 本地删除了节点：对方没改过就保持删除，对方改过就恢复。
    //   - 连线、日志按 key 合并（见 mergeKeyedLists）；title/icon 本地没改过就跟随 remote。
    function mergeClassicCanvas(local, remote, base){
        const baseNodes = new Map((base?.nodes || []).map(n => [n.id, n]));
        const remoteNodes = Array.isArray(remote?.nodes) ? remote.nodes : [];
        const remoteById = new Map(remoteNodes.map(n => [n.id, n]));
        const localNodes = local?.nodes || [];
        const localIds = new Set(localNodes.map(n => n.id));
        const nodes = [];
        localNodes.forEach(localNode => {
            const remoteNode = remoteById.get(localNode.id);
            const baseNode = baseNodes.get(localNode.id);
            if(remoteNode){
                nodes.push({id:localNode.id, source:'local', node:baseNode ? mergeClassicNode(localNode, remoteNode, baseNode) : {...localNode}});
                return;
            }
            if(baseNode && jsonEqual(localNode, baseNode)) return;
            nodes.push({id:localNode.id, source:'local', node:{...localNode}});
        });
        remoteNodes.forEach(remoteNode => {
            if(localIds.has(remoteNode.id)) return;
            const baseNode = baseNodes.get(remoteNode.id);
            if(baseNode && jsonEqual(remoteNode, baseNode)) return;
            nodes.push({id:remoteNode.id, source:'remote', node:remoteNode});
        });
        const connections = mergeKeyedLists(local?.connections, remote?.connections, base?.connections, classicConnectionKey);
        const logs = mergeKeyedLists(local?.logs, remote?.logs, base?.logs, classicLogKey)
            .sort((a, b) => Number(b?.createdAt || 0) - Number(a?.createdAt || 0))
            .slice(0, 500);
        const title = local?.title === base?.title && remote?.title ? remote.title : local?.title;
        const icon = local?.icon === base?.icon && remote?.icon ? remote.icon : local?.icon;
        return {nodes, connections, logs, title, icon};
    }

    // 把合并结果写回实时节点对象（原地修改：运行中的任务、选中状态都持有它的引用）。
    // serialized 是该节点合并前的序列化形式；只动序列化字段，运行时临时字段保持不变。
    function syncNodeFields(live, serialized, merged){
        const keys = new Set([...Object.keys(serialized || {}), ...Object.keys(merged || {})]);
        keys.forEach(key => {
            if(hasOwn(merged, key)){
                if(live[key] !== merged[key]) live[key] = merged[key];
            } else {
                delete live[key];
            }
        });
        return live;
    }

    // ---------------- 过期页面的未保存修改（草稿） ----------------
    // 页面是旧代码、不能再保存时，只把“相对上次同步内容有变化的部分”记下来，体积小，能放进 localStorage。
    // comparable：比较前把节点规整成可比较的形式（智能画布要补默认字段）；默认原样比较。
    function buildDraftPatch({localNodes, baseNodes, localConnections, baseConnections, connectionKey, comparable}){
        const cmp = comparable || (node => node);
        const keyOf = connectionKey || (c => c?.id || (c ? `${c.from}->${c.to}` : ''));
        const baseList = Array.isArray(baseNodes) ? baseNodes : [];
        const baseById = new Map(baseList.filter(n => n && n.id).map(n => [n.id, n]));
        const localById = new Map((localNodes || []).filter(n => n && n.id).map(n => [n.id, n]));
        const changed = [];
        localById.forEach((local, id) => {
            const base = baseById.get(id);
            if(!base) changed.push({id, base:null, local});
            else if(!stableEqual(cmp(local), cmp(base))) changed.push({id, base, local});
        });
        const removed = [];
        baseById.forEach((base, id) => { if(!localById.has(id)) removed.push({id, base}); });
        const baseKeys = new Set((baseConnections || []).map(keyOf));
        const localKeys = new Set((localConnections || []).map(keyOf));
        return {
            version:1,
            changed,
            removed,
            addedConnections:(localConnections || []).filter(c => !baseKeys.has(keyOf(c))),
            removedConnections:(baseConnections || []).filter(c => !localKeys.has(keyOf(c))),
        };
    }

    function draftPatchCount(patch){
        if(!patch) return 0;
        return (patch.changed || []).length + (patch.removed || []).length
            + (patch.addedConnections || []).length + (patch.removedConnections || []).length;
    }

    // 把草稿合并到服务器上的最新版本：
    //   服务器上没动过的节点 → 用草稿里的版本；两边都改过 → mergeNode 按字段三方合并；
    //   服务器上已删除的节点 → 跳过（不复活）；草稿里删掉、但服务器上又改过的节点 → 保留服务器版本。
    function applyDraftPatch({serverNodes, serverConnections, patch, mergeNode, connectionKey, comparable}){
        const cmp = comparable || (node => node);
        const keyOf = connectionKey || (c => c?.id || (c ? `${c.from}->${c.to}` : ''));
        const nodes = (serverNodes || []).slice();
        const indexOf = id => nodes.findIndex(n => n && n.id === id);
        const report = {applied:0, merged:0, skippedDeletedOnServer:0, keptChangedOnServer:0, connections:0};
        (patch?.changed || []).forEach(({id, base, local}) => {
            const index = indexOf(id);
            if(index < 0){
                if(base){ report.skippedDeletedOnServer += 1; return; }
                nodes.push(local);
                report.applied += 1;
                return;
            }
            const server = nodes[index];
            if(base && stableEqual(cmp(server), cmp(base))){
                nodes[index] = local;
                report.applied += 1;
            } else {
                nodes[index] = mergeNode ? mergeNode(local, server, base || null) : local;
                report.merged += 1;
            }
        });
        (patch?.removed || []).forEach(({id, base}) => {
            const index = indexOf(id);
            if(index < 0) return;
            if(stableEqual(cmp(nodes[index]), cmp(base))){
                nodes.splice(index, 1);
                report.applied += 1;
            } else {
                report.keptChangedOnServer += 1;
            }
        });
        const nodeIds = new Set(nodes.map(n => n && n.id));
        const removedKeys = new Set((patch?.removedConnections || []).map(keyOf));
        const connections = (serverConnections || []).filter(c => !removedKeys.has(keyOf(c)));
        report.connections += (serverConnections || []).length - connections.length;
        const present = new Set(connections.map(keyOf));
        (patch?.addedConnections || []).forEach(c => {
            if(present.has(keyOf(c))) return;
            connections.push(c);
            present.add(keyOf(c));
            report.connections += 1;
        });
        return {nodes, connections:connections.filter(c => c && nodeIds.has(c.from) && nodeIds.has(c.to)), report};
    }

    const api = {
        jsonEqual,
        stableEqual,
        threeWayMergeFields,
        mergeKeyedLists,
        mergeImageLists,
        mergeNodeListsUnion,
        mergeConnectionsUnion,
        mergeClassicCanvas,
        mergeClassicNode,
        mergeListWithBase,
        mergeMediaList,
        mergeNodeListsWithBase,
        mergeConnectionsWithBase,
        syncNodeFields,
        CLASSIC_APPEND_LIST_KEYS,
        classicConnectionKey,
        classicLogKey,
        smartConnectionKey,
        buildDraftPatch,
        applyDraftPatch,
        draftPatchCount,
    };
    if(typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.CanvasMerge = api;
})(typeof window !== 'undefined' ? window : globalThis);
