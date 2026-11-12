// 真实浏览器下的 Origin: null 与“本机其他端口”测试。
// 在一个随机本机端口上起一个“攻击页面”（本机来源，Chrome 的 Private Network Access 不会拦截它访问 127.0.0.1），
// 页面里：
//   - 沙箱 iframe（浏览器自动带 Origin: null）发不触发预检的 text/plain POST，以及会触发预检的 JSON POST；
//   - 页面本身（Origin: http://127.0.0.1:<随机端口>，即本机其他端口）发同样的两个请求。
// 期望：服务器拒绝全部请求，画布数量不变。只能在隔离实例上运行。
//
//   REPRO_BASE=http://127.0.0.1:3100 node tests/repro/null_origin_browser.js
const http = require('http');
const { testBase, launchBrowser, writeResults, sleep } = require('./_browser');

const B = testBase();
const TARGET = `${B}/api/canvases`;

const attackerPage = `<!doctype html><meta charset="utf-8"><script>window.results={};addEventListener('message',e=>Object.assign(results,e.data));</script>
<iframe sandbox="allow-scripts" srcdoc="<script>
  const T=${JSON.stringify(TARGET).replace(/"/g, '&quot;')};
  fetch(T,{method:'POST',mode:'no-cors',headers:{'Content-Type':'text/plain'},body:JSON.stringify({title:'EVIL-null-simple'})})
    .then(r=>parent.postMessage({nullSimple:'sent ('+r.type+')'},'*')).catch(e=>parent.postMessage({nullSimple:'error '+e.message},'*'));
  fetch(T,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'EVIL-null-json'})})
    .then(r=>parent.postMessage({nullJson:'status '+r.status},'*')).catch(e=>parent.postMessage({nullJson:'error '+e.message},'*'));
</script>"></iframe>
<script>
  const T=${JSON.stringify(TARGET)};
  fetch(T,{method:'POST',mode:'no-cors',headers:{'Content-Type':'text/plain'},body:JSON.stringify({title:'EVIL-local-port-simple'})})
    .then(r=>results.localPortSimple='sent ('+r.type+')').catch(e=>results.localPortSimple='error '+e.message);
  fetch(T,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'EVIL-local-port-json'})})
    .then(r=>results.localPortJson='status '+r.status).catch(e=>results.localPortJson='error '+e.message);
</script>`;

const countEvil = async () => (await (await fetch(TARGET)).json()).canvases.filter(c => String(c.title || '').startsWith('EVIL')).length;

(async () => {
  const server = http.createServer((req, res) => { res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(attackerPage); });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const attackerOrigin = `http://127.0.0.1:${server.address().port}`;
  const evilBefore = await countEvil();
  const browser = await launchBrowser();
  const wire = [];
  try {
    const page = await browser.newPage();
    page.on('response', r => { if (r.url().startsWith(TARGET)) wire.push({ method: r.request().method(), status: r.status() }); });
    await page.goto(`${attackerOrigin}/`);
    await sleep(3000);
    const frameResults = await page.evaluate('window.results');
    const evilAfter = await countEvil();
    const out = { base: B, attackerOrigin, frameResults, serverResponses: wire, evilCanvasesCreated: evilAfter - evilBefore,
      pass: evilAfter === evilBefore && wire.every(w => w.status === 403 || w.status === 400) };
    console.log(JSON.stringify(out, null, 1));
    console.log('results:', writeResults('results-null-origin.json', out));
    process.exitCode = out.pass ? 0 : 1;
  } finally {
    await browser.close();
    server.close();
  }
})();
