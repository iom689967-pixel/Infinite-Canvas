// 大画布性能测量：服务端 PUT、前端单次保存（含各阶段）、合并、拖动节点时的长任务与帧间隔、打开画布。
// 会对指定画布多次保存，只能在隔离实例上运行。
//
//   REPRO_BASE=http://127.0.0.1:3100 node tests/repro/perf_large_canvas.js <智能画布 id>
//
// 结果写入 REPRO_RESULTS（默认 tests/repro/results-perf.json）。
const { testBase, launchBrowser, writeResults, sleep } = require('./_browser');

const B = testBase();
const CID = process.argv[2];
if (!CID) { console.error('usage: node tests/repro/perf_large_canvas.js <smart canvas id>'); process.exit(2); }
const median = a => [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)];

const LONGTASK_OBSERVER = () => {
  window.__longTasks = [];
  new PerformanceObserver(list => list.getEntries().forEach(e => window.__longTasks.push({ start: Math.round(e.startTime), dur: Math.round(e.duration) })))
    .observe({ entryTypes: ['longtask'] });
};

async function openCanvas(browser) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  await page.evaluateOnNewDocument(LONGTASK_OBSERVER);
  const t0 = Date.now();
  await page.goto(`${B}/static/smart-canvas.html?id=${CID}`);
  await page.waitForFunction('typeof canvas!=="undefined" && canvas && Array.isArray(nodes) && nodes.length > 0', { timeout: 120000 });
  const ready = Date.now() - t0;
  await sleep(5000);
  return { page, ready };
}

