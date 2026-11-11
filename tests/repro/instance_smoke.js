// 实例模式端到端冒烟：登录 → 新建并打开普通画布和智能画布 → 修改并保存 → 模拟生成 → 刷新后确认结果还在。
// 只在 run_instance_mode.sh 启动的实例上运行（全新的临时数据目录、假平台、只监听 127.0.0.1）。
//
//   tests/repro/run_instance_mode.sh start 3110
//   IC_INSTANCE_DIR=... REPRO_INSTANCE_BASE=http://127.0.0.1:3110 node tests/repro/instance_smoke.js
//
// 生成请求（POST /api/canvas-image-tasks 与轮询）由浏览器拦截并返回模拟结果，不访问任何上游；
// 其余请求（登录、画布读写、配置）都走真实的实例后端：鉴权 Cookie、CSRF、路由与静态文件白名单都会经过。
// 每一步单独记录通过与否；发现的问题只记录，不在这里修。
const fs = require('fs');
const path = require('path');
const { launchBrowser, writeResults, sleep } = require('./_browser');

const BASE = (process.env.REPRO_INSTANCE_BASE || 'http://127.0.0.1:3110').replace(/\/$/, '');
const port = Number(new URL(BASE).port);
if (port === 3000 || !/^http:\/\/127\.0\.0\.1:/.test(BASE)) {
  console.error('refusing to run: use an instance started by run_instance_mode.sh on 127.0.0.1 (never port 3000)');
  process.exit(2);
}
const DIR = process.env.IC_INSTANCE_DIR || path.join(process.env.TMPDIR || '/tmp', 'ic-instance-smoke');
const PASSWORD = fs.readFileSync(path.join(DIR, 'password'), 'utf8').trim();
const USER = 'smoke';
const PROVIDER = 'smoke-images';

