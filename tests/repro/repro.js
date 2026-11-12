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

// ---------------- C8：删除按基线判断；追加型数组逐元素合并 ----------------
const smartC8Nodes = () => [
  { id: 'audit-s1', type: 'smart-text', x: 100, y: 100, w: 440, h: 250, title: 'S1', text: '' },
  { id: 'audit-s2', type: 'smart-text', x: 700, y: 100, w: 440, h: 250, title: 'S2', text: '' },
  { id: 'audit-s3', type: 'smart-text', x: 1300, y: 100, w: 440, h: 250, title: 'S3', text: '' },
];

async function c8SmartDeleteStaysDeleted(browser) {
  const id = await createTestCanvas('smart', smartC8Nodes());
  const c0 = await getCanvas(id);
  await api('PUT', `/api/canvases/${id}`, { connections: [{ id: 'audit-e12', from: 'audit-s1', to: 'audit-s2' }, { id: 'audit-e23', from: 'audit-s2', to: 'audit-s3' }], base_updated_at: c0.updated_at });
  const p1 = await openSmart(browser, id, 3);
  const p2 = await openSmart(browser, id, 3);
  const p1Puts = []; trackPuts(p1, id, p1Puts);
  const start = await getCanvas(id);
  // P2 删除 S3（连同它的连线）和 S1→S2 这条连线并保存；P1 几乎同时改 S1 的位置（P1 的基线已过期 → 409 → 合并）
  await p2.evaluate(`nodes = nodes.filter(n => n.id !== 'audit-s3');
    canvas.connections = (canvas.connections || []).filter(c => c.to !== 'audit-s3' && c.from !== 'audit-s3' && !(c.from === 'audit-s1' && c.to === 'audit-s2'));
    render(); saveCanvas();`);
  await p1.evaluate(`nodes.find(n => n.id === 'audit-s1').x += 50; saveCanvas();`);
  await sleep(9000);
  const c1 = await getCanvas(id);
  const serverIds = c1.nodes.map(n => n.id).sort();
  const serverConns = (c1.connections || []).map(c => `${c.from}->${c.to}`).sort();
  const p1Ids = (await p1.evaluate(`nodes.map(n => n.id)`)).sort();
  const p1Conns = (await p1.evaluate(`(canvas.connections || []).map(c => c.from + '->' + c.to)`)).sort();
  const pass = serverIds.join(',') === 'audit-s1,audit-s2' && serverConns.length === 0
    && p1Ids.join(',') === 'audit-s1,audit-s2' && p1Conns.length === 0 && nodeX(c1, 'audit-s1') === nodeX(start, 'audit-s1') + 50;
  record('C8_smart_deleted_node_stays_deleted', pass, { serverIds, serverConns, p1Ids, p1Conns, s1x: nodeX(c1, 'audit-s1'), expectedS1x: nodeX(start, 'audit-s1') + 50, p1Puts: p1Puts.map(p => p.status) });
  await p1.close(); await p2.close();
}

async function c8ClassicSameOutputBothImages(browser) {
  const id = await createTestCanvas('classic', [
    { id: 'audit-n1', type: 'prompt', x: 100, y: 100, text: 'a cat' },
    { id: 'audit-g1', type: 'generator', x: 600, y: 100, apiProvider: 'codex', model: 'gpt-image-2' },
    { id: 'audit-out', type: 'output', x: 1100, y: 100, images: [{ url: '/assets/output/audit-seed.png', viewed: true }] },
  ]);
  const c0 = await getCanvas(id);
  await api('PUT', `/api/canvases/${id}`, { connections: [{ id: 'audit-e1', from: 'audit-n1', to: 'audit-g1' }, { id: 'audit-e2', from: 'audit-g1', to: 'audit-out' }], base_updated_at: c0.updated_at });
  const p1 = await openClassic(browser, id, 3);
  const p2 = await openClassic(browser, id, 3);
  await p1.evaluate('stopCanvasRemotePolling()');
  await p2.evaluate('stopCanvasRemotePolling()');
  const c1 = await installGenMocks(p1, { canvasId: id, prefix: 'mock-c8tab1' });
  const c2 = await installGenMocks(p2, { canvasId: id, prefix: 'mock-c8tab2' });
  // 两个标签页都运行同一个生成节点，结果都追加到同一个输出节点
  await Promise.all([p1.evaluate(`void runGenerator('audit-g1')`), p2.evaluate(`void runGenerator('audit-g1')`)]);
  await sleep(4000);
  c1.releasedAll = true;
  c2.releasedAll = true;
  await sleep(14000);
  const c = await getCanvas(id);
  const out = nodeOf(c, 'audit-out');
  const urls = (out?.images || []).map(i => i?.url || i);
  const outputNodes = (c.nodes || []).filter(n => n.type === 'output').map(n => n.id);
  const pass = urls.some(u => u.includes('audit-seed')) && urls.some(u => u.includes('mock-c8tab1')) && urls.some(u => u.includes('mock-c8tab2'))
    && outputNodes.join(',') === 'audit-out';
  record('C8_classic_two_tabs_same_output_both_images_kept', pass, { outputImages: urls, outputNodes, tab1Puts: c1.putStatuses, tab2Puts: c2.putStatuses });
  await p1.close(); await p2.close();
}

