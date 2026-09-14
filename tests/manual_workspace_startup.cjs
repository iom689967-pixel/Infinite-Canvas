/* Real Chromium + real temporary supervisor. Run with Playwright on NODE_PATH. */
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const path=require('node:path');
const output=process.env.MIO_BROWSER_OUTPUT||'/private/tmp/mio-workspace-entry-browser';
fs.mkdirSync(output,{recursive:true});
const fixture=spawn('.venv/bin/python',['tests/manual_workspace_startup.py'],{stdio:['pipe','pipe','pipe']});
const lines=[],waiters=[];
readline.createInterface({input:fixture.stdout}).on('line',line=>{const item=JSON.parse(line);if(waiters.length)waiters.shift()(item);else lines.push(item);});
const next=()=>lines.length?Promise.resolve(lines.shift()):new Promise(resolve=>waiters.push(resolve));
const command=async(operation,username)=>{fixture.stdin.write(JSON.stringify({operation,username})+'\n');return next();};
let errors='';fixture.stderr.on('data',data=>errors+=data);
let browser;
const failures=[];
(async()=>{
    const {origin}=await next();
    browser=await chromium.launch({headless:true});
    const context=await browser.newContext({viewport:{width:1440,height:960}});
    await context.addInitScript(()=>{
        window.__entryTrace=[];
        const original=window.fetch.bind(window);
        window.fetch=async(input,options)=>{
            const path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
            try{const r=await original(input,options);const entry={path,status:r.status};if(r.status>=400)try{entry.detail=(await r.clone().json()).detail;}catch(_){}window.__entryTrace.push(entry);return r;}
            catch(e){window.__entryTrace.push({path,exception:e.name});throw e;}
        };
        if(navigator.locks){const request=navigator.locks.request.bind(navigator.locks);navigator.locks.request=async(...args)=>{
            try{return await request(...args);}catch(e){window.__entryTrace.push({lock_exception:e.name});throw e;}
        };}
    });
    const page=await context.newPage();const navigation=[],statuses=[];
    page.on('framenavigated',frame=>{if(frame===page.mainFrame())navigation.push(new URL(frame.url()).pathname);});
    page.on('response',async r=>{const pathname=new URL(r.url()).pathname;if(pathname.startsWith('/api/'))statuses.push(r.status());if(r.status()>=400){let detail;try{detail=(await r.json()).detail;}catch(_){}failures.push({path:pathname,status:r.status(),detail});}});
    const password='Workspace-browser-fixture-42';
    async function ready(p=page){
        await p.waitForFunction(()=>window.WorkspaceStartup?.state==='ready');
        await p.waitForFunction(()=>document.getElementById('frame-canvas')?.dataset.loadState==='ready');
        await p.frameLocator('#frame-canvas').locator('.ws-project-row').first().waitFor({state:'visible'});
    }
    async function form(p,kind,name){await p.goto(origin+'/'+kind);await p.locator('input[name=username]').fill(name);await p.locator('input[name=password]').fill(password);if(kind==='register')await p.locator('input[name=confirmation]').fill(password);const started=Date.now();await p.locator('button[type=submit]').click();await p.waitForURL(origin+'/');await p.locator('#studioSidebar').waitFor({state:'visible'});const shell=Date.now()-started;await ready(p);return {shell_ms:shell,canvas_ms:Date.now()-started};}
    async function logout(p=page){
        await p.locator('#instance-logout').click();
        await p.waitForURL(origin+'/login',{waitUntil:'commit'});
        await p.locator('input[name=username]').waitFor({state:'visible'});
    }
    const registered=await form(page,'register','entry-alice');
    console.log(JSON.stringify({stage:'registered',...registered}));
    const canvas=await page.evaluate(async()=>{const r=await fetch('/api/canvases',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'Entry acceptance A',kind:'smart'})});if(!r.ok)throw Error('Create fixture canvas');return (await r.json()).canvas.id;});
    await logout();const warmBefore=await command('status');
    const warm=await form(page,'login','entry-alice');const warmAfter=await command('status');
    console.log(JSON.stringify({stage:'warm',...warm}));
    assert.equal(warmBefore.users[0].pid,warmAfter.users[0].pid);
    await page.frameLocator('#frame-canvas').getByText('Entry acceptance A',{exact:true}).waitFor({state:'visible'});
    assert.equal(await page.locator('#workspace-startup').isVisible(),false);
    await page.screenshot({path:path.join(output,'warm.png')});
    await logout();await command('stop','entry-alice');
    let coldBeforeReady=false;
    // Hold the start request for 1.2 s to deterministically inspect the real
    // cold shell before the stopped worker starts. No app/source changes.
    await page.route('**/api/beta/enter',async route=>{await new Promise(resolve=>setTimeout(resolve,1200));await route.continue();});
    const coldTask=form(page,'login','entry-alice');
    await page.waitForURL(origin+'/');await page.locator('#studioSidebar').waitFor({state:'visible'});
    await page.waitForFunction(()=>window.WorkspaceStartup?.state==='starting');
    await page.frameLocator('#frame-canvas').locator('#boardProjectName').waitFor({state:'visible'});
    await page.locator('#workspace-startup').waitFor({state:'visible'});
    assert.equal(await page.frameLocator('#frame-canvas').locator('#boardEmptyHint').isVisible(),false);
    coldBeforeReady=await page.evaluate(()=>window.WorkspaceStartup.state==='starting' && !!document.getElementById('frame-canvas').contentDocument?.getElementById('boardProjectName'));
    const coldReads=await page.frameLocator('#frame-canvas').locator('body').evaluate(()=>window.__entryTrace.filter(x=>x.path.startsWith('/api/')).length);
    assert.equal(coldReads,0);
    await page.screenshot({path:path.join(output,'cold-shell.png')});
    const cold=await coldTask;await page.unroute('**/api/beta/enter');assert(coldBeforeReady);
    await page.frameLocator('#frame-canvas').getByText('Entry acceptance A',{exact:true}).waitFor({state:'visible'});
    console.log(JSON.stringify({stage:'cold',...cold}));
    await page.screenshot({path:path.join(output,'cold-ready.png')});
    assert.equal(await page.locator('text=正在启动你的工作区').count(),0);
    const tabsBefore=await command('status');
    const other=await context.newPage();await Promise.all([page.reload(),other.goto(origin+'/')]);await Promise.all([ready(),ready(other)]);
    const tabsAfter=await command('status');assert.equal(tabsBefore.started_events,tabsAfter.started_events);assert.equal(tabsBefore.users[0].pid,tabsAfter.users[0].pid);
    await Promise.all([page.goto('about:blank'),other.goto('about:blank')]);await command('stop','entry-alice');
    await Promise.all([page.goto(origin+'/'),other.goto(origin+'/')]);await Promise.all([ready(),ready(other)]);
    const coldTabs=await command('status');assert.equal(coldTabs.started_events,tabsAfter.started_events+1);
    for(const p of [page,other])assert.equal(await p.evaluate(async()=> (await fetch('/api/canvases',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'Cold tab fixture'})})).status),200);
    await other.close();
    await page.route('**/api/beta/enter',route=>route.fulfill({status:503,contentType:'application/json',body:'{"detail":"fixture startup failure"}'}));
    await page.reload();await page.locator('#workspace-startup-retry').waitFor({state:'visible'});
    assert.equal(new URL(page.url()).pathname,'/');assert(await page.locator('#studioSidebar').isVisible());
    await page.frameLocator('#frame-canvas').locator('#boardProjectName').waitFor({state:'visible'});
    assert.equal(await page.frameLocator('#frame-canvas').locator('#boardEmptyHint').isVisible(),false);
    await page.screenshot({path:path.join(output,'retry.png')});
    await page.unroute('**/api/beta/enter');await page.locator('#workspace-startup-retry').click();await ready();
    await page.frameLocator('#frame-canvas').getByText('Entry acceptance A',{exact:true}).waitFor({state:'visible'});
    console.log(JSON.stringify({stage:'retry_passed'}));
    const wsBefore=await command('status');
    await page.evaluate(()=>{window.__entrySocketOpens=0;window.__entrySocket=new MioWorkspaceSocket(location.origin.replace('http','ws')+'/ws/stats');window.__entrySocket.onopen=()=>window.__entrySocketOpens++;});
    await page.waitForFunction(()=>window.__entrySocketOpens===1);await command('restart-gateway');
    await page.waitForFunction(()=>window.__entrySocketOpens>=2);const wsAfter=await command('status');
    assert.equal(wsBefore.users[0].pid,wsAfter.users[0].pid);assert.equal(wsBefore.supervisor_pid,wsAfter.supervisor_pid);
    assert.equal(await page.evaluate(async()=> (await fetch('/api/auth/me')).status),200);
    const bContext=await browser.newContext();const b=await bContext.newPage();await form(b,'register','entry-bob');
    const isolated=await b.evaluate(async id=>({list:(await (await fetch('/api/canvases')).json()).canvases.length,direct:(await fetch('/api/canvases/'+id)).status,providers:(await (await fetch('/api/instance/providers')).json()).providers.length}),canvas);
    assert.deepEqual(isolated,{list:0,direct:404,providers:0});await logout(b);
    await logout();const unauth=await page.evaluate(async()=>{const out={};for(const p of ['/api/beta/me','/api/auth/me','/api/canvases','/api/instance/providers'])out[p]=(await fetch(p)).status;return out;});
    assert(Object.values(unauth).every(x=>x===401));assert(!navigation.includes('/workspace'));
    const report={registered,warm,cold:{...cold,fixture_delay_ms:1200,shell_before_ready:coldBeforeReady},full_ui:true,no_startup_navigation:true,warm_pid_unchanged:true,multitab_no_duplicate_start:true,failure_retry_in_canvas:true,gateway_restart_preserved_instance_and_session:true,websocket_reconnected:true,ab_isolation:isolated,logout_401:unauth,real_model_calls:0};
    fs.writeFileSync(path.join(output,'acceptance.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
})().catch(async e=>{
    console.error(e);process.exitCode=1;
    if(browser)for(const context of browser.contexts())for(const page of context.pages()){
        const state=await page.evaluate(()=>({path:location.pathname,state:window.WorkspaceStartup?.state,readyState:document.readyState,trace:window.__entryTrace,
            frames:[...document.querySelectorAll('iframe')].map(f=>({id:f.id,src:f.getAttribute('src'),state:f.dataset.loadState}))})).catch(()=>({unavailable:true}));
        console.error(JSON.stringify({state,failures}));
        await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});
    }
    console.error(JSON.stringify(await command('status')));
}).finally(async()=>{if(browser)await browser.close();fixture.stdin.write('{"operation":"finish"}\n');fixture.stdin.end();await new Promise(resolve=>fixture.on('exit',resolve));if(errors)process.stderr.write(errors);});
