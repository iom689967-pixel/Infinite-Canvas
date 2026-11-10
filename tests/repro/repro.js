// 审计复现脚本（前端 C1/C2/C3/C4 + 画布 PUT 语义 C6/C7）。
// 只对本脚本新建的 AUDIT-TEST 画布做写操作，结束时把它们移入回收站。
//
// 用法:
//   REPRO_BASE=http://127.0.0.1:3100 node tests/repro/repro.js
// 环境变量:
//   REPRO_BASE        被测服务地址（默认 http://127.0.0.1:3000；建议指向 run_test_instance.sh 启动的隔离实例）
//   PUPPETEER_CORE    puppeteer-core 模块路径（默认在 ~/.npm/_npx 缓存里查找）
//   CHROME_PATH       Chrome 可执行文件（默认 macOS 系统 Chrome）
//   REPRO_RESULTS     结果 JSON 输出路径（默认 tests/repro/results.json，已被 .gitignore 忽略）
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
const results = {};
const log = (...a) => console.log(new Date().toISOString().slice(11, 23), ...a);

async function api(method, url, body) {
  const res = await fetch(B + url, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  const text = await res.text();
  let json = null; try { json = JSON.parse(text); } catch (e) {}
  return { status: res.status, json, bytes: text.length };
}
const getCanvas = async id => (await api('GET', `/api/canvases/${id}`)).json.canvas;
const nodeX = (c, id) => (c.nodes.find(n => n.id === id) || {}).x;

async function createTestCanvas(kind, nodes) {
  const r = await api('POST', '/api/canvases', { title: `AUDIT-TEST-${kind}-${Date.now()}`, icon: kind === 'smart' ? 'sparkles' : '🧩', kind });
  const c = r.json.canvas;
  const full = await getCanvas(c.id);
  const put = await api('PUT', `/api/canvases/${c.id}`, { title: full.title, icon: full.icon, nodes, connections: [], viewport: { x: 0, y: 0, scale: 1 }, logs: [], settings: {}, base_updated_at: full.updated_at, client_id: 'audit-seed' });
  if (put.status !== 200) throw new Error('seed failed ' + put.status);
  return c.id;
}

function trackPuts(page, id, store) {
  page.on('response', res => {
    const req = res.request();
    if (req.method() === 'PUT' && req.url().includes(`/api/canvases/${id}`)) {
      let base = null; try { base = JSON.parse(req.postData() || '{}').base_updated_at; } catch (e) {}
      store.push({ t: Date.now(), status: res.status(), base });
    }
  });
}

async function openClassic(browser, id) {
  const p = await browser.newPage();
  p.on('pageerror', e => log('pageerror(classic)', e.message));
  await p.goto(`${B}/static/canvas.html?id=${id}`, { waitUntil: 'domcontentloaded' });
  await p.waitForFunction(`typeof canvas!=='undefined' && canvas && canvas.id===${JSON.stringify(id)} && nodes.length===2`, { timeout: 20000 });
  await sleep(1500);
  return p;
}

(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-first-run', '--no-default-browser-check'] });
  const created = [];
  try {
    // ================= C1 / C2：智能画布 =================
    const smartId = await createTestCanvas('smart', [
      { id: 'audit-s1', type: 'smart-text', x: 100, y: 100, w: 440, h: 250, title: 'S1', text: '' },
      { id: 'audit-s2', type: 'smart-text', x: 700, y: 100, w: 440, h: 250, title: 'S2', text: '' },
    ]);
    created.push(smartId);
    log('smart test canvas', smartId);

    const sp = await browser.newPage();
    sp.on('pageerror', e => log('pageerror(smart)', e.message));
    let phase = 'idle', putSeq = 0;
    await sp.setRequestInterception(true);
    sp.on('request', async req => {
      if (req.method() === 'PUT' && req.url().includes(`/api/canvases/${smartId}`)) {
        if (phase === 'c1') {
          putSeq++;
          const delay = putSeq === 1 ? 1500 : putSeq === 2 ? 3000 : 0; // A 先到服务器，B 后到（同一标签页内两次保存重叠）
          if (delay) await sleep(delay);
        }
        if (phase === 'c2') { return req.respond({ status: 500, contentType: 'application/json', body: '{"detail":"audit simulated 500"}' }); }
      }
      req.continue();
    });
    const smartPuts = []; trackPuts(sp, smartId, smartPuts);
    await sp.goto(`${B}/static/smart-canvas.html?id=${smartId}`, { waitUntil: 'domcontentloaded' });
    await sp.waitForFunction(`typeof canvas!=='undefined' && canvas && Array.isArray(nodes) && nodes.length===2`, { timeout: 20000 });
    await sleep(2500);

    // --- C1 ---
    const s0 = await getCanvas(smartId);
    const s1x0 = nodeX(s0, 'audit-s1'), s2x0 = nodeX(s0, 'audit-s2');
    smartPuts.length = 0; phase = 'c1';
    await sp.evaluate(`nodes.find(n=>n.id==='audit-s1').x += 111; scheduleSave();`); // 编辑 1 → 450ms 后保存 A
    await sleep(700);
    await sp.evaluate(`nodes.find(n=>n.id==='audit-s2').x += 222; scheduleSave();`); // 编辑 2 → 保存 B（A 仍在途）
    await sleep(7000);
    phase = 'idle';
    const s1 = await getCanvas(smartId);
    const local = await sp.evaluate(`({s1:nodes.find(n=>n.id==='audit-s1').x, s2:nodes.find(n=>n.id==='audit-s2').x})`);
    results.C1 = {
      expected: { s1: s1x0 + 111, s2: s2x0 + 222 },
      server: { s1: nodeX(s1, 'audit-s1'), s2: nodeX(s1, 'audit-s2') },
      pageLocal: local,
      puts: smartPuts.map(p => ({ status: p.status, base: p.base })),
    };
    results.C1.edit2Lost = results.C1.server.s2 !== s2x0 + 222;
    log('C1', JSON.stringify(results.C1));

    // --- C2 ---
    const c2before = await getCanvas(smartId);
    smartPuts.length = 0; phase = 'c2';
    const c2Requests = [];
    const reqListener = req => { if (req.method() === 'PUT' && req.url().includes(`/api/canvases/${smartId}`)) c2Requests.push(Date.now()); };
    sp.on('request', reqListener);
    await sp.evaluate(`nodes.find(n=>n.id==='audit-s1').x += 1000; scheduleSave();`);
    await sleep(2000);
    phase = 'idle'; // 后端恢复正常后再观察 6 秒，看是否自动重试
    await sleep(6000);
    sp.off('request', reqListener);
    const c2after = await getCanvas(smartId);
    const uiText = await sp.evaluate(`[...document.querySelectorAll('[class*="toast"],[class*="Toast"],.error-modal,[role="alert"]')].map(e=>e.innerText).filter(Boolean).join(' | ')`);
    results.C2 = {
      putAttempts: c2Requests.length,
      serverS1Before: nodeX(c2before, 'audit-s1'), serverS1After: nodeX(c2after, 'audit-s1'),
      pageLocalS1: await sp.evaluate(`nodes.find(n=>n.id==='audit-s1').x`),
      visibleErrorUi: uiText || '(none)',
    };
    log('C2', JSON.stringify(results.C2));
    await sp.close();

    // ================= C3 / C4：普通画布 =================
    const classicId = await createTestCanvas('classic', [
      { id: 'audit-n1', type: 'prompt', x: 100, y: 100, text: 'n1' },
      { id: 'audit-n2', type: 'prompt', x: 600, y: 100, text: 'n2' },
    ]);
    created.push(classicId);
    log('classic test canvas', classicId);

    // --- C3：后台标签页（轮询被跳过）编辑后保存遇到 409 ---
    {
      const p1 = await openClassic(browser, classicId);
      const p2 = await openClassic(browser, classicId);
      await p2.bringToFront();
      const p1Hidden = await p1.evaluate('document.hidden');
      await p1.evaluate('stopCanvasRemotePolling()'); // 等价于后台标签页：代码在 document.hidden 时跳过轮询
      const p1Puts = []; trackPuts(p1, classicId, p1Puts);
      const c0 = await getCanvas(classicId);
      const n1x0 = nodeX(c0, 'audit-n1'), n2x0 = nodeX(c0, 'audit-n2');
      await p2.evaluate(`nodes.find(n=>n.id==='audit-n1').x += 300; saveCanvas();`);
      await sleep(1200);
      const afterP2 = await getCanvas(classicId);
      await p1.evaluate(`nodes.find(n=>n.id==='audit-n2').x += 400; scheduleSave();`);
      await sleep(4000);
      const c3 = await getCanvas(classicId);
      results.C3 = {
        p1DocumentHiddenNaturally: p1Hidden,
        afterP2Save: { n1: nodeX(afterP2, 'audit-n1') },
        final: { n1: nodeX(c3, 'audit-n1'), n2: nodeX(c3, 'audit-n2') },
        expectedIfNoLoss: { n1: n1x0 + 300, n2: n2x0 + 400 },
        p1Puts: p1Puts.map(p => ({ status: p.status, base: p.base })),
      };
      results.C3.p2EditOverwritten = results.C3.final.n1 === n1x0;
      log('C3', JSON.stringify(results.C3));
      await p1.close(); await p2.close();
    }

    // --- C4 及删除两行后的副作用 ---
    // 模拟 index.html:1772-1776 的转发：页面内连 /ws/stats，把 canvas_updated 原样 postMessage 给本页
    const shim = `(() => { const ws = new WebSocket('ws://'+location.host+'/ws/stats?client_id=audit-shim-'+Math.random().toString(36).slice(2));
      ws.onmessage = e => { try { const d = JSON.parse(e.data); if (d.type === 'canvas_updated') { window.__auditMsgs = (window.__auditMsgs||0)+1; window.postMessage(d, '*'); } } catch(_){} };
      return new Promise(r => ws.onopen = r); })()`;
    const variants = {
      original: null,
      removedBothLines: `handleCanvasUpdatedMessage = function(data){
        if(!canvas || !data || data.type !== 'canvas_updated') return;
        if(data.client_id && data.client_id === CLIENT_ID) return;
        if(data.canvas_id !== canvas.id) return;
        const remoteUpdatedAt = Number(data.updated_at || 0);
        if(remoteUpdatedAt && remoteUpdatedAt <= Number(lastCanvasUpdatedAt || 0)) return;
        clearTimeout(remoteSyncTimer);
        remoteSyncTimer = setTimeout(syncRemoteCanvasNow, savingCanvasNow ? 700 : 120);
        setStatus('Syncing...'); }`,
      removedOnlyDirtyReset: `handleCanvasUpdatedMessage = function(data){
        if(!canvas || !data || data.type !== 'canvas_updated') return;
        if(data.client_id && data.client_id === CLIENT_ID) return;
        if(data.canvas_id !== canvas.id) return;
        const remoteUpdatedAt = Number(data.updated_at || 0);
        if(remoteUpdatedAt && remoteUpdatedAt <= Number(lastCanvasUpdatedAt || 0)) return;
        clearTimeout(saveTimer); saveTimer = null;
        clearTimeout(remoteSyncTimer);
        remoteSyncTimer = setTimeout(syncRemoteCanvasNow, savingCanvasNow ? 700 : 120);
        setStatus('Syncing...'); }`,
    };
    results.C4 = {};
    for (const [name, override] of Object.entries(variants)) {
      const p1 = await openClassic(browser, classicId);
      const p2 = await openClassic(browser, classicId);
      await p1.evaluate(shim);
      if (override) await p1.evaluate(override); // 仅在本测试页内存中替换函数，不改仓库代码
      const p1Puts = []; trackPuts(p1, classicId, p1Puts);
      const c0 = await getCanvas(classicId);
      const n1x0 = nodeX(c0, 'audit-n1'), n2x0 = nodeX(c0, 'audit-n2');
      await p1.evaluate(`nodes.find(n=>n.id==='audit-n2').x += 500; scheduleSave();`); // P1 编辑，500ms 防抖中
      await p2.evaluate(`nodes.find(n=>n.id==='audit-n1').x += 600; saveCanvas();`);   // P2 立即保存 → WS 广播
      await sleep(5000);
      const c4 = await getCanvas(classicId);
      const r = {
        broadcastsForwardedToP1: await p1.evaluate('window.__auditMsgs||0'),
        p1Puts: p1Puts.map(p => ({ status: p.status, base: p.base })),
        final: { n1: nodeX(c4, 'audit-n1'), n2: nodeX(c4, 'audit-n2') },
        expectedIfNoLoss: { n1: n1x0 + 600, n2: n2x0 + 500 },
        p1Local: await p1.evaluate(`({n1:nodes.find(n=>n.id==='audit-n1').x, n2:nodes.find(n=>n.id==='audit-n2').x, dirty:localCanvasDirty, saveTimerSet:!!saveTimer})`),
      };
      r.p1EditLost = r.final.n2 === n2x0;
      r.p2EditLost = r.final.n1 === n1x0;
      results.C4[name] = r;
      log('C4', name, JSON.stringify(r));
      await p1.close(); await p2.close();
    }

    // ================= 409 / PUT 语义（直接调 API，只针对测试画布） =================
    {
      const id = smartId;
      let c = await getCanvas(id);
      // 先给测试画布写入 logs / settings
      let r = await api('PUT', `/api/canvases/${id}`, { title: c.title, icon: c.icon, nodes: c.nodes, connections: c.connections, viewport: c.viewport, logs: [{ id: 'audit-log-1', prompt: 'x' }], settings: { audit: 1 }, base_updated_at: c.updated_at, client_id: 'audit' });
      c = await getCanvas(id);
      const api409 = {};
      r = await api('PUT', `/api/canvases/${id}`, { title: c.title, nodes: c.nodes, logs: c.logs, settings: c.settings, viewport: c.viewport, base_updated_at: c.updated_at - 1, client_id: 'audit' });
      api409.staleBase = { status: r.status, detailKeys: Object.keys(r.json?.detail || {}), returnsFullCanvas: !!r.json?.detail?.canvas, bytes: r.bytes };
      r = await api('PUT', `/api/canvases/${id}`, { title: c.title, nodes: c.nodes, logs: c.logs, settings: c.settings, viewport: c.viewport, base_updated_at: 0, client_id: 'audit' });
      api409.baseZeroBypassesCheck = { status: r.status };
      c = await getCanvas(id);
      r = await api('PUT', `/api/canvases/${id}`, { nodes: c.nodes, connections: c.connections, viewport: c.viewport, base_updated_at: c.updated_at, client_id: 'audit' });
      const after = await getCanvas(id);
      api409.omittedFields = { status: r.status, titleBefore: c.title, titleAfter: after.title, iconAfter: after.icon, logsBefore: c.logs.length, logsAfter: after.logs.length, settingsBefore: c.settings, settingsAfter: after.settings };
      // C7：meta 改名不推进 updated_at，随后编辑器带旧标题的 PUT 把名字改回去
      const beforeMeta = await getCanvas(id);
      await api('POST', `/api/canvases/${id}/meta`, { title: 'AUDIT-renamed-in-list' });
      const afterMeta = await getCanvas(id);
      r = await api('PUT', `/api/canvases/${id}`, { title: beforeMeta.title, icon: beforeMeta.icon, nodes: beforeMeta.nodes, connections: beforeMeta.connections, viewport: beforeMeta.viewport, logs: beforeMeta.logs, settings: beforeMeta.settings, base_updated_at: beforeMeta.updated_at, client_id: 'audit' });
      const afterPut = await getCanvas(id);
      api409.C7 = { updatedAtBeforeMeta: beforeMeta.updated_at, updatedAtAfterMeta: afterMeta.updated_at, titleAfterMeta: afterMeta.title, editorPutStatus: r.status, titleAfterEditorPut: afterPut.title };
      results.api = api409;
      log('API', JSON.stringify(api409));
    }
  } catch (e) {
    results.error = String(e && e.stack || e);
    log('ERROR', results.error);
  } finally {
    await browser.close();
    // 清理：测试画布移入回收站（可在回收站查看或彻底删除）
    for (const id of created) { const r = await api('DELETE', `/api/canvases/${id}`); log('trash test canvas', id, r.status); }
    results.testCanvases = created;
    results.base = B;
    fs.writeFileSync(RESULTS_PATH, JSON.stringify(results, null, 2));
    console.log('RESULTS ' + JSON.stringify(results));
  }
})();