// ---------------- 第六批：页面版本过期时不能保存；未保存的修改暂存，刷新后恢复 ----------------
async function outdatedPageKeepsDraft(browser, kind) {
  const smart = kind === 'smart';
  const id = await createTestCanvas(kind, smart ? smartNodes() : classicNodes());
  const [n1, n2] = smart ? ['audit-s1', 'audit-s2'] : ['audit-n1', 'audit-n2'];
  const page = smart ? await openSmart(browser, id, 2) : await openClassic(browser, id, 3);
  const puts = []; trackPuts(page, id, puts);
  const c0 = await getCanvas(id);
  // 模拟“页面是在后端更新之前打开的”：把页面里的构建号改成旧值
  await page.evaluate(`document.querySelector('meta[name="ic-client-build"]').content = 'old-build-000000'`);
  let detectedBy;
  if (smart) {
    await sleep(9500);           // 智能画布：8 秒一次的 /meta 轮询发现构建号不同
    detectedBy = 'meta-poll';
  } else {
    await page.evaluate(`stopCanvasRemotePolling(); nodes.find(n => n.id === ${JSON.stringify(n1)}).x += 1; saveCanvas();`);
    await sleep(1500);           // 普通画布：保存被服务端以 412 拒绝
    detectedBy = 'save-412';
  }
  const bannerAfterDetect = await page.evaluate(`Boolean(document.getElementById('icOutdatedBanner'))`);
  const putsBeforeEdit = puts.map(p => p.status);
  // 过期后继续编辑：改 n1、加一个节点，然后触发保存
  const newNode = smart ? { id: 'audit-draft-new', type: 'smart-text', x: 100, y: 500, w: 440, h: 250, title: 'D', text: 'draft' }
                        : { id: 'audit-draft-new', type: 'prompt', x: 100, y: 500, text: 'draft' };
  await page.evaluate(`nodes.find(n => n.id === ${JSON.stringify(n1)}).x += 30; nodes.push(${JSON.stringify(newNode)}); render(); saveCanvas();`);
  await sleep(1500);
  const afterOutdatedEdit = await getCanvas(id);
  const draftInfo = await page.evaluate(`(() => {
    const keys = Object.keys(localStorage).filter(k => k.startsWith('ic-outdated-draft:' + ${JSON.stringify(id)} + ':'));
    const draft = keys.length ? JSON.parse(localStorage.getItem(keys[0])) : null;
    return { keys: keys.length, changed: draft ? draft.patch.changed.map(c => c.id).sort() : [], banner: document.getElementById('icOutdatedBanner')?.textContent || '' };
  })()`);
  // 期间别处改了另一个节点
  const ext = await api('PUT', `/api/canvases/${id}`, { nodes: afterOutdatedEdit.nodes.map(n => n.id === n2 ? { ...n, x: n.x + 77 } : n), base_updated_at: afterOutdatedEdit.updated_at });
  // 刷新页面 → 新代码 → 提示恢复 → 点“恢复到画布”
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForFunction(`typeof canvas!=='undefined' && canvas && Array.isArray(nodes) && nodes.length > 0`, { timeout: 20000 });
  await page.waitForSelector('#icOutdatedDraftRestore', { timeout: 15000 }).catch(() => null);
  const restoreOffered = await page.evaluate(`Boolean(document.getElementById('icOutdatedDraftRestore'))`);
  await page.evaluate(`(() => { const b = [...document.querySelectorAll('#icOutdatedDraftRestore button')].pop(); if (b) b.click(); })()`);
  await sleep(4000);
  const final = await getCanvas(id);
  const draftsLeft = await page.evaluate(`Object.keys(localStorage).filter(k => k.startsWith('ic-outdated-draft:' + ${JSON.stringify(id)} + ':')).length`);
  const checks = {
    detected: bannerAfterDetect,
    noSaveWhileOutdated: nodeX(afterOutdatedEdit, n1) === nodeX(c0, n1) && !nodeOf(afterOutdatedEdit, 'audit-draft-new'),
    serverRejectedOrNotAsked: smart ? putsBeforeEdit.length === 0 : putsBeforeEdit.includes(412) && !putsBeforeEdit.includes(200),
    draftStored: draftInfo.keys === 1 && draftInfo.changed.includes(n1) && draftInfo.changed.includes('audit-draft-new') && /暂存|kept/.test(draftInfo.banner),
    externalSaved: ext.status === 200,
    restoreOffered,
    restored: nodeX(final, n1) === nodeX(c0, n1) + (smart ? 30 : 31) && Boolean(nodeOf(final, 'audit-draft-new')) && nodeX(final, n2) === nodeX(c0, n2) + 77,
    draftCleared: draftsLeft === 0,
  };
  record(`OUTDATED_${kind}_page_cannot_save_and_restores_draft`, Object.values(checks).every(Boolean),
    { detectedBy, checks, putsBeforeEdit, draftInfo, n1: { start: nodeX(c0, n1), final: nodeX(final, n1) }, n2: { start: nodeX(c0, n2), final: nodeX(final, n2) } });
  await page.close();
}

