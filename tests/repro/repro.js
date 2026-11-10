// 审计复现 / 回归脚本：前端画布同步（C1、C2、C3、C4）与画布 PUT 语义（C6、C7）。
// 每个场景输出 PASS（问题已修复）或 FAIL（问题仍在）。
// 只对本脚本新建的 AUDIT-TEST 画布做写操作，结束时把它们移入回收站。
//
// 用法:
//   REPRO_BASE=http://127.0.0.1:3100 node tests/repro/repro.js
// 环境变量:
//   REPRO_BASE        被测服务地址（默认 http://127.0.0.1:3000；建议指向 run_test_instance.sh 启动的隔离实例）
//   PUPPETEER_CORE    puppeteer-core 模块路径（默认在 ~/.npm/_npx 缓存里查找）
//   CHROME_PATH       Chrome 可执行文件（默认 macOS 系统 Chrome）
//   REPRO_RESULTS     结果 JSON 输出路径（默认 tests/repro/results.json，已被 .gitignore 忽略）
//   REPRO_ONLY        只跑指定场景前缀，逗号分隔，例如 C1,C2
const fs = require('fs');
const path = require('path');
const os = require('os');

function findPuppeteerCore() {
  if (process.env.PUPPETEER_CORE) return process.env.PUPPETEER_CORE;
  try { return require.resolve('puppeteer-core'); } catch (e) {}
  const npx = path.join(os.homedir(), '.npm', '_npx');
  for (const dir of (fs.existsSync(npx) ? fs.readdirSync(npx) : [])) {
    const candidate = path.join(npx, dir, 'node_modules', 'puppeteer-core');
    if (fs.existsSync(path.join(candidate, 'package.json'))) return candidate;
  }
  throw new Error('puppeteer-core not found; set PUPPETEER_CORE');
}
const puppeteer = require(findPuppeteerCore());
const B = (process.env.REPRO_BASE || 'http://127.0.0.1:3000').replace(/\/+$/, '');
const CHROME = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const RESULTS_PATH = process.env.REPRO_RESULTS || path.join(__dirname, 'results.json');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = { scenarios: {} };
const log = (...a) => console.log(new Date().toISOString().slice(11, 23), ...a);
const created = [];

