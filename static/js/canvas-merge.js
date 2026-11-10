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

    // 节点列表按 id 取并集（不判断删除，见审计 C8）：只在一方存在的节点保留，双方都有的交给 mergeNode。
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

    // 连线取并集，按 from->to:kind 去重，并丢掉指向不存在节点的连线。
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

    // ---------------- 普通画布 ----------------

    const classicConnectionKey = c => c?.id || (c ? `${c.from}->${c.to}` : '');
    const classicLogKey = l => l?.id || '';

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
                nodes.push({id:localNode.id, source:'local', node:baseNode ? threeWayMergeFields(localNode, remoteNode, baseNode) : {...localNode}});
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

    const api = {
        jsonEqual,
        threeWayMergeFields,
        mergeKeyedLists,
        mergeImageLists,
        mergeNodeListsUnion,
        mergeConnectionsUnion,
        mergeClassicCanvas,
        syncNodeFields,
        classicConnectionKey,
        classicLogKey,
    };
    if(typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.CanvasMerge = api;
})(typeof window !== 'undefined' ? window : globalThis);