// ---------------- 第六批：online.html、gpt-chat.html 只用 api_providers（/api/config 不再有全局模型列表） ----------------
async function standalonePagesUseProviders(browser) {
  const cfg = (await api('GET', '/api/config')).json;
  const legacyKeys = ['chat_model', 'image_model', 'chat_models', 'image_models', 'video_models', 'ms_chat_models', 'base_url'].filter(k => k in cfg);
  const enabled = (cfg.api_providers || []).filter(p => p.enabled !== false);
  const imageIds = enabled.filter(p => (p.image_models || []).length || (p.rh_apps || []).length || (p.rh_workflows || []).length).map(p => p.id);
  const chatIds = enabled.filter(p => (p.chat_models || []).length).map(p => p.id);
  const errors = [];
  const online = await browser.newPage();
  online.on('pageerror', e => errors.push('online: ' + e.message));
  await online.goto(`${B}/static/online.html`, { waitUntil: 'domcontentloaded' });
  await online.waitForFunction(`document.querySelectorAll('#providerSelect option').length > 0`, { timeout: 20000 });
  await sleep(1500);
  const onlineView = await online.evaluate(`(() => {
    const providers = [...document.querySelectorAll('#providerSelect option')].filter(o => !o.disabled).map(o => o.value);
    const selected = document.getElementById('providerSelect').value;
    const p = apiProviders.find(x => x.id === selected);
    const isRh = String(p?.protocol || p?.id || '').toLowerCase() === 'runninghub';
    return { providers, selected, isRh, models: [...document.querySelectorAll('#modelSelect option')].map(o => o.value), expectedModels: p?.image_models || [] };
  })()`);
  await online.close();
  const chat = await browser.newPage();
  chat.on('pageerror', e => errors.push('chat: ' + e.message));
  await chat.goto(`${B}/static/gpt-chat.html`, { waitUntil: 'domcontentloaded' });
  await chat.waitForFunction(`typeof apiProviders !== 'undefined' && apiProviders.length > 0`, { timeout: 20000 });
  await sleep(1500);
  const chatView = await chat.evaluate(`({ chatProviders: chatProviders().map(p => p.id), imageProviders: imageProviders().map(p => p.id),
    provider, activeChatModel, chatModelsOfProvider: (apiProviders.find(p => p.id === provider)?.chat_models || []) })`);
  await chat.close();
  const same = (a, b) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
  const checks = {
    configHasNoLegacyLists: legacyKeys.length === 0,
    onlineProviders: same(onlineView.providers, imageIds),
    onlineModels: onlineView.isRh || same(onlineView.models, onlineView.expectedModels),
    chatProviders: same(chatView.chatProviders, chatIds),
    chatModelFromProvider: chatView.chatModelsOfProvider.includes(chatView.activeChatModel),
    noPageErrors: errors.length === 0,
  };
  record('STANDALONE_pages_use_only_api_providers', Object.values(checks).every(Boolean), { checks, legacyKeys, onlineView, chatView, errors });
}