const steps = [];
function step(name, pass, details = {}) {
  steps.push({ name, pass: Boolean(pass), details });
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}  ${JSON.stringify(details).slice(0, 400)}`);
}

// 记录页面上的问题：JS 报错、4xx/5xx 响应（模拟生成产生的图片 404 除外）、写请求是否带 CSRF 头
function watch(page, label, sink) {
  page.on('pageerror', e => sink.pageErrors.push(`${label}: ${e.message}`));
  page.on('response', r => {
    const url = r.url().replace(BASE, '');
    // 模拟生成的图片文件并不存在：它本身和它的预览（/api/media-preview?url=...mock-smoke-...）返回 404 是预期的
    if (r.status() >= 400 && !/mock-smoke-/.test(decodeURIComponent(url))) sink.badResponses.push(`${label}: ${r.request().method()} ${url.split('?')[0]} -> ${r.status()}`);
  });
  page.on('request', r => {
    const method = r.method();
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && r.url().startsWith(BASE)) {
      sink.writes.push({ label, method, url: r.url().replace(BASE, '').split('?')[0], csrf: Boolean(r.headers()['x-csrf-token']) });
    }
  });
}

// 生成接口的模拟：返回任务号，轮询时返回一张图片；记录提交时是否带 CSRF 头
async function mockGeneration(page, prefix) {
  const ctl = { submits: [], seq: 0 };
  await page.setRequestInterception(true);
  page.on('request', req => {
    const url = req.url();
    if (req.method() === 'POST' && /\/api\/canvas-image-tasks$/.test(url)) {
      ctl.submits.push({ csrf: Boolean(req.headers()['x-csrf-token']), body: JSON.parse(req.postData() || '{}') });
      return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ task_id: `${prefix}-${++ctl.seq}`, status: 'queued' }) });
    }
    if (req.method() === 'GET' && url.includes(`/api/canvas-image-tasks/${prefix}-`)) {
      const id = decodeURIComponent(url.split('/').pop().split('?')[0]);
      return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ id, status: 'succeeded', result: { images: [`/assets/output/${id}.png`] } }) });
    }
    return req.continue();
  });
  return ctl;
}

const inPage = (page, fn, ...args) => page.evaluate(fn, ...args);
const pageFetch = (page, url, init) => inPage(page, async (u, i) => {
  const r = await fetch(u, i ? { ...i, headers: { 'Content-Type': 'application/json', ...(i.headers || {}) } } : undefined);
  let json = null; try { json = await r.json(); } catch (_) { /* 非 JSON */ }
  return { status: r.status, json };
}, url, init);
const getCanvas = async (page, id) => (await pageFetch(page, `/api/canvases/${id}`)).json?.canvas || null;

(async () => {
  const sink = { pageErrors: [], badResponses: [], writes: [] };
  const browser = await launchBrowser();
  const out = { base: BASE };
  try {
    const page = await browser.newPage();
    watch(page, 'workspace', sink);

    // 1. 未登录访问首页 → 登录页；用登录表单登录
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
    step('unauthenticated_redirects_to_login', new URL(page.url()).pathname === '/login', { url: page.url().replace(BASE, '') });
    await page.type('#instance-login input[name=username]', USER);
    await page.type('#instance-login input[name=password]', PASSWORD);
    await Promise.all([page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 20000 }).catch(() => null), page.click('#instance-login button[type=submit]')]);
    await sleep(1500);
    const me = await pageFetch(page, '/api/auth/me');
    step('login_via_form', new URL(page.url()).pathname !== '/login' && me.status === 200 && me.json?.username === USER,
      { url: page.url().replace(BASE, ''), me: me.status, role: me.json?.role, hasCsrf: Boolean(me.json?.csrf) });

    // 2. 通过页面（带实例的 CSRF 补丁）新建两张画布并写入节点
    const created = {};
    for (const kind of ['classic', 'smart']) {
      const r = await pageFetch(page, '/api/canvases', { method: 'POST', body: JSON.stringify({ title: `SMOKE-${kind}`, kind }) });
      created[kind] = (r.json?.canvas || r.json || {}).id;
      step(`create_${kind}_canvas`, r.status === 200 && created[kind], { status: r.status });
    }
    const classicNodes = [
      { id: 'smoke-n1', type: 'prompt', x: 100, y: 100, text: 'a cat' },
      { id: 'smoke-g1', type: 'generator', x: 600, y: 100, apiProvider: PROVIDER, model: 'gpt-image-2' },
      { id: 'smoke-out', type: 'output', x: 1100, y: 100, images: [] },
    ];
    const smartNodes = [
      { id: 'smoke-sg', type: 'smart-image-generation', x: 100, y: 100, w: 440, h: 250, title: 'G',
        runSettings: { engine: 'api', apiKind: 'image', provider_id: PROVIDER, model: 'gpt-image-2' } },
      { id: 'smoke-st', type: 'smart-text', x: 700, y: 100, w: 440, h: 250, title: 'T', text: '' },
    ];
    for (const [kind, nodes, connections] of [
      ['classic', classicNodes, [{ id: 'smoke-e1', from: 'smoke-n1', to: 'smoke-g1' }, { id: 'smoke-e2', from: 'smoke-g1', to: 'smoke-out' }]],
      ['smart', smartNodes, []],
    ]) {
      const cur = await getCanvas(page, created[kind]);
      const r = await pageFetch(page, `/api/canvases/${created[kind]}`, { method: 'PUT', body: JSON.stringify({ nodes, connections, base_updated_at: cur?.updated_at || 0 }) });
      step(`seed_${kind}_canvas`, r.status === 200, { status: r.status });
    }
    const providers = await pageFetch(page, '/api/providers');
    step('providers_visible_to_instance_user', providers.status === 200 && (providers.json?.providers || []).some(p => p.id === PROVIDER),
      { status: providers.status, ids: (providers.json?.providers || []).map(p => p.id) });

    // 3. 普通画布：打开、修改并保存、模拟生成、刷新后检查
    {
      const p = await browser.newPage();
      watch(p, 'classic', sink);
      const gen = await mockGeneration(p, 'mock-smoke-c');
      await p.goto(`${BASE}/static/canvas.html?id=${created.classic}`, { waitUntil: 'domcontentloaded' });
      const opened = await p.waitForFunction(`typeof canvas!=='undefined' && canvas && canvas.id===${JSON.stringify(created.classic)} && nodes.length===3`, { timeout: 20000 }).then(() => true, () => false);
      step('classic_canvas_opens', opened, { url: p.url().replace(BASE, '') });
      if (opened) {
        await sleep(1500);
        await p.evaluate(`nodes.find(n => n.id === 'smoke-n1').x += 25; saveCanvas();`);
        await sleep(2500);
        const afterSave = await getCanvas(page, created.classic);
        step('classic_canvas_saves', (afterSave?.nodes || []).find(n => n.id === 'smoke-n1')?.x === 125, { n1x: (afterSave?.nodes || []).find(n => n.id === 'smoke-n1')?.x });
        await p.evaluate(`void runGenerator('smoke-g1')`);
        await sleep(9000);
        const afterGen = await getCanvas(page, created.classic);
        const urls = ((afterGen?.nodes || []).find(n => n.id === 'smoke-out')?.images || []).map(i => i?.url || i);
        step('classic_generation_result_saved', gen.submits.length >= 1 && urls.some(u => u.includes('mock-smoke-c-1')),
          { submits: gen.submits.length, submitCsrf: gen.submits.map(s => s.csrf), provider: gen.submits[0]?.body?.provider_id, outputImages: urls, logs: (afterGen?.logs || []).length });
        await p.reload({ waitUntil: 'domcontentloaded' });
        const reloaded = await p.waitForFunction(`typeof canvas!=='undefined' && canvas && nodes.length>=3`, { timeout: 20000 }).then(() => true, () => false);
        const localUrls = reloaded ? await p.evaluate(`(nodes.find(n => n.id === 'smoke-out')?.images || []).map(i => i?.url || i)`) : [];
        step('classic_result_survives_reload', localUrls.some(u => u.includes('mock-smoke-c-1')), { localUrls });
      }
      await p.close();
    }

    // 4. 智能画布：打开、修改并保存、模拟生成、刷新后检查
    {
      const p = await browser.newPage();
      watch(p, 'smart', sink);
      const gen = await mockGeneration(p, 'mock-smoke-s');
      await p.goto(`${BASE}/static/smart-canvas.html?id=${created.smart}`, { waitUntil: 'domcontentloaded' });
      const opened = await p.waitForFunction(`typeof canvas!=='undefined' && canvas && Array.isArray(nodes) && nodes.length===2`, { timeout: 20000 }).then(() => true, () => false);
      step('smart_canvas_opens', opened, { url: p.url().replace(BASE, '') });
      if (opened) {
        await sleep(2500);
        await p.evaluate(`nodes.find(n => n.id === 'smoke-st').x += 30; saveCanvas();`);
        await sleep(3000);
        const afterSave = await getCanvas(page, created.smart);
        step('smart_canvas_saves', (afterSave?.nodes || []).find(n => n.id === 'smoke-st')?.x === 730, { stx: (afterSave?.nodes || []).find(n => n.id === 'smoke-st')?.x });
        await p.evaluate(`selectedId='smoke-sg'; updateComposer(); setPromptText('a cat'); void runGeneration({});`);
        await sleep(12000);
        const afterGen = await getCanvas(page, created.smart);
        const g = (afterGen?.nodes || []).find(n => n.id === 'smoke-sg');
        const urls = (g?.images || []).map(i => i?.url || i);
        const history = (g?.generationHistory || []).map(h => h.status);
        const toast = await p.evaluate(`document.getElementById('toast')?.textContent || ''`);
        step('smart_generation_result_saved', gen.submits.length >= 1 && urls.some(u => u.includes('mock-smoke-s-1')) && history.includes('success'),
          { submits: gen.submits.length, submitCsrf: gen.submits.map(s => s.csrf), provider: gen.submits[0]?.body?.provider_id, images: urls, history, toast: toast.slice(0, 160) });
        await p.reload({ waitUntil: 'domcontentloaded' });
        const reloaded = await p.waitForFunction(`typeof canvas!=='undefined' && canvas && Array.isArray(nodes) && nodes.length>=2`, { timeout: 20000 }).then(() => true, () => false);
        const localUrls = reloaded ? await p.evaluate(`(nodes.find(n => n.id === 'smoke-sg')?.images || []).map(i => i?.url || i)`) : [];
        step('smart_result_survives_reload', localUrls.some(u => u.includes('mock-smoke-s-1')), { localUrls });
      }
      await p.close();
    }

    // 5. 汇总：页面报错、异常响应、写请求是否都带 CSRF 头
    const writesWithoutCsrf = sink.writes.filter(w => !w.csrf && w.url !== '/api/auth/login');
    step('no_page_errors', sink.pageErrors.length === 0, { pageErrors: sink.pageErrors.slice(0, 10) });
    step('no_error_responses', sink.badResponses.length === 0, { badResponses: [...new Set(sink.badResponses)].slice(0, 20) });
    step('all_writes_carry_csrf', writesWithoutCsrf.length === 0, { writes: sink.writes.length, withoutCsrf: writesWithoutCsrf.slice(0, 10) });
    await page.close();
  } finally {
    await browser.close();
  }
  out.steps = steps;
  out.passed = steps.filter(s => s.pass).length;
  out.total = steps.length;
  console.log(`\n${out.passed}/${out.total} steps passed`);
  console.log('results:', writeResults('results-instance-smoke.json', out));
  process.exitCode = out.passed === out.total ? 0 : 1;
})();