(async () => {
  const out = { base: B, canvasId: CID };

  // 1) 服务端：直接 PUT 整张画布（不经浏览器）：上传 + 解析 + 加锁 + 原子写 + 广播
  const serverPut = [];
  for (let i = 0; i < 3; i++) {
    const cur = (await (await fetch(`${B}/api/canvases/${CID}`)).json()).canvas;
    const body = JSON.stringify({ nodes: cur.nodes, connections: cur.connections, logs: cur.logs, settings: cur.settings, viewport: cur.viewport, base_updated_at: cur.updated_at });
    out.requestBodyMB = +(Buffer.byteLength(body) / 1048576).toFixed(1);
    const t = Date.now();
    await (await fetch(`${B}/api/canvases/${CID}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body })).text();
    serverPut.push(Date.now() - t);
  }
  out.serverPutMs = { runs: serverPut, median: median(serverPut) };

  const browser = await launchBrowser();
  try {
    const { page, ready } = await openCanvas(browser);
    const loadTasks = await page.evaluate('window.__longTasks.splice(0)');
    out.load = { nodesReadyMs: ready, nodes: await page.evaluate('nodes.length'), longTaskCount: loadTasks.length, longTaskMaxMs: Math.max(0, ...loadTasks.map(t => t.dur)) };

    // 2) 前端单次保存：各阶段时间戳 + 期间的长任务
    out.save = await page.evaluate(async () => {
      const runs = [];
      for (let i = 0; i < 3; i++) {
        const marks = {}; const mark = n => { marks[n] = Math.round(performance.now()); };
        const origBuild = buildSmartSavePayload;
        buildSmartSavePayload = function () { mark('buildStart'); const x = origBuild(); mark('buildEnd'); return x; };
        const origFetch = window.fetch;
        window.fetch = async (u, init) => {
          const put = init && init.method === 'PUT';
          if (put) mark('putSent');
          const res = await origFetch(u, init);
          if (put) { mark('putHeaders'); const json = res.json.bind(res); res.json = async () => { mark('jsonStart'); const d = await json(); mark('jsonEnd'); return d; }; }
          return res;
        };
        window.__longTasks.splice(0);
        mark('start'); await saveCanvas(); mark('end');
        window.fetch = origFetch; buildSmartSavePayload = origBuild;
        await new Promise(r => setTimeout(r, 300));
        runs.push({ totalMs: marks.end - marks.start, buildMs: marks.buildEnd - marks.buildStart, waitServerMs: marks.putHeaders - marks.putSent,
          parseResponseMs: marks.jsonEnd - marks.jsonStart, longTasksMs: window.__longTasks.splice(0).map(t => t.dur) });
        await new Promise(r => setTimeout(r, 800));
      }
      return runs;
    });

    // 3) 主线程上各步骤的单独耗时
    out.breakdown = await page.evaluate(async () => {
      const med = a => [...a].sort((x, y) => x - y)[1];
      const r = { canvasForStorage: [], stringify: [], parse: [], render: [] };
      for (let i = 0; i < 3; i++) {
        let t = performance.now(); const sc = canvasForStorage(); r.canvasForStorage.push(performance.now() - t);
        t = performance.now(); JSON.stringify(sc); r.stringify.push(performance.now() - t);
        const text = await (await fetch('/api/canvases/' + canvasId)).text();
        t = performance.now(); JSON.parse(text); r.parse.push(performance.now() - t);
        t = performance.now(); render(); r.render.push(performance.now() - t);
      }
      return Object.fromEntries(Object.entries(r).map(([k, v]) => [k + 'Ms', Math.round(med(v))]));
    });

    // 4) 合并：拉取 + 解析、深拷贝基线、纯合并、applyMergedServerCanvas（含重新渲染）
    out.merge = await page.evaluate(async () => {
      const r = { fetchParseMs: [], cloneBaseMs: [], pureMergeMs: [], applyWithRenderMs: [] };
      for (let i = 0; i < 3; i++) {
        let t = performance.now();
        const server = (await (await fetch('/api/canvases/' + canvasId)).json()).canvas;
        r.fetchParseMs.push(Math.round(performance.now() - t));
        t = performance.now(); const clone = cloneSmartJson(server.nodes); r.cloneBaseMs.push(Math.round(performance.now() - t));
        t = performance.now(); mergeSmartNodeLists(nodes, clone.map(normalizeLegacySmartNode), smartSyncBaseNodes); r.pureMergeMs.push(Math.round(performance.now() - t));
        t = performance.now(); applyMergedServerCanvas(server); r.applyWithRenderMs.push(Math.round(performance.now() - t));
        await new Promise(res => setTimeout(res, 800));
      }
      return r;
    });
    await page.close();

    // 5) 拖动节点：从节点中心按下（标题栏不是拖动区域），60 步、约 2 秒；记录长任务和帧间隔
    out.drag = [];
    for (const kind of ['smart-image-generation', 'smart-image']) {
      const { page: p } = await openCanvas(browser);
      const info = await p.evaluate(kind => {
        const node = nodes.find(n => n.type === kind) || nodes[0];
        viewport.x = 500 - (node.x || 0) * 0.6; viewport.y = 250 - (node.y || 0) * 0.6; viewport.scale = 0.6; applyViewport(); render();
        const r = document.querySelector(`.image-node[data-id="${node.id}"]`).getBoundingClientRect();
        return { id: node.id, type: node.type, x0: node.x, y0: node.y, px: r.left + r.width / 2, py: r.top + r.height / 2 };
      }, kind);
      await sleep(1500);
      await p.evaluate(() => {
        window.__longTasks.splice(0); window.__frames = []; window.__frameLoop = true; let last = performance.now();
        const tick = t => { window.__frames.push(t - last); last = t; if (window.__frameLoop) requestAnimationFrame(tick); };
        requestAnimationFrame(tick);
      });
      const t0 = await p.evaluate('performance.now()');
      await p.mouse.move(info.px, info.py);
      await p.mouse.down();
      for (let i = 1; i <= 60; i++) { await p.mouse.move(info.px + i * 4, info.py + i * 2); await sleep(16); }
      await p.mouse.up();
      const t1 = await p.evaluate('performance.now()');
      const frames = (await p.evaluate('(window.__frameLoop = false, window.__frames.slice(1))')).sort((a, b) => a - b);
      await sleep(3500);
      const tasks = await p.evaluate('window.__longTasks.splice(0)');
      const after = await p.evaluate(id => { const n = nodes.find(n => n.id === id); return { x: n.x, y: n.y }; }, info.id);
      out.drag.push({ nodeType: info.type, moved: { dx: Math.round(after.x - info.x0), dy: Math.round(after.y - info.y0) }, dragMs: Math.round(t1 - t0),
        frameP50Ms: Math.round(frames[Math.floor(frames.length * 0.5)]), frameP95Ms: Math.round(frames[Math.floor(frames.length * 0.95)]), frameMaxMs: Math.round(frames.at(-1)),
        longTasksDuringDragMs: tasks.filter(t => t.start >= t0 && t.start <= t1).map(t => t.dur), longTasksAfterDropMs: tasks.filter(t => t.start > t1).map(t => t.dur) });
      await p.close();
    }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out, null, 1));
  console.log('results:', writeResults('results-perf.json', out));
})();