async function api(method, url, body) {
  const res = await fetch(B + url, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  const text = await res.text();
  let json = null; try { json = JSON.parse(text); } catch (e) {}
  return { status: res.status, json, bytes: text.length };
}
const getCanvas = async id => (await api('GET', `/api/canvases/${id}`)).json.canvas;
const nodeOf = (c, id) => (c.nodes || []).find(n => n.id === id) || null;
const nodeX = (c, id) => (nodeOf(c, id) || {}).x;

function record(name, pass, detail) {
  results.scenarios[name] = { pass: !!pass, ...detail };
  log(`[${pass ? 'PASS' : 'FAIL'}] ${name}`, JSON.stringify(detail));
}

async function createTestCanvas(kind, nodes) {
  const r = await api('POST', '/api/canvases', { title: `AUDIT-TEST-${kind}-${Date.now()}`, icon: kind === 'smart' ? 'sparkles' : '🧩', kind });
  const c = r.json.canvas;
  created.push(c.id);
  const full = await getCanvas(c.id);
  const put = await api('PUT', `/api/canvases/${c.id}`, { title: full.title, icon: full.icon, nodes, connections: [], viewport: { x: 0, y: 0, scale: 1 }, logs: [], settings: {}, base_updated_at: full.updated_at, client_id: 'audit-seed' });
  if (put.status !== 200) throw new Error('seed failed ' + put.status);
  return c.id;
}
const smartNodes = () => [
  { id: 'audit-s1', type: 'smart-text', x: 100, y: 100, w: 440, h: 250, title: 'S1', text: '' },
  { id: 'audit-s2', type: 'smart-text', x: 700, y: 100, w: 440, h: 250, title: 'S2', text: '' },
];
const classicNodes = () => [
  { id: 'audit-n1', type: 'prompt', x: 100, y: 100, text: 'n1' },
  { id: 'audit-n2', type: 'prompt', x: 600, y: 100, text: 'n2' },
  { id: 'audit-n3', type: 'prompt', x: 1100, y: 100, text: 'n3' },
];

function trackPuts(page, id, store) {
  page.on('response', res => {
    const req = res.request();
    if (req.method() === 'PUT' && req.url().includes(`/api/canvases/${id}`)) {
      let base = null; try { base = JSON.parse(req.postData() || '{}').base_updated_at; } catch (e) {}
      store.push({ t: Date.now(), status: res.status(), base });
    }
  });
}

async function openSmart(browser, id, count = 2) {
  const p = await browser.newPage();
  p.on('pageerror', e => log('pageerror(smart)', e.message));
  await p.goto(`${B}/static/smart-canvas.html?id=${id}`, { waitUntil: 'domcontentloaded' });
  await p.waitForFunction(`typeof canvas!=='undefined' && canvas && Array.isArray(nodes) && nodes.length===${count}`, { timeout: 20000 });
  await sleep(2500);
  return p;
}
async function openClassic(browser, id, count = 3) {
  const p = await browser.newPage();
  p.on('pageerror', e => log('pageerror(classic)', e.message));
  await p.goto(`${B}/static/canvas.html?id=${id}`, { waitUntil: 'domcontentloaded' });
  await p.waitForFunction(`typeof canvas!=='undefined' && canvas && canvas.id===${JSON.stringify(id)} && nodes.length===${count}`, { timeout: 20000 });
  await sleep(1500);
  return p;
}
// 模拟 index.html 对 WS canvas_updated 的转发（普通画布页自己不连 WS，只能靠外壳转发）
const forwardShim = `(() => { const ws = new WebSocket('ws://'+location.host+'/ws/stats?client_id=audit-shim-'+Math.random().toString(36).slice(2));
  ws.onmessage = e => { try { const d = JSON.parse(e.data); if (d.type === 'canvas_updated') { window.__auditMsgs = (window.__auditMsgs||0)+1; window.postMessage(d, '*'); } } catch(_){} };
  return new Promise(r => ws.onopen = r); })()`;
const localNode = (page, id) => page.evaluate(`(() => { const n = nodes.find(n => n.id === ${JSON.stringify(id)}); return n ? {x: n.x, title: n.title, text: n.text} : null; })()`);

// ---------------- C1：智能画布 ----------------
async function c1Overlap(browser) {
  const id = await createTestCanvas('smart', smartNodes());
  const page = await browser.newPage();
  page.on('pageerror', e => log('pageerror(smart)', e.message));
  let phase = 'idle', seq = 0;
  await page.setRequestInterception(true);
  page.on('request', async req => {
    if (phase === 'c1' && req.method() === 'PUT' && req.url().includes(`/api/canvases/${id}`)) {
      seq++;
      const delay = seq === 1 ? 1500 : seq === 2 ? 3000 : 0; // 让两次保存在网络上重叠
      if (delay) await sleep(delay);
    }
    req.continue();
  });
  const puts = []; trackPuts(page, id, puts);
  await page.goto(`${B}/static/smart-canvas.html?id=${id}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(`typeof canvas!=='undefined' && canvas && Array.isArray(nodes) && nodes.length===2`, { timeout: 20000 });
  await sleep(2500);
  const c0 = await getCanvas(id);
  puts.length = 0; phase = 'c1';
  await page.evaluate(`nodes.find(n=>n.id==='audit-s1').x += 111; scheduleSave();`);
  await sleep(700);
  await page.evaluate(`nodes.find(n=>n.id==='audit-s2').x += 222; scheduleSave();`);
  await sleep(8000);
  phase = 'idle';
  const c1 = await getCanvas(id);
  const expected = { s1: nodeX(c0, 'audit-s1') + 111, s2: nodeX(c0, 'audit-s2') + 222 };
  const server = { s1: nodeX(c1, 'audit-s1'), s2: nodeX(c1, 'audit-s2') };
  const local = { s1: (await localNode(page, 'audit-s1')).x, s2: (await localNode(page, 'audit-s2')).x };
  record('C1_single_tab_overlapping_saves', server.s1 === expected.s1 && server.s2 === expected.s2 && local.s2 === expected.s2,
    { expected, server, local, puts: puts.map(p => p.status) });
  await page.close();
}

async function c1CrossTab(browser) {
  const id = await createTestCanvas('smart', smartNodes());
  const p1 = await openSmart(browser, id);
  const p2 = await openSmart(browser, id);
  const p1Puts = []; trackPuts(p1, id, p1Puts);
  const c0 = await getCanvas(id);
  // P2 改 S1 的标题并保存；P1 几乎同时改 S1 的位置和 S2 的位置（P1 的 base 已过期 → 409 → 合并）
  await p2.evaluate(`nodes.find(n=>n.id==='audit-s1').title = 'P2-title'; saveCanvas();`);
  await p1.evaluate(`nodes.find(n=>n.id==='audit-s1').x += 50; nodes.find(n=>n.id==='audit-s2').x += 60; saveCanvas();`);
  await sleep(9000);
  const c1 = await getCanvas(id);
  const s1 = nodeOf(c1, 'audit-s1'), s2 = nodeOf(c1, 'audit-s2');
  const expected = { s1: { x: nodeX(c0, 'audit-s1') + 50, title: 'P2-title' }, s2: { x: nodeX(c0, 'audit-s2') + 60 } };
  const p2Local = { s1: await localNode(p2, 'audit-s1'), s2: await localNode(p2, 'audit-s2') };
  const pass = s1.x === expected.s1.x && s1.title === 'P2-title' && s2.x === expected.s2.x
    && p2Local.s1.x === expected.s1.x && p2Local.s2.x === expected.s2.x;
  record('C1_cross_tab_conflict_merge', pass, { expected, server: { s1: { x: s1.x, title: s1.title }, s2: { x: s2.x } }, p2Local, p1Puts: p1Puts.map(p => p.status) });
  await p1.close(); await p2.close();
}

async function c1RemoteWhileDirty(browser) {
  const id = await createTestCanvas('smart', smartNodes());
  const p1 = await openSmart(browser, id);
  const p2 = await openSmart(browser, id);
  const c0 = await getCanvas(id);
  // P1 改 S2 后处于 450ms 防抖中；P2 立即保存 S1 → P1 收到 WS 广播并合并服务器版本
  await p1.evaluate(`nodes.find(n=>n.id==='audit-s2').x += 70; scheduleSave();`);
  await p2.evaluate(`nodes.find(n=>n.id==='audit-s1').x += 80; saveCanvas();`);
  await sleep(6000);
  const c1 = await getCanvas(id);
  const expected = { s1: nodeX(c0, 'audit-s1') + 80, s2: nodeX(c0, 'audit-s2') + 70 };
  const server = { s1: nodeX(c1, 'audit-s1'), s2: nodeX(c1, 'audit-s2') };
  record('C1_remote_update_while_local_dirty', server.s1 === expected.s1 && server.s2 === expected.s2, { expected, server });
  await p1.close(); await p2.close();
}

// ---------------- C2：保存失败 ----------------
async function c2(browser, kind) {
  const id = await createTestCanvas(kind, kind === 'smart' ? smartNodes() : classicNodes());
  const page = kind === 'smart' ? await openSmart(browser, id) : await openClassic(browser, id);
  const nodeId = kind === 'smart' ? 'audit-s1' : 'audit-n1';
  let failing = false;
  const attempts = [];
  await page.setRequestInterception(true);
  page.on('request', req => {
    if (req.method() === 'PUT' && req.url().includes(`/api/canvases/${id}`)) {
      attempts.push({ t: Date.now(), failing });
      if (failing) return req.respond({ status: 500, contentType: 'application/json', body: '{"detail":"audit simulated 500"}' });
    }
    req.continue();
  });
  const before = nodeX(await getCanvas(id), nodeId);
  failing = true;
  await page.evaluate(`nodes.find(n=>n.id===${JSON.stringify(nodeId)}).x += 1000; scheduleSave();`);
  await sleep(4000);
  const duringFailure = await page.evaluate(`({
    state: document.documentElement.dataset.canvasSaveState || '(none)',
    unsaved: typeof canvasHasUnsavedChanges === 'function' ? canvasHasUnsavedChanges() : '(no fn)',
  })`);
  const attemptsWhileFailing = attempts.length;
  failing = false; // 后端恢复
  await sleep(14000);
  const after = nodeX(await getCanvas(id), nodeId);
  const local = (await localNode(page, nodeId)).x;
  const afterState = await page.evaluate(`({
    state: document.documentElement.dataset.canvasSaveState || '(none)',
    unsaved: typeof canvasHasUnsavedChanges === 'function' ? canvasHasUnsavedChanges() : '(no fn)',
  })`);
  const pass = attemptsWhileFailing >= 2 && duringFailure.state === 'error' && duringFailure.unsaved === true
    && after === before + 1000 && local === after && afterState.unsaved === false;
  record(`C2_${kind}_save_failure_retry`, pass, { attemptsWhileFailing, totalAttempts: attempts.length, duringFailure, serverBefore: before, serverAfter: after, local, afterState });
  await page.close();
}

// ---------------- C3 / C4：普通画布 ----------------
async function classicPair(browser) {
  const id = await createTestCanvas('classic', classicNodes());
  const p1 = await openClassic(browser, id);
  const p2 = await openClassic(browser, id);
  await p2.bringToFront();
  return { id, p1, p2 };
}

async function c3(browser) {
  // 场景 1：不同节点
  {
    const { id, p1, p2 } = await classicPair(browser);
    await p1.evaluate('stopCanvasRemotePolling()'); // 后台标签页：document.hidden 时代码本来就跳过轮询
    const puts = []; trackPuts(p1, id, puts);
    const c0 = await getCanvas(id);
    await p2.evaluate(`nodes.find(n=>n.id==='audit-n1').x += 300; saveCanvas();`);
    await sleep(1200);
    await p1.evaluate(`nodes.find(n=>n.id==='audit-n2').x += 400; scheduleSave();`);
    await sleep(5000);
    const c1 = await getCanvas(id);
    const expected = { n1: nodeX(c0, 'audit-n1') + 300, n2: nodeX(c0, 'audit-n2') + 400 };
    const server = { n1: nodeX(c1, 'audit-n1'), n2: nodeX(c1, 'audit-n2') };
    const p1Local = { n1: (await localNode(p1, 'audit-n1')).x, n2: (await localNode(p1, 'audit-n2')).x };
    record('C3_conflict_different_nodes', server.n1 === expected.n1 && server.n2 === expected.n2 && p1Local.n1 === expected.n1,
      { expected, server, p1Local, p1Puts: puts.map(p => p.status) });
    await p1.close(); await p2.close();
  }
  // 场景 2：同一节点的不同字段
  {
    const { id, p1, p2 } = await classicPair(browser);
    await p1.evaluate('stopCanvasRemotePolling()');
    const c0 = await getCanvas(id);
    await p2.evaluate(`nodes.find(n=>n.id==='audit-n1').text = 'edited-by-P2'; saveCanvas();`);
    await sleep(1200);
    await p1.evaluate(`nodes.find(n=>n.id==='audit-n1').x += 55; scheduleSave();`);
    await sleep(5000);
    const n1 = nodeOf(await getCanvas(id), 'audit-n1');
    record('C3_conflict_same_node_different_fields', n1.text === 'edited-by-P2' && n1.x === nodeX(c0, 'audit-n1') + 55,
      { server: { text: n1.text, x: n1.x }, expected: { text: 'edited-by-P2', x: nodeX(c0, 'audit-n1') + 55 } });
    await p1.close(); await p2.close();
  }
  // 场景 3：P2 删除节点，P1 编辑另一个节点
  {
    const { id, p1, p2 } = await classicPair(browser);
    await p1.evaluate('stopCanvasRemotePolling()');
    const c0 = await getCanvas(id);
    await p2.evaluate(`nodes = nodes.filter(n => n.id !== 'audit-n3'); saveCanvas();`);
    await sleep(1200);
    await p1.evaluate(`nodes.find(n=>n.id==='audit-n1').x += 66; scheduleSave();`);
    await sleep(5000);
    const c1 = await getCanvas(id);
    const ids = (c1.nodes || []).map(n => n.id).sort();
    record('C3_conflict_remote_delete_vs_local_edit', !ids.includes('audit-n3') && nodeX(c1, 'audit-n1') === nodeX(c0, 'audit-n1') + 66,
      { nodeIds: ids, n1x: nodeX(c1, 'audit-n1'), expectedN1x: nodeX(c0, 'audit-n1') + 66 });
    await p1.close(); await p2.close();
  }
}

async function c4(browser) {
  const { id, p1, p2 } = await classicPair(browser);
  await p1.evaluate(forwardShim);
  const puts = []; trackPuts(p1, id, puts);
  const c0 = await getCanvas(id);
  await p1.evaluate(`nodes.find(n=>n.id==='audit-n2').x += 500; scheduleSave();`); // 500ms 防抖中
  await p2.evaluate(`nodes.find(n=>n.id==='audit-n1').x += 600; saveCanvas();`);   // 立即保存 → WS 广播
  await sleep(6000);
  const c1 = await getCanvas(id);
  const expected = { n1: nodeX(c0, 'audit-n1') + 600, n2: nodeX(c0, 'audit-n2') + 500 };
  const server = { n1: nodeX(c1, 'audit-n1'), n2: nodeX(c1, 'audit-n2') };
  const p1Local = { n1: (await localNode(p1, 'audit-n1')).x, n2: (await localNode(p1, 'audit-n2')).x };
  record('C4_remote_update_keeps_local_edit', server.n1 === expected.n1 && server.n2 === expected.n2 && p1Local.n1 === expected.n1 && p1Local.n2 === expected.n2,
    { broadcastsForwardedToP1: await p1.evaluate('window.__auditMsgs||0'), expected, server, p1Local, p1Puts: puts.map(p => p.status) });
  await p1.close(); await p2.close();
}

// ---------------- A2：配置拉取失败不得改写节点 ----------------
async function failConfigRequests(page) {
  const state = { failing: false };
  await page.setRequestInterception(true);
  page.on('request', req => {
    const url = req.url();
    if (state.failing && req.method() === 'GET' && (url.includes('/api/config') || url.includes('/api/workflows'))) {
      return req.respond({ status: 500, contentType: 'application/json', body: '{"detail":"audit simulated 500"}' });
    }
    req.continue();
  });
  return state;
}
async function a2Classic(browser) {
  const id = await createTestCanvas('classic', [
    { id: 'audit-n1', type: 'prompt', x: 100, y: 100, text: 'n1' },
    { id: 'audit-g1', type: 'generator', x: 600, y: 100, apiProvider: 'codex', model: 'gpt-image-2' },
    { id: 'audit-c1', type: 'comfy', x: 1200, y: 100, comfyWorkflow: 'MiniMax_H3.json' },
  ]);
  const page = await openClassic(browser, id, 3);
  const state = await failConfigRequests(page);
  state.failing = true;
  // 设置页保存后的广播、切回画布标签页，都会触发重新拉取配置
  await page.evaluate(`window.postMessage({type:'providers-changed'}, location.origin); window.postMessage({type:'canvas-focus'}, location.origin);`);
  await sleep(2500);
  await page.evaluate(`nodes.find(n=>n.id==='audit-n1').x += 10; scheduleSave();`);
  await sleep(2500);
  const c = await getCanvas(id);
  const g1 = nodeOf(c, 'audit-g1'), c1 = nodeOf(c, 'audit-c1');
  record('A2_classic_config_failure_keeps_nodes', g1.apiProvider === 'codex' && g1.model === 'gpt-image-2' && c1.comfyWorkflow === 'MiniMax_H3.json',
    { generator: { apiProvider: g1.apiProvider, model: g1.model }, comfyWorkflow: c1.comfyWorkflow ?? '(missing)' });
  await page.close();
}
async function a2Smart(browser) {
  const id = await createTestCanvas('smart', smartNodes());
  const page = await openSmart(browser, id);
  const before = await page.evaluate(`({providers: apiProviders.length, workflows: comfyWorkflows.length})`);
  const state = await failConfigRequests(page);
  state.failing = true;
  await page.evaluate(`window.postMessage({type:'providers-changed'}, location.origin)`);
  await sleep(2500);
  const after = await page.evaluate(`({providers: apiProviders.length, workflows: comfyWorkflows.length})`);
  record('A2_smart_config_failure_keeps_config', after.providers === before.providers && after.workflows === before.workflows && before.providers > 0, { before, after });
  await page.close();
}

// ---------------- A1 前端：不悄悄换平台、不凭空造 comfly ----------------
const DELETED_PROVIDER = 'audit-deleted-provider';
function countRequests(page, pattern) {
  const hits = [];
  page.on('request', req => { if (req.method() === 'POST' && req.url().includes(pattern)) hits.push(req.postData() || ''); });
  return hits;
}
async function a1Classic(browser) {
  const id = await createTestCanvas('classic', [
    { id: 'audit-n1', type: 'prompt', x: 100, y: 100, text: 'a cat' },
    { id: 'audit-g1', type: 'generator', x: 600, y: 100, apiProvider: DELETED_PROVIDER, model: 'gpt-image-2' },
    { id: 'audit-v1', type: 'video', x: 600, y: 700, apiProvider: '', model: 'veo3-fast' },
  ]);
  const c0 = await getCanvas(id);
  await api('PUT', `/api/canvases/${id}`, { connections: [{ id: 'audit-e1', from: 'audit-n1', to: 'audit-g1' }, { id: 'audit-e2', from: 'audit-n1', to: 'audit-v1' }], base_updated_at: c0.updated_at });
  const page = await openClassic(browser, id, 3);
  page.on('dialog', d => d.dismiss().catch(() => {}));
  const imageTasks = countRequests(page, '/api/canvas-image-tasks');
  const videoCalls = countRequests(page, '/api/canvas-video');
  await page.evaluate(`render(); nodes.find(n=>n.id==='audit-n1').x += 10; scheduleSave();`);
  await sleep(2500);
  const c = await getCanvas(id);
  const g1 = nodeOf(c, 'audit-g1'), v1 = nodeOf(c, 'audit-v1');
  const optionsShowStale = await page.evaluate(`providerOptions(${JSON.stringify(DELETED_PROVIDER)}).includes(${JSON.stringify(DELETED_PROVIDER)}) && providerOptions(${JSON.stringify(DELETED_PROVIDER)}).includes('⚠')`);
  await page.evaluate(`runGenerator('audit-g1')`);
  await sleep(800);
  const generatorError = await page.evaluate(`document.getElementById('errorMessage')?.textContent || ''`);
  await page.evaluate(`document.getElementById('errorModal')?.classList.remove('open'); runVideoNode('audit-v1')`);
  await sleep(800);
  const videoError = await page.evaluate(`document.getElementById('errorMessage')?.textContent || ''`);
  record('A1_classic_deleted_provider_not_switched',
    g1.apiProvider === DELETED_PROVIDER && g1.model === 'gpt-image-2' && optionsShowStale && generatorError.includes(DELETED_PROVIDER) && imageTasks.length === 0,
    { saved: { apiProvider: g1.apiProvider, model: g1.model }, optionsShowStale, generatorError: generatorError.slice(0, 80), imageTaskRequests: imageTasks.length });
  record('A1_classic_no_phantom_comfly',
    v1.apiProvider !== 'comfly' && v1.model === 'veo3-fast' && videoCalls.length === 0 && !videoCalls.some(b => b.includes('comfly')),
    { savedVideo: { apiProvider: v1.apiProvider, model: v1.model }, videoError: videoError.slice(0, 80), videoRequests: videoCalls.length, sentProviders: videoCalls.map(b => { try { return JSON.parse(b).provider_id; } catch (e) { return '?'; } }) });
  await page.close();
}
async function a1Smart(browser) {
  const id = await createTestCanvas('smart', [
    { id: 'audit-g', type: 'smart-image-generation', x: 100, y: 100, w: 440, h: 250, title: 'G',
      runSettings: { engine: 'api', apiKind: 'image', provider_id: DELETED_PROVIDER, model: 'gpt-image-2' } },
  ]);
  const page = await openSmart(browser, id, 1);
  const imageTasks = countRequests(page, '/api/canvas-image-tasks');
  await page.evaluate(`selectedId = 'audit-g'; updateComposer(); renderDynamicParams();`);
  await sleep(1000);
  const pill = await page.evaluate(`document.querySelector('#dynamicParams .provider-control .sub, .provider-control .sub')?.textContent || ''`);
  await page.evaluate(`scheduleSave()`);
  await sleep(2500);
  const g = nodeOf(await getCanvas(id), 'audit-g');
  const runError = await page.evaluate(`runApiGeneration('a cat', [], nodes.find(n=>n.id==='audit-g').runSettings).then(() => 'ran', e => e.message)`);
  await sleep(500);
  record('A1_smart_deleted_provider_not_switched',
    g.runSettings?.provider_id === DELETED_PROVIDER && g.runSettings?.model === 'gpt-image-2' && pill.includes(DELETED_PROVIDER) && runError.includes(DELETED_PROVIDER) && imageTasks.length === 0,
    { saved: { provider_id: g.runSettings?.provider_id, model: g.runSettings?.model }, pill, runError: String(runError).slice(0, 80), imageTaskRequests: imageTasks.length });
  await page.close();
}

// ---------------- A3：智能画布节点里的失效平台要被发现并提示 ----------------
async function a3Smart(browser) {
  const id = await createTestCanvas('smart', [
    { id: 'audit-a', type: 'smart-image-generation', x: 100, y: 100, w: 440, h: 250, title: 'A',
      runSettings: { engine: 'api', apiKind: 'image', provider_id: DELETED_PROVIDER, model: 'gpt-image-2' } },
    { id: 'audit-b', type: 'smart-prompt', x: 700, y: 100, w: 316, h: 240, title: 'Prompt', text: '', llmEnabled: true, llmProvider: 'audit-deleted-chat', llmModel: 'x' },
    { id: 'audit-c', type: 'smart-image-generation', x: 100, y: 500, w: 440, h: 250, title: 'C',
      runSettings: { engine: 'api', apiKind: 'image', provider_id: 'codex', model: 'gpt-image-2' } },
  ]);
  const page = await openSmart(browser, id, 3);
  const onLoad = await page.evaluate(`({
    toast: document.getElementById('toast')?.textContent || '',
    marked: [...document.querySelectorAll('.image-node[data-provider-missing="true"]')].map(el => el.dataset.id).sort(),
  })`);
  // 用户在参数面板改选了可用平台后，标记应消失
  await page.evaluate(`nodes.find(n => n.id === 'audit-a').runSettings.provider_id = 'codex'; render();`);
  await sleep(300);
  const afterFix = await page.evaluate(`[...document.querySelectorAll('.image-node[data-provider-missing="true"]')].map(el => el.dataset.id).sort()`);
  const pass = onLoad.marked.join(',') === 'audit-a,audit-b' && onLoad.toast.includes(DELETED_PROVIDER) && onLoad.toast.includes('audit-deleted-chat')
    && afterFix.join(',') === 'audit-b';
  record('A3_smart_missing_providers_reported', pass, { onLoad, afterFix });
  await page.close();
}

// ---------------- GEN：保存进行中 / 409 合并期间生成任务返回结果 ----------------
// 生成任务由 puppeteer 拦截并返回模拟结果（隔离实例只有假 Key）；保存请求可人为延迟，制造“保存进行中”的窗口。
async function installGenMocks(page, { canvasId, prefix }) {
  const ctl = { releasedAll: false, putDelay: 0, putsInFlight: 0, putStatuses: [], taskSeq: 0, successWhileSaving: 0, successServed: 0 };
  await page.setRequestInterception(true);
  page.on('request', async req => {
    const url = req.url(), method = req.method();
    if (method === 'POST' && /\/api\/canvas-image-tasks$/.test(url)) {
      const id = `${prefix}-${++ctl.taskSeq}`;
      return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ task_id: id, status: 'queued' }) });
    }
    if (method === 'GET' && url.includes(`/api/canvas-image-tasks/${prefix}-`)) {
      const id = decodeURIComponent(url.split('/').pop().split('?')[0]);
      if (ctl.releasedAll) {
        ctl.successServed++;
        if (ctl.putsInFlight > 0) ctl.successWhileSaving++;
        return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ id, status: 'succeeded', result: { images: [`/assets/output/${id}.png`] } }) });
      }
      return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ id, status: 'running' }) });
    }
    if (method === 'PUT' && url.includes(`/api/canvases/${canvasId}`) && ctl.putDelay) {
      ctl.putsInFlight++;
      await sleep(ctl.putDelay);
      ctl.putsInFlight--;
    }
    req.continue();
  });
  page.on('response', res => {
    if (res.request().method() === 'PUT' && res.url().includes(`/api/canvases/${canvasId}`)) ctl.putStatuses.push(res.status());
  });
  return ctl;
}
const genNode = (id, x, y) => ({ id, type: 'smart-image-generation', x, y, w: 440, h: 250, title: id,
  runSettings: { engine: 'api', apiKind: 'image', provider_id: 'codex', model: 'gpt-image-2' } });
const historyOf = node => (node?.generationHistory || []).map(h => ({ status: h.status, outputs: (h.outputs || []).map(o => o?.url || o) }));
const imageUrls = node => (node?.images || []).map(i => i?.url || i);
async function startSmartRun(page, nodeId) {
  await page.evaluate(`selectedId=${JSON.stringify(nodeId)}; updateComposer(); setPromptText('a cat'); void runGeneration({});`);
}

async function genSmartWhileSaving(browser) {
  const id = await createTestCanvas('smart', [genNode('audit-g', 100, 100), { id: 'audit-s', type: 'smart-text', x: 700, y: 100, w: 440, h: 250, title: 'S', text: '' }]);
  const page = await openSmart(browser, id, 2);
  const ctl = await installGenMocks(page, { canvasId: id, prefix: 'mock-ws' });
  await startSmartRun(page, 'audit-g');
  await sleep(3000);
  ctl.putDelay = 4000;                    // 之后的保存都要 4 秒才返回
  await page.evaluate(`nodes.find(n=>n.id==='audit-s').x += 33; scheduleSave();`);
  await sleep(800);                       // 保存已发出、尚未返回
  ctl.releasedAll = true;                 // 下一次轮询（≤2s）拿到结果，此时保存仍在进行中
  await sleep(14000);
  const c = await getCanvas(id);
  const g = nodeOf(c, 'audit-g'), sNode = nodeOf(c, 'audit-s');
  const urls = imageUrls(g), hist = historyOf(g);
  const pass = ctl.successWhileSaving > 0 && urls.some(u => u.includes('mock-ws-1')) && hist.some(h => h.status === 'success' && h.outputs.some(u => u.includes('mock-ws-1')))
    && sNode.x === 733 && !(g.pendingTasks || []).length;
  record('GEN_smart_result_while_save_in_flight', pass, { successWhileSaving: ctl.successWhileSaving, images: urls, history: hist, sX: sNode.x, pendingLeft: (g.pendingTasks || []).length, puts: ctl.putStatuses });
  await page.close();
}

async function genSmartDuring409(browser) {
  const id = await createTestCanvas('smart', [genNode('audit-g', 100, 100),
    { id: 'audit-s', type: 'smart-text', x: 700, y: 100, w: 440, h: 250, title: 'S', text: '' },
    { id: 'audit-t', type: 'smart-text', x: 700, y: 500, w: 440, h: 250, title: 'T', text: '' }]);
  const page = await openSmart(browser, id, 3);
  const ctl = await installGenMocks(page, { canvasId: id, prefix: 'mock-409' });
  await startSmartRun(page, 'audit-g');
  await sleep(3500);
  // 另一个客户端修改 T；client_id 用本页的，使本页不收到广播，下一次保存必然 409
  const clientId = await page.evaluate('smartClientId');
  const cur = await getCanvas(id);
  const tNodes = cur.nodes.map(n => n.id === 'audit-t' ? { ...n, x: n.x + 44 } : n);
  const ext = await api('PUT', `/api/canvases/${id}`, { nodes: tNodes, base_updated_at: cur.updated_at, client_id: clientId });
  ctl.putDelay = 4000;
  await page.evaluate(`nodes.find(n=>n.id==='audit-s').x += 55; scheduleSave();`);
  await sleep(800);
  ctl.releasedAll = true;                 // 结果在“这次注定 409 的保存”进行中到达
  await sleep(16000);
  const c = await getCanvas(id);
  const g = nodeOf(c, 'audit-g');
  const urls = imageUrls(g), hist = historyOf(g);
  const pass = ctl.putStatuses.includes(409) && ctl.successWhileSaving > 0
    && urls.some(u => u.includes('mock-409-1')) && hist.some(h => h.status === 'success' && h.outputs.some(u => u.includes('mock-409-1')))
    && nodeX(c, 'audit-t') === 744 && nodeX(c, 'audit-s') === 755 && !(g.pendingTasks || []).length;
  record('GEN_smart_result_during_409_merge', pass, { externalPut: ext ? 'ok' : 'failed', puts: ctl.putStatuses, successWhileSaving: ctl.successWhileSaving, images: urls, history: hist, tX: nodeX(c, 'audit-t'), sX: nodeX(c, 'audit-s'), pendingLeft: (g.pendingTasks || []).length });
  await page.close();
}

async function genSmartTwoTabsHistory(browser) {
  const id = await createTestCanvas('smart', [genNode('audit-g', 100, 100)]);
  const p1 = await openSmart(browser, id, 1);
  const p2 = await openSmart(browser, id, 1);
  const c1 = await installGenMocks(p1, { canvasId: id, prefix: 'mock-tab1' });
  const c2 = await installGenMocks(p2, { canvasId: id, prefix: 'mock-tab2' });
  c1.putDelay = c2.putDelay = 1500;       // 让两边都先开始生成，再看到对方的保存
  await Promise.all([startSmartRun(p1, 'audit-g'), startSmartRun(p2, 'audit-g')]);
  await sleep(4000);
  c1.releasedAll = true;
  c2.releasedAll = true;
  c1.putDelay = c2.putDelay = 0;
  await sleep(14000);
  const g = nodeOf(await getCanvas(id), 'audit-g');
  const hist = historyOf(g);
  const has = tag => hist.some(h => h.status === 'success' && h.outputs.some(u => u.includes(tag)));
  const p1Hist = await p1.evaluate(`(nodes.find(n=>n.id==='audit-g').generationHistory||[]).length`);
  const p2Hist = await p2.evaluate(`(nodes.find(n=>n.id==='audit-g').generationHistory||[]).length`);
  record('GEN_smart_two_tabs_both_history_entries_kept', has('mock-tab1') && has('mock-tab2') && p1Hist >= 2 && p2Hist >= 2,
    { serverHistory: hist, tab1LocalHistory: p1Hist, tab2LocalHistory: p2Hist, tab1Puts: c1.putStatuses, tab2Puts: c2.putStatuses });
  await p1.close(); await p2.close();
}

const classicGenNodes = () => [
  { id: 'audit-n1', type: 'prompt', x: 100, y: 100, text: 'a cat' },
  { id: 'audit-g1', type: 'generator', x: 600, y: 100, apiProvider: 'codex', model: 'gpt-image-2' },
  { id: 'audit-n2', type: 'prompt', x: 100, y: 700, text: 'a dog' },
  { id: 'audit-g2', type: 'generator', x: 600, y: 700, apiProvider: 'codex', model: 'gpt-image-2' },
];
async function seedClassicGen() {
  const id = await createTestCanvas('classic', classicGenNodes());
  const c0 = await getCanvas(id);
  await api('PUT', `/api/canvases/${id}`, { connections: [{ id: 'audit-e1', from: 'audit-n1', to: 'audit-g1' }, { id: 'audit-e2', from: 'audit-n2', to: 'audit-g2' }], base_updated_at: c0.updated_at });
  return id;
}
const outputUrlsOf = c => (c.nodes || []).filter(n => n.type === 'output').flatMap(n => (n.images || []).map(i => i?.url || i));
const logOutputsOf = c => (c.logs || []).flatMap(l => (l.outputs || []).map(o => o?.url || o));

async function genClassicDuring409(browser) {
  const id = await seedClassicGen();
  const page = await openClassic(browser, id, 4);
  await page.evaluate('stopCanvasRemotePolling()');
  const ctl = await installGenMocks(page, { canvasId: id, prefix: 'mock-c409' });
  await page.evaluate(`void runGenerator('audit-g1')`);
  await sleep(4000);
  const cur = await getCanvas(id);
  const ext = await api('PUT', `/api/canvases/${id}`, { nodes: cur.nodes.map(n => n.id === 'audit-n2' ? { ...n, x: n.x + 77 } : n), base_updated_at: cur.updated_at });
  ctl.putDelay = 4000;
  await page.evaluate(`nodes.find(n=>n.id==='audit-n1').x += 11; scheduleSave();`);
  await sleep(800);
  ctl.releasedAll = true;
  await sleep(16000);
  const c = await getCanvas(id);
  const outs = outputUrlsOf(c), logs = logOutputsOf(c);
  const pass = ctl.putStatuses.includes(409) && ctl.successWhileSaving > 0 && outs.some(u => u.includes('mock-c409-1')) && logs.some(u => u.includes('mock-c409-1'))
    && nodeX(c, 'audit-n2') === 177 && nodeX(c, 'audit-n1') === 111;
  record('GEN_classic_result_during_409_merge', pass, { externalPut: ext ? 'ok' : 'failed', puts: ctl.putStatuses, successWhileSaving: ctl.successWhileSaving, outputImages: outs, logOutputs: logs, n1x: nodeX(c, 'audit-n1'), n2x: nodeX(c, 'audit-n2') });
  await page.close();
}

async function genClassicTwoTabsLogs(browser) {
  const id = await seedClassicGen();
  const p1 = await openClassic(browser, id, 4);
  const p2 = await openClassic(browser, id, 4);
  await p1.evaluate('stopCanvasRemotePolling()');
  await p2.evaluate('stopCanvasRemotePolling()');
  const c1 = await installGenMocks(p1, { canvasId: id, prefix: 'mock-ctab1' });
  const c2 = await installGenMocks(p2, { canvasId: id, prefix: 'mock-ctab2' });
  await Promise.all([p1.evaluate(`void runGenerator('audit-g1')`), p2.evaluate(`void runGenerator('audit-g2')`)]);
  await sleep(4000);
  c1.releasedAll = true;
  c2.releasedAll = true;
  await sleep(14000);
  const c = await getCanvas(id);
  const logs = logOutputsOf(c), outs = outputUrlsOf(c);
  record('GEN_classic_two_tabs_both_log_entries_kept', logs.some(u => u.includes('mock-ctab1')) && logs.some(u => u.includes('mock-ctab2')) && outs.some(u => u.includes('mock-ctab1')) && outs.some(u => u.includes('mock-ctab2')),
    { logOutputs: logs, outputImages: outs, tab1Puts: c1.putStatuses, tab2Puts: c2.putStatuses });
  await p1.close(); await p2.close();
}

// ---------------- 画布 PUT 语义（接口级） ----------------
async function apiSemantics() {
  const id = await createTestCanvas('smart', smartNodes());
  let c = await getCanvas(id);
  await api('PUT', `/api/canvases/${id}`, { title: c.title, icon: c.icon, nodes: c.nodes, connections: c.connections, viewport: c.viewport, logs: [{ id: 'audit-log-1', prompt: 'x' }], settings: { audit: 1 }, base_updated_at: c.updated_at });
  c = await getCanvas(id);
  let r = await api('PUT', `/api/canvases/${id}`, { nodes: c.nodes, base_updated_at: c.updated_at - 1 });
  const stale = { status: r.status, detailKeys: Object.keys(r.json?.detail || {}) };
  r = await api('PUT', `/api/canvases/${id}`, { nodes: c.nodes, connections: c.connections, viewport: c.viewport, base_updated_at: c.updated_at });
  const after = await getCanvas(id);
  record('C6_put_omitted_fields_preserved', r.status === 200 && after.title === c.title && after.logs.length === 1 && after.settings.audit === 1,
    { status: r.status, title: after.title === c.title ? 'kept' : after.title, logs: after.logs.length, settings: after.settings, staleBase409: stale });
  const beforeMeta = await getCanvas(id);
  await api('POST', `/api/canvases/${id}/meta`, { title: 'AUDIT-renamed-in-list' });
  const afterMeta = await getCanvas(id);
  r = await api('PUT', `/api/canvases/${id}`, { title: beforeMeta.title, nodes: beforeMeta.nodes, base_updated_at: beforeMeta.updated_at });
  const afterPut = await getCanvas(id);
  record('C7_list_rename_survives_editor_save', afterPut.title === 'AUDIT-renamed-in-list',
    { metaBumpsUpdatedAt: afterMeta.updated_at !== beforeMeta.updated_at, editorPutStatus: r.status, titleAfterEditorPut: afterPut.title });
}

(async () => {
  const only = (process.env.REPRO_ONLY || '').split(',').map(s => s.trim()).filter(Boolean);
  const want = name => !only.length || only.some(o => name.startsWith(o));
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-first-run', '--no-default-browser-check'] });
  const steps = [
    ['C1', () => c1Overlap(browser)], ['C1', () => c1CrossTab(browser)], ['C1', () => c1RemoteWhileDirty(browser)],
    ['C2', () => c2(browser, 'smart')], ['C2', () => c2(browser, 'classic')],
    ['C3', () => c3(browser)], ['C4', () => c4(browser)],
    ['A2', () => a2Classic(browser)], ['A2', () => a2Smart(browser)],
    ['A1', () => a1Classic(browser)], ['A1', () => a1Smart(browser)],
    ['A3', () => a3Smart(browser)],
    ['GEN', () => genSmartWhileSaving(browser)], ['GEN', () => genSmartDuring409(browser)], ['GEN', () => genSmartTwoTabsHistory(browser)],
    ['GEN', () => genClassicDuring409(browser)], ['GEN', () => genClassicTwoTabsLogs(browser)],
    ['C6', () => apiSemantics()],
  ];
  try {
    for (const [name, fn] of steps) {
      if (!want(name)) continue;
      try { await fn(); } catch (e) { record(`${name}_error`, false, { error: String(e && e.stack || e).slice(0, 600) }); }
    }
  } finally {
    await browser.close();
    for (const id of created) await api('DELETE', `/api/canvases/${id}`); // 移入回收站
    results.base = B;
    results.testCanvases = created;
    const entries = Object.entries(results.scenarios);
    results.passed = entries.filter(([, v]) => v.pass).length;
    results.total = entries.length;
    fs.writeFileSync(RESULTS_PATH, JSON.stringify(results, null, 2));
    console.log(`\n${results.passed}/${results.total} scenarios passed`);
    for (const [k, v] of entries) console.log(`  ${v.pass ? 'PASS' : 'FAIL'}  ${k}`);
  }
})();