// ---------------- RunningHub：打开画布时只预取保存过参数的工作流，不再对没有参数的条目产生 404 ----------------
async function rhPrefetchSkipsUnsavedWorkflows(browser) {
  const cfg = (await api('GET', '/api/config')).json;
  const rh = (cfg.api_providers || []).find(p => p.id === 'runninghub' && p.enabled !== false);
  const nonEmpty = v => Array.isArray(v) ? v.length > 0 : Boolean(v && typeof v === 'object' && Object.keys(v).length);
  const entries = rh?.rh_workflows || [];
  const saved = entries.filter(e => nonEmpty(e.fields) || nonEmpty(e.workflowJson) || nonEmpty(e.raw)).map(e => String(e.workflowId || e.id));
  const unsaved = entries.map(e => String(e.workflowId || e.id)).filter(id => !saved.includes(id));
  const views = {};
  for (const kind of ['classic', 'smart']) {
    const id = await createTestCanvas(kind, kind === 'smart' ? smartNodes() : classicNodes());
    const page = await browser.newPage();
    const seen = [];
    page.on('response', r => {
      const m = new URL(r.url()).pathname.match(/^\/api\/runninghub\/workflows\/([^/]+)$/);
      if (m && r.request().method() === 'GET') seen.push({ id: decodeURIComponent(m[1]), status: r.status() });
    });
    await page.goto(`${B}/static/${kind === 'smart' ? 'smart-canvas' : 'canvas'}.html?id=${id}`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(`typeof canvas!=='undefined' && canvas && Array.isArray(nodes) && nodes.length > 0`, { timeout: 20000 });
    await sleep(4000);
    views[kind] = { requested: seen.map(s => s.id), notFound: seen.filter(s => s.status === 404).map(s => s.id) };
    await page.close();
  }
  const pass = unsaved.length > 0 && saved.length > 0
    && ['classic', 'smart'].every(k => views[k].notFound.length === 0
      && unsaved.every(u => !views[k].requested.includes(u))
      && saved.every(sv => views[k].requested.includes(sv)));
  record('RH_prefetch_skips_workflows_without_saved_params', pass, { saved, unsaved, views });
}

// ---------------- A4 + A6：设置页增删改平台/模型后，已打开的画布不刷新就能看到一致的下拉 ----------------
// 用真实的外壳页（index.html）嵌入设置页、普通画布、智能画布三个 iframe，和实际使用时的结构一致；
// 修复前外壳页会把设置页的通知再转发给每个 iframe，一次保存会让画布重复加载配置（A6）。
async function a4a6SettingsChangesReachOpenCanvases(browser) {
  const PID = 'audit-a4-provider';
  const classicId = await createTestCanvas('classic', [{ id: 'audit-g1', type: 'generator', x: 100, y: 100, apiProvider: '', model: '' }]);
  const smartId = await createTestCanvas('smart', smartNodes());
  const shell = await browser.newPage();
  shell.on('pageerror', e => log('pageerror(shell)', e.message));
  await shell.goto(`${B}/`, { waitUntil: 'domcontentloaded' });
  // 浏览器里残留的旧 key：以前普通画布会用它完全替代服务端的对话模型列表（A4）
  await shell.evaluate(`localStorage.setItem('canvas_chat_models_ordered', JSON.stringify(['audit-legacy-chat-model']))`);
  await shell.evaluate(`(() => {
    for (const [name, src] of [['audit-settings', '/static/api-settings.html'], ['audit-classic', '/static/canvas.html?id=${classicId}'], ['audit-smart', '/static/smart-canvas.html?id=${smartId}']]) {
      const f = document.createElement('iframe'); f.name = name; f.src = src; f.style.cssText = 'position:fixed;left:0;top:0;width:1400px;height:900px;z-index:99999;background:#fff';
      document.body.appendChild(f);
    }
  })()`);
  const frameOf = name => shell.frames().find(f => f.name() === name);
  let settingsFrame, classic, smart;
  for (let i = 0; i < 100 && !(settingsFrame && classic && smart); i++) { await sleep(200); settingsFrame = frameOf('audit-settings'); classic = frameOf('audit-classic'); smart = frameOf('audit-smart'); }
  await settingsFrame.waitForFunction(`typeof providers !== 'undefined' && Array.isArray(providers) && providers.length > 0 && typeof saveProviders === 'function'`, { timeout: 20000 });
  await classic.waitForFunction(`typeof canvas!=='undefined' && canvas && canvas.id===${JSON.stringify(classicId)} && nodes.length===1`, { timeout: 20000 });
  await smart.waitForFunction(`typeof canvas!=='undefined' && canvas && Array.isArray(nodes) && nodes.length===2`, { timeout: 20000 });
  await sleep(3000);
  // 按浏览器实际发出的 /api/config 请求计数（A6），修复前后的代码都能比较
  const configRequests = { classic: 0, smart: 0 };
  shell.on('request', r => {
    if (new URL(r.url()).pathname !== '/api/config') return;
    if (r.frame() === classic) configRequests.classic += 1;
    if (r.frame() === smart) configRequests.smart += 1;
  });
  const saveFromSettings = async mutate => {
    const ok = await settingsFrame.evaluate(`(async () => { ${mutate}; renderEditor(); return await saveProviders(); })()`);
    await sleep(3000);
    return ok;
  };
  const observe = async () => {
    const cfg = (await api('GET', '/api/config')).json;
    const providers = (cfg.api_providers || []).filter(p => p.enabled !== false && (p.image_models || []).length);
    const chatModels = [...new Set((cfg.api_providers || []).filter(p => p.enabled !== false).flatMap(p => p.chat_models || []).map(m => String(m).trim()).filter(Boolean))];
    const target = (cfg.api_providers || []).find(p => p.id === PID);
    const classicView = await classic.evaluate(`(() => {
      const el = document.querySelector('[data-id="audit-g1"]');
      const opts = sel => [...(el?.querySelectorAll(sel + ' option') || [])];
      return {
        providers: opts('.provider-select').filter(o => !o.textContent.startsWith('⚠') && o.value).map(o => o.value),
        stale: opts('.provider-select').filter(o => o.textContent.startsWith('⚠')).map(o => o.value),
        models: opts('.model-select').filter(o => !o.disabled).map(o => o.value),
        chatModels: allChatModels(),
      };
    })()`);
    const smartView = await smart.evaluate(`(() => ({
      providers: [...document.querySelectorAll('#dynamicParams [data-smart-param="provider_id"]')].map(b => b.dataset.smartValue),
      models: [...document.querySelectorAll('#dynamicParams [data-smart-param="model"]')].map(b => b.dataset.smartValue),
      selected: settings.provider_id,
    }))()`);
    return {
      expected: {
        classicProviders: providers.filter(p => p.id !== 'modelscope').map(p => p.id),
        smartProviders: providers.filter(p => p.id !== 'modelscope' && p.id !== 'volcengine').map(p => p.id),
        targetModels: target ? target.image_models : null,
        chatModels,
      },
      classic: classicView, smart: smartView,
    };
  };
  const steps = {};
  const loadsBefore = { ...configRequests };
  // 1) 新增平台（一个图片模型）
  steps.add = { saved: await saveFromSettings(`providers.push({id:${JSON.stringify(PID)}, name:'Audit A4', enabled:true, protocol:'openai', base_url:'http://127.0.0.1:9/v1', image_models:['audit-img-1'], chat_models:[], video_models:[]}); selectedId = ${JSON.stringify(PID)}`) };
  // 两个画布里都选中这个新平台（普通画布：节点上的平台下拉；智能画布：参数面板的平台选项）
  await classic.evaluate(`(() => { const s = document.querySelector('[data-id="audit-g1"] .provider-select'); s.value = ${JSON.stringify(PID)}; s.dispatchEvent(new Event('change', {bubbles:true})); })()`);
  await smart.evaluate(`document.querySelector('#dynamicParams [data-smart-param="provider_id"][data-smart-value=${JSON.stringify(PID)}]')?.click()`);
  await sleep(800);
  steps.add.view = await observe();
  // 2) 修改：给这个平台加一个模型
  steps.modify = { saved: await saveFromSettings(`providers.find(p => p.id === ${JSON.stringify(PID)}).image_models.push('audit-img-2'); selectedId = ${JSON.stringify(PID)}`) };
  steps.modify.view = await observe();
  // 3) 删除这个平台
  steps.remove = { saved: await saveFromSettings(`providers = providers.filter(p => p.id !== ${JSON.stringify(PID)}); selectedId = providers[0]?.id || ''`) };
  steps.remove.view = await observe();
  const loadsAfter = { ...configRequests };
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const checks = {
    saved: steps.add.saved && steps.modify.saved && steps.remove.saved,
    add: same(steps.add.view.classic.providers, steps.add.view.expected.classicProviders) && same(steps.add.view.smart.providers, steps.add.view.expected.smartProviders)
      && same(steps.add.view.classic.models, ['audit-img-1']) && same(steps.add.view.smart.models, ['audit-img-1']),
    modify: same(steps.modify.view.classic.providers, steps.modify.view.expected.classicProviders) && same(steps.modify.view.smart.providers, steps.modify.view.expected.smartProviders)
      && same(steps.modify.view.classic.models, ['audit-img-1', 'audit-img-2']) && same(steps.modify.view.smart.models, ['audit-img-1', 'audit-img-2']),
    remove: same(steps.remove.view.classic.providers, steps.remove.view.expected.classicProviders) && same(steps.remove.view.smart.providers, steps.remove.view.expected.smartProviders)
      && !steps.remove.view.smart.providers.includes(PID) && steps.remove.view.expected.targetModels === null,
    // A4：普通画布的对话模型只来自已配置的平台，不受 localStorage 旧 key 和旧全局列表影响
    chatModelsFromProviders: [steps.add, steps.modify, steps.remove].every(step => same(step.view.classic.chatModels, step.view.expected.chatModels)),
    // A6：三次保存，每个画布各请求 /api/config 3 次（每次保存一次），不再重复加载
    oneLoadPerSave: loadsAfter.classic - loadsBefore.classic === 3 && loadsAfter.smart - loadsBefore.smart === 3,
  };
  record('A4_A6_settings_changes_reach_open_canvases', Object.values(checks).every(Boolean), { checks, loadsBefore, loadsAfter, steps });
  await shell.evaluate(`localStorage.removeItem('canvas_chat_models_ordered')`).catch(() => {});
  await shell.close();
}

// ---------------- C7：编辑器打开时在列表里改名，编辑器之后的保存不能把名字改回去 ----------------
async function c7EditorKeepsListRename(browser, kind) {
  const smart = kind === 'smart';
  const id = await createTestCanvas(kind, smart ? smartNodes() : classicNodes());
  const page = smart ? await openSmart(browser, id, 2) : await openClassic(browser, id, 3);
  const puts = []; trackPuts(page, id, puts);
  const nodeId = smart ? 'audit-s1' : 'audit-n1';
  const c0 = await getCanvas(id);
  const renamed = `AUDIT-C7-${kind}-renamed-in-list`;
  // 列表页的改名方式（canvas-list.js → POST /meta），不推进 updated_at
  const meta = await api('POST', `/api/canvases/${id}/meta`, { title: renamed });
  // 编辑器随后连续两次编辑并保存：第一次的基线早于改名，第二次的基线晚于改名
  for (const step of [1, 2]) {
    await page.evaluate(`nodes.find(n => n.id === ${JSON.stringify(nodeId)}).x += 7; saveCanvas();`);
    await sleep(3000);
  }
  await sleep(smart ? 7000 : 2000); // 等编辑器轮询 meta（普通画布 2.5 秒、智能画布 8 秒）
  const c1 = await getCanvas(id);
  const shown = await page.evaluate(smart ? `document.getElementById('smartTitle')?.textContent || ''` : `document.getElementById('currentCanvasTitle')?.textContent || ''`);
  const pass = meta.status === 200 && c1.title === renamed && nodeX(c1, nodeId) === nodeX(c0, nodeId) + 14 && shown === renamed;
  record(`C7_${kind}_editor_save_keeps_list_rename`, pass, {
    serverTitle: c1.title, shownTitle: shown, nodeMoved: nodeX(c1, nodeId) - nodeX(c0, nodeId), puts: puts.map(p => p.status),
  });
  await page.close();
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

// ---------------- C10：普通画布编辑器删掉接不到界面的"选画布"旧代码后，打开、编辑、保存、同步标题都正常 ----------------
// 修复前每次自动保存后还会请求整个画布列表和回收站（/api/canvases、/api/canvases/trash），只为渲染一个不存在的列表。
async function c10ClassicEditorWithoutGateCode(browser) {
  const id = await createTestCanvas('classic', [{ id: 'audit-c10', type: 'text', x: 100, y: 100, text: 'c10' }]);
  const p = await browser.newPage();
  const errors = [], listRequests = [];
  p.on('pageerror', e => errors.push(String(e.message || e).slice(0, 200)));
  p.on('request', r => { const path = new URL(r.url()).pathname; if (path === '/api/canvases' || path === '/api/canvases/trash') listRequests.push(r.method() + ' ' + path); });
  await p.goto(`${B}/static/canvas.html?id=${id}`, { waitUntil: 'domcontentloaded' });
  await p.waitForFunction(`typeof canvas!=='undefined' && canvas && canvas.id===${JSON.stringify(id)} && nodes.length===1`, { timeout: 20000 });
  await sleep(1500);
  const before = (await getCanvas(id)).updated_at;
  await p.evaluate(`(() => { nodes[0].x = 345; scheduleSave(); })()`);
  let saved = null;
  for (let i = 0; i < 40; i++) { await sleep(250); const c = await getCanvas(id); if (c.updated_at !== before && nodeX(c, 'audit-c10') === 345) { saved = c; break; } }
  await sleep(1000);
  // 在列表页改名（/meta），编辑器靠 meta 轮询同步标题
  await api('POST', `/api/canvases/${id}/meta`, { title: 'AUDIT-c10-renamed' });
  let shownTitle = '';
  for (let i = 0; i < 40 && shownTitle !== 'AUDIT-c10-renamed'; i++) { await sleep(250); shownTitle = await p.evaluate(`document.getElementById('currentCanvasTitle')?.textContent || ''`); }
  await p.close();
  const pass = Boolean(saved) && !errors.length && !listRequests.length && shownTitle === 'AUDIT-c10-renamed';
  record('C10_classic_editor_saves_without_list_refetch', pass, { saved: Boolean(saved), errors, listRequests, shownTitle });
}

// ---------------- C10：普通画布编辑器的资产面板里能打开"资产库管理"弹窗 ----------------
// 修复前弹窗和管理代码都在，但唯一的入口按钮在早已拆走的"选画布"页面上，编辑器里没有任何办法打开它。
async function c10ClassicAssetManagerEntry(browser) {
  const id = await createTestCanvas('classic', [{ id: 'audit-am', type: 'text', x: 100, y: 100, text: 'am' }]);
  const p = await browser.newPage();
  await p.setViewport({ width: 1400, height: 900 });
  const errors = [], badApi = [];
  p.on('pageerror', e => errors.push(String(e.message || e).slice(0, 200)));
  p.on('response', r => { const u = new URL(r.url()); if (u.pathname.startsWith('/api/') && r.status() >= 400) badApi.push(r.status() + ' ' + u.pathname); });
  await p.goto(`${B}/static/canvas.html?id=${id}`, { waitUntil: 'domcontentloaded' });
  await p.waitForFunction(`typeof canvas!=='undefined' && canvas && canvas.id===${JSON.stringify(id)} && nodes.length===1`, { timeout: 20000 });
  await sleep(800);
  const hasButton = await p.evaluate(`Boolean(document.getElementById('canvasAssetManageBtn'))`);
  const res = { hasButton, opened: false, visible: false, tabs: {}, closed: false };
  if (hasButton) {
    await p.click('#canvasAssetToggle');
    await p.waitForFunction(`document.getElementById('canvasAssetPanel').classList.contains('open')`, { timeout: 5000 });
    await sleep(300);
    await p.click('#canvasAssetManageBtn');
    try { await p.waitForFunction(`document.getElementById('assetManagerModal').classList.contains('open') && document.getElementById('assetManagerBody').children.length > 0`, { timeout: 8000 }); res.opened = true; } catch (e) {}
    // 弹窗真的在最上层：面板中心点命中弹窗自身
    res.visible = await p.evaluate(`(() => { const panel = document.querySelector('#assetManagerModal .asset-manager-panel'); const b = panel.getBoundingClientRect();
      if (b.width < 100 || b.height < 100) return false; const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2); return Boolean(hit && panel.contains(hit)); })()`);
    for (const tab of ['workflows', 'prompts', 'assets']) {
      await p.click(`[data-manager-tab="${tab}"]`); await sleep(400);
      res.tabs[tab] = await p.evaluate(`document.querySelector('[data-manager-tab="${tab}"]').classList.contains('active') && document.getElementById('assetManagerBody').children.length > 0`);
    }
    await p.click('#assetManagerModal .asset-manager-head .preview-icon-btn');
    await sleep(300);
    res.closed = await p.evaluate(`!document.getElementById('assetManagerModal').classList.contains('open')`);
  }
  await p.close();
  const pass = hasButton && res.opened && res.visible && Object.values(res.tabs).every(Boolean) && Object.keys(res.tabs).length === 3 && res.closed && !errors.length && !badApi.length;
  record('C10_classic_asset_manager_opens_from_panel', pass, { ...res, errors, badApi });
}

// ---------------- A5 后续：设置页"恢复自动识别"按钮清除手动选择的图片接口 ----------------
// 修复前一旦手动选过图片接口就没有办法退回自动识别，只能删掉平台重建。
async function a5RestoreImageModeAuto(browser) {
  const AG = 'audit-auto-agnes', PL = 'audit-auto-plain', OFF = 'audit-auto-off';
  const seed = [
    { id: AG, name: 'AUDIT Agnes', base_url: 'https://apihub.agnes-ai.com/v1', protocol: 'openai', image_models: ['agnes-image-2.1-flash'], image_request_mode: 'openai', image_request_mode_manual: true },
    { id: PL, name: 'AUDIT Plain', base_url: 'https://audit-plain.example.test/v1', protocol: 'openai', image_models: ['audit-image-1'], image_request_mode: 'openai-responses', image_request_mode_manual: true },
    { id: OFF, name: 'AUDIT Off', base_url: 'https://audit-off.example.test/v1', protocol: 'openai', image_models: ['audit-image-1'], image_request_mode: 'openai', image_request_mode_manual: false },
  ];
  const p = await browser.newPage();
  await p.setViewport({ width: 1400, height: 900 });
  const errors = [];
  p.on('pageerror', e => errors.push(String(e.message || e).slice(0, 200)));
  const ready = `typeof providers !== 'undefined' && Array.isArray(providers) && providers.length > 0 && typeof saveProviders === 'function'`;
  await p.goto(`${B}/static/api-settings.html`, { waitUntil: 'domcontentloaded' });
  await p.waitForFunction(ready, { timeout: 20000 });
  // 先存入三个测试平台（两个曾手动选择过，一个没有），再重新打开页面，模拟"以前选过、现在回来改"
  const seeded = await p.evaluate(`(async () => { providers.push(...${JSON.stringify(seed)}); renderProviderList(); return await saveProviders(); })()`);
  await p.goto(`${B}/static/api-settings.html`, { waitUntil: 'domcontentloaded' });
  await p.waitForFunction(ready, { timeout: 20000 });
  await sleep(500);
  const hasButton = await p.evaluate(`Boolean(document.getElementById('imageRequestModeAutoBtn'))`);
  const view = async id => {
    await p.evaluate(`selectProvider(${JSON.stringify(id)})`); await sleep(200);
    return p.evaluate(`(() => { const b = document.getElementById('imageRequestModeAutoBtn'); const item = providers.find(x => x.id === ${JSON.stringify(id)});
      return { value: document.getElementById('imageRequestModeInput').value, visible: Boolean(b && !b.hidden && b.offsetWidth > 0), manual: item.image_request_mode_manual === true }; })()`);
  };
  const res = { seeded, hasButton, before: {}, after: {}, status: {}, saved: {} };
  res.before.off = await view(OFF);
  for (const [key, id] of [['agnes', AG], ['plain', PL]]) {
    res.before[key] = await view(id);
    if (hasButton && res.before[key].visible) await p.click('#imageRequestModeAutoBtn');
    await sleep(200);
    res.after[key] = await view(id);
    res.status[key] = await p.evaluate(`document.getElementById('status')?.textContent || ''`);
  }
  // 用页面上的"保存"按钮保存，再从服务端读回
  const putDone = p.waitForResponse(r => new URL(r.url()).pathname === '/api/providers' && r.request().method() === 'PUT', { timeout: 10000 }).catch(() => null);
  await p.click('.api-page-save-btn');
  const put = await putDone;
  const list = ((await api('GET', '/api/providers')).json || {}).providers || [];
  for (const [key, id] of [['agnes', AG], ['plain', PL], ['off', OFF]]) {
    const s = list.find(x => x.id === id) || {};
    res.saved[key] = { mode: s.image_request_mode, manual: s.image_request_mode_manual };
  }
  res.putStatus = put ? put.status() : null;
  // 清理：删掉测试平台
  await p.evaluate(`(async () => { providers = providers.filter(x => !String(x.id).startsWith('audit-auto-')); selectedId = providers[0]?.id || ''; renderProviderList(); return await saveProviders(); })()`);
  await p.close();
  const pass = res.seeded === true && hasButton
    && !res.before.off.visible
    && res.before.agnes.visible && res.before.agnes.value === 'openai'
    && !res.after.agnes.visible && !res.after.agnes.manual && res.after.agnes.value === 'openai-json'
    && res.before.plain.visible && !res.after.plain.visible && !res.after.plain.manual && res.after.plain.value === 'openai-responses'
    && res.putStatus === 200
    && res.saved.agnes.mode === 'openai-json' && res.saved.agnes.manual === false
    && res.saved.plain.mode === 'openai-responses' && res.saved.plain.manual === false
    && res.saved.off.manual === false && !errors.length;
  record('A5_settings_restore_image_mode_auto', pass, { ...res, errors });
}

// ---------------- C10：外壳页（index.html）的 WebSocket 断线后自动重连，重连后照常收到画布更新通知 ----------------
// 修复前断开一次就再也不连，之后打开的画布只能靠轮询发现别处的修改。
async function wsShellReconnects(browser) {
  const p = await browser.newPage();
  // 包一层 WebSocket，记录页面创建的每个连接和收到的消息类型（不改变行为）
  await p.evaluateOnNewDocument(`(() => {
    const Native = window.WebSocket; window.__ws = [];
    window.WebSocket = function(url, protocols) {
      const ws = protocols === undefined ? new Native(url) : new Native(url, protocols);
      const rec = { ws, url: String(url), messages: [] }; window.__ws.push(rec);
      ws.addEventListener('message', e => { try { rec.messages.push(JSON.parse(e.data).type); } catch (_) {} });
      return ws;
    };
    window.WebSocket.prototype = Native.prototype;
    Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  })()`);
  await p.goto(`${B}/`, { waitUntil: 'domcontentloaded' });
  const statsSockets = `window.__ws.filter(r => r.url.includes('/ws/stats') && r.url.includes('client_id=') && !r.url.includes('audit-'))`;
  await p.waitForFunction(`${statsSockets}.some(r => r.ws.readyState === 1)`, { timeout: 15000 });
  const firstCount = await p.evaluate(`${statsSockets}.length`);
  await p.evaluate(`${statsSockets}.forEach(r => r.ws.close())`);
  let reconnected = false;
  try { await p.waitForFunction(`${statsSockets}.length > ${firstCount} && ${statsSockets}.at(-1).ws.readyState === 1`, { timeout: 8000 }); reconnected = true; } catch (e) {}
  // 重连后在别处保存一张画布，新连接应收到 canvas_updated
  const id = await createTestCanvas('classic', [{ id: 'audit-ws', type: 'text', x: 1, y: 1, text: 'ws' }]);
  const c = await getCanvas(id);
  await api('PUT', `/api/canvases/${id}`, { nodes: [{ id: 'audit-ws', type: 'text', x: 2, y: 1, text: 'ws' }], connections: [], base_updated_at: c.updated_at, client_id: 'audit-ws-writer' });
  let gotUpdate = false;
  for (let i = 0; i < 20 && !gotUpdate; i++) { await sleep(250); gotUpdate = reconnected && await p.evaluate(`${statsSockets}.at(-1).messages.includes('canvas_updated')`); }
  const sockets = await p.evaluate(`${statsSockets}.length`);
  await p.close();
  record('WS_shell_socket_reconnects_and_keeps_receiving_updates', reconnected && gotUpdate, { firstCount, sockets, reconnected, gotUpdate });
}

// ---------------- C10：列表页改名、移动项目、拖动卡片保存失败时，界面恢复成修改前的样子 ----------------
// 修复前保存失败只提示"保存失败"，界面上留着没保存成功的标题/项目/位置，刷新后才变回去。
async function listMetaFailureRollsBack(browser) {
  const id = await createTestCanvas('classic', [{ id: 'audit-meta', type: 'text', x: 1, y: 1, text: 'm' }]);
  const original = (await getCanvas(id));
  const p = await browser.newPage();
  const errors = [];
  p.on('pageerror', e => errors.push(String(e.message || e).slice(0, 200)));
  await p.setRequestInterception(true);
  p.on('request', async r => {
    const url = new URL(r.url());
    if (r.method() === 'POST' && url.pathname === `/api/canvases/${id}/meta`) {
      const body = JSON.parse(r.postData() || '{}');
      if (body.title === 'AUDIT-meta-fail' || body.project === 'audit-fail-project' || body.board_x === 4321) {
        await sleep(body.title === 'AUDIT-meta-fail' ? 800 : 50);  // 让"先发出、后失败"的请求晚于下一次成功的修改返回
        return r.respond({ status: 500, contentType: 'application/json', body: '{"detail":"audit forced failure"}' });
      }
    }
    r.continue();
  });
  await p.goto(`${B}/static/canvas-list.html?project=${encodeURIComponent(original.project || 'default')}`, { waitUntil: 'domcontentloaded' });
  await p.waitForFunction(`typeof canvases!=='undefined' && canvases.some(c => c.id === ${JSON.stringify(id)})`, { timeout: 20000 });
  await sleep(800);
  const local = k => p.evaluate(`canvases.find(c => c.id === ${JSON.stringify(id)})[${JSON.stringify(k)}]`);
  // 1) 改名失败 -> 恢复
  await p.evaluate(`setCanvasTitle(${JSON.stringify(id)}, 'AUDIT-meta-fail')`);
  const titleAfterFail = await local('title');
  // 2) 先发一个会失败的改名，紧接着再改一次（成功）；旧请求晚到的失败不能覆盖新标题
  await p.evaluate(`setCanvasTitle(${JSON.stringify(id)}, 'AUDIT-meta-fail'); setCanvasTitle(${JSON.stringify(id)}, 'AUDIT-meta-ok')`);
  await sleep(1500);
  const titleAfterRace = await local('title');
  // 3) 移动到项目失败 -> 恢复
  await p.evaluate(`moveCanvasToProject(${JSON.stringify(id)}, 'audit-fail-project')`);
  const projectAfterFail = await local('project');
  // 4) 拖动卡片保存失败 -> 恢复位置
  const x0 = await local('board_x');
  await p.evaluate(`(() => { const c = canvases.find(c => c.id === ${JSON.stringify(id)}); const before = {board_x: c.board_x, board_y: c.board_y}; c.board_x = 4321; return persistMeta(c.id, {board_x: 4321, board_y: c.board_y}, before); })()`);
  const xAfterFail = await local('board_x');
  const server = await getCanvas(id);
  await p.close();
  const detail = { titleAfterFail, titleAfterRace, projectAfterFail, x0, xAfterFail, serverTitle: server.title, serverProject: server.project, errors };
  const pass = titleAfterFail === original.title && titleAfterRace === 'AUDIT-meta-ok' && server.title === 'AUDIT-meta-ok'
    && projectAfterFail === (original.project || 'default') && server.project === original.project && xAfterFail === x0 && !errors.length;
  record('C10_list_meta_failure_rolls_back', pass, detail);
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
    ['C8', () => c8SmartDeleteStaysDeleted(browser)], ['C8', () => c8ClassicSameOutputBothImages(browser)],
    ['RH', () => rhPrefetchSkipsUnsavedWorkflows(browser)],
    ['STANDALONE', () => standalonePagesUseProviders(browser)],
    ['OUTDATED', () => outdatedPageKeepsDraft(browser, 'classic')], ['OUTDATED', () => outdatedPageKeepsDraft(browser, 'smart')],
    ['A4', () => a4a6SettingsChangesReachOpenCanvases(browser)],
    ['C7', () => c7EditorKeepsListRename(browser, 'classic')], ['C7', () => c7EditorKeepsListRename(browser, 'smart')],
    ['C10', () => c10ClassicEditorWithoutGateCode(browser)],
    ['WS', () => wsShellReconnects(browser)],
    ['C10', () => listMetaFailureRollsBack(browser)],
    ['C10', () => c10ClassicAssetManagerEntry(browser)],
    ['A5', () => a5RestoreImageModeAuto(browser)],
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
