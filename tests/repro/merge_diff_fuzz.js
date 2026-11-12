// 差分比对：把某个历史提交里画布页面内的旧合并实现，与 static/js/canvas-merge.js 的纯函数在随机输入上逐一比对。
// 用来证明“把合并规则抽成纯函数”没有改变行为。默认对照 1dadb8a（抽取前的最后一个提交）。
//
//   node tests/repro/merge_diff_fuzz.js [--ref <git ref>] [--cases 2000] [--seed 12345]
//
// 不需要服务实例；只读取 git 历史和当前工作区文件。
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..', '..');
const arg = (name, fallback) => { const i = process.argv.indexOf(`--${name}`); return i > 0 ? process.argv[i + 1] : fallback; };
const REF = arg('ref', '1dadb8a');
const N = Number(arg('cases', 2000));
let seed = Number(arg('seed', 12345));

const M = require(path.join(ROOT, 'static/js/canvas-merge.js'));
const gitShow = file => execFileSync('git', ['-C', ROOT, 'show', `${REF}:${file}`], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
const oldCanvas = gitShow('static/js/canvas.js');
const oldSmart = gitShow('static/js/smart-canvas.js');
const cur = fs.readFileSync(path.join(ROOT, 'static/js/canvas.js'), 'utf8');

function fnSource(src, name) {
  const start = src.search(new RegExp(`(?:async )?function ${name}\\(`));
  if (start < 0) throw new Error(`function ${name} not found (does ${REF} still contain the in-page merge code?)`);
  const tail = src.slice(start);
  const end = tail.slice(1).search(/\n(?:async )?function /);
  return end >= 0 ? tail.slice(0, end + 1) : tail;
}

// 旧的普通画布合并：在带最小桩的上下文里运行原函数（它直接改写页面全局状态）
const OldClassic = new Function(`
  let nodes = [], connections = [], canvas = null, canvasSyncBaseJson = '', lastCanvasUpdatedAt = 0, selected = new Set();
  const currentCanvasTitle = null; function render(){} function sanitizeConnections(){} function tr(k){return k;}
  function resetTransientRunState(list){ (list||[]).forEach(n => { if(n && n.running) n.running = false; }); }
  ${fnSource(cur, 'serializableCanvasNode')}
  ${fnSource(cur, 'canvasSyncSnapshot')}
  ${fnSource(oldCanvas, 'canvasJsonEqual')}
  ${fnSource(oldCanvas, 'mergeCanvasNodeFieldsInPlace')}
  ${fnSource(oldCanvas, 'mergeKeyedCanvasLists')}
  ${fnSource(oldCanvas, 'mergeRemoteCanvasIntoLocal')}
  return function(local, remote, baseJson){
    nodes = local.nodes; connections = local.connections; canvas = {id:'c', title:local.title, icon:local.icon, logs:local.logs};
    canvasSyncBaseJson = baseJson;
    mergeRemoteCanvasIntoLocal(remote);
    return {nodes, connections, logs:canvas.logs, title:canvas.title, icon:canvas.icon};
  };`)();
const OldSmart = new Function(`
  ${fnSource(oldSmart, 'mergeSmartImageLists')}
  function smartJsonEqual(a,b){ if(a===b) return true; try { return JSON.stringify(a)===JSON.stringify(b); } catch(e){ return false; } }
  ${fnSource(oldSmart, 'threeWayMergeSmartFields')}
  ${fnSource(oldSmart, 'mergeSmartNodeLists').replace('mergeSmartNode(local, remote, baseById.get(id) || null)', '__mergeNode(local, remote, baseById.get(id) || null)')}
  ${fnSource(oldSmart, 'mergeSmartConnections')}
  let __mergeNode = null;
  return {mergeSmartImageLists, threeWayMergeSmartFields, mergeSmartConnections,
    mergeSmartNodeLists(l, r, b, fn){ __mergeNode = fn; return mergeSmartNodeLists(l, r, b); }};`)();

// 新实现：与当前 canvas.js 的 mergeRemoteCanvasIntoLocal 相同的包装
const serializable = new Function(`${fnSource(cur, 'serializableCanvasNode')}; return serializableCanvasNode;`)();
const snapshot = new Function('serializableCanvasNode', `${fnSource(cur, 'canvasSyncSnapshot')}; return canvasSyncSnapshot;`)(serializable);
function newClassic(local, remote, baseJson) {
  let base = baseJson ? JSON.parse(baseJson) : null;
  const nextBaseJson = snapshot(remote);
  if (!base) base = JSON.parse(nextBaseJson);
  const liveById = new Map(local.nodes.map(n => [n.id, n]));
  const serById = new Map(local.nodes.map(n => [n.id, serializable(n)]));
  const merged = M.mergeClassicCanvas({ title: local.title, icon: local.icon, nodes: local.nodes.map(n => serById.get(n.id)), connections: local.connections, logs: local.logs }, remote, base);
  const nodes = merged.nodes.map(e => {
    if (e.source === 'remote') { if (e.node.running) e.node.running = false; return e.node; }
    return M.syncNodeFields(liveById.get(e.id), serById.get(e.id), e.node);
  });
  return { nodes, connections: merged.connections, logs: merged.logs, title: merged.title, icon: merged.icon };
}

// 随机输入
const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
const pick = arr => arr[Math.floor(rnd() * arr.length)];
const vals = [0, 1, 2, 'a', 'b', [1], [1, 2], { k: 1 }, { k: 2 }, null];
function mutateNode(n) {
  const c = JSON.parse(JSON.stringify(n)); const r = rnd(); const key = pick(['x', 'y', 'text', 'tags', 'meta', 'extra']);
  if (r < 0.35) c[key] = pick(vals); else if (r < 0.5) delete c[key];
  return c;
}
function randomCanvas() {
  const nodes = ['a', 'b', 'c', 'd', 'e', 'f'].filter(() => rnd() < 0.8).map(id => ({ id, type: 'prompt', x: Math.floor(rnd() * 3), text: pick(['p', 'q']), tags: pick([[1], [1, 2]]) }));
  return { title: pick(['T', 'U']), icon: pick(['i', 'j']), nodes,
    connections: nodes.slice(1).filter(() => rnd() < 0.5).map((n, i) => ({ id: 'e' + n.id, from: nodes[i].id, to: n.id })),
    logs: Array.from({ length: Math.floor(rnd() * 4) }, (_, i) => ({ id: 'l' + i + pick(['', 'x']), createdAt: Math.floor(rnd() * 10) })) };
}
function derive(base) {
  const c = JSON.parse(JSON.stringify(base));
  c.nodes = c.nodes.filter(() => rnd() > 0.15).map(n => (rnd() < 0.6 ? mutateNode(n) : n));
  if (rnd() < 0.3) c.nodes.push({ id: 'new' + Math.floor(rnd() * 3), type: 'prompt', x: 1 });
  if (rnd() < 0.3) c.title = pick(['T', 'U', 'V']);
  c.connections = c.connections.filter(() => rnd() > 0.2);
  if (rnd() < 0.3) c.connections.push({ id: 'en' + Math.floor(rnd() * 3), from: 'a', to: 'b' });
  c.logs = c.logs.filter(() => rnd() > 0.2);
  if (rnd() < 0.4) c.logs.push({ id: 'ln' + Math.floor(rnd() * 3), createdAt: Math.floor(rnd() * 10) });
  return c;
}

let classicMismatch = 0, smartMismatch = 0, firstDiff = null;
for (let i = 0; i < N; i++) {
  const base = randomCanvas(); const local = derive(base); const remote = { ...derive(base), id: 'c', updated_at: 5 };
  if (rnd() < 0.2) local.nodes.forEach(n => { if (rnd() < 0.5) n.running = true; });
  const baseJson = rnd() < 0.1 ? '' : JSON.stringify({ title: base.title, icon: base.icon, nodes: base.nodes, connections: base.connections, logs: base.logs });
  const a = OldClassic(JSON.parse(JSON.stringify(local)), JSON.parse(JSON.stringify(remote)), baseJson);
  const b = newClassic(JSON.parse(JSON.stringify(local)), JSON.parse(JSON.stringify(remote)), baseJson);
  if (JSON.stringify(a) !== JSON.stringify(b)) { classicMismatch++; if (!firstDiff) firstDiff = { case: i, old: a, new: b }; }
  const ln = local.nodes[0] || { id: 'z' }, rn = remote.nodes[0] || { id: 'z' }, bn = rnd() < 0.15 ? null : (base.nodes[0] || { id: 'z' });
  const pairs = [
    [OldSmart.threeWayMergeSmartFields({ ...ln, images: [{ url: '1' }] }, { ...rn, images: [{ url: '2' }] }, bn && { ...bn, images: [] }),
      M.threeWayMergeFields({ ...ln, images: [{ url: '1' }] }, { ...rn, images: [{ url: '2' }] }, bn && { ...bn, images: [] }, { skipKeys: ['images'] })],
    [OldSmart.mergeSmartImageLists(local.nodes.map(n => ({ url: n.id })), remote.nodes.map(n => ({ url: n.id }))), M.mergeImageLists(local.nodes.map(n => ({ url: n.id })), remote.nodes.map(n => ({ url: n.id })))],
    [OldSmart.mergeSmartNodeLists(local.nodes, remote.nodes, base.nodes, (l, r, b2) => ({ l: l.id, r: r.id, b: b2 && b2.id })),
      M.mergeNodeListsUnion(local.nodes, remote.nodes, base.nodes, (l, r, b2) => ({ l: l.id, r: r.id, b: b2 && b2.id }))],
    [OldSmart.mergeSmartConnections(local.connections, remote.connections, new Set(['a', 'b', 'c'])), M.mergeConnectionsUnion(local.connections, remote.connections, new Set(['a', 'b', 'c']))],
  ];
  if (pairs.some(([x, y]) => JSON.stringify(x) !== JSON.stringify(y))) smartMismatch++;
}
const result = { ref: REF, cases: N, classicMismatch, smartMismatch, firstDiff };
console.log(JSON.stringify(result));
process.exit(classicMismatch || smartMismatch ? 1 : 0);
