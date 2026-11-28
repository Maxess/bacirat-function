// The exact packaged page and assets, a test-only SQLite school, and real Chromium.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {packaged, serveBrowser, send, skip} = require('./fixtures/classroom-browser.cjs');
const lite = {view:'lite'};
const file = {route:'content',light:'1',lesson:'a'.repeat(32),epoch:'e'.repeat(24),doc:'b'.repeat(32)};

test('light mode has no media session, is read-only and enforces live class and attachment scope', {skip,timeout:30_000}, t=>{
    const f=packaged(t);
    const page=f.entryRequest({query:lite});
    assert.equal(page.status,200);
    assert.match(page.body,/آخرین پیام‌های کلاس/);
    assert.match(page.body,/صفحهٔ ۲.*از فایل/);
    assert.match(page.body,/&lt;b&gt;پیام تازهٔ آزمون&lt;\/b&gt;/);
    assert.equal((page.body.match(/<li>/g)||[]).length,16);
    assert.equal((page.body.match(/<path /g)||[]).length,1,'erased stroke is not published by the snapshot');
    assert.doesNotMatch(page.body, /<script|<iframe|<audio|<video|data-csrf|livekit-client/);
    assert.ok(Buffer.byteLength(page.body)<16*1024);
    for (const phase of ['starting','paused','closing','stale']) {
        const r=f.entryRequest({query:lite,phase});
        assert.equal(r.status,200); assert.match(r.body,/در انتظار کلاس/);
        assert.doesNotMatch(r.body,/worksheet\.pdf|پیام تازهٔ آزمون/);
    }
    for (const actor of [0,2]) {
        const r=f.entryRequest({query:lite,actor});
        assert.equal(r.status,actor===0?401:403); assert.doesNotMatch(r.body,/worksheet\.pdf|پیام تازهٔ آزمون/);
    }
    const empty=f.entryRequest({query:lite,phase:'empty'});
    assert.equal(empty.status,200); assert.doesNotMatch(empty.body,/worksheet\.pdf|Deprecated/);
    assert.match(empty.body,/آخرین پیام‌های کلاس/);
    const good=f.entryRequest({query:file,range:'bytes=0-4'});
    assert.deepEqual(good,{status:206,body:'%PDF-'});
    for (const request of [{query:{...file,epoch:'9'.repeat(24)}},{query:{...file,doc:'c'.repeat(32)}},
        {query:{...file,photo:'student-1'}},{query:file,phase:'stale'},{query:file,actor:2},{query:file,actor:0},
        {query:{...file,light:'0'}},{query:file,method:'POST'}]) {
        const r=f.entryRequest(request);
        assert.ok(r.status>=400, JSON.stringify(request)); assert.equal(r.body,'');
    }
});

test('old or media-blocked Chromium can open the lesson with no JS and no automatic requests', {skip,timeout:35_000}, async t=>{
    const fixture=packaged(t);
    const full=fixture.entryRequest().body;
    const light=fixture.entryRequest({query:lite}).body;
    const requests=[];
    let waiting=true;
    const {browser,origin}=await serveBrowser(t,async(req,res,url)=>{
        requests.push(url.searchParams.get('name') || url.pathname);
        if (url.searchParams.get('view')==='lite') return send(res,'text/html; charset=UTF-8',light,{'Content-Security-Policy':"default-src 'none'; style-src 'self'"});
        if (url.searchParams.get('route')==='api') {
            let raw=''; for await (const chunk of req) raw+=chunk;
            const {action}=JSON.parse(raw);
            if (action==='ticket') return send(res,'application/json',JSON.stringify({ok:true,lesson:'qa',epoch:'A',token:'disposable',url:origin.replace('http','ws')}));
            assert.equal(action,'status');
            return send(res,'application/json',JSON.stringify(waiting ? {ok:true,ready:true,phase:'idle'}
                : {ok:true,ready:true,phase:'live',joinable:true,lesson:'qa',epoch:'A'}));
        }
        if (fixture.serve(url,res)) return;
        if (url.pathname==='/rtc' || url.pathname.includes('/validate')) { res.writeHead(503).end(); return; }
        return send(res,'text/html; charset=UTF-8',full);
    });
    const old=await browser.newPage({viewport:{width:360,height:700},reducedMotion:'reduce'});
    await old.addInitScript(()=>Object.defineProperty(window,'RTCPeerConnection',{value:undefined}));
    await old.goto(origin);
    await old.getByText(/BROWSER-RTC/).waitFor();
    assert.equal(await old.locator('#waiting').getAttribute('data-wait'),'no');
    assert.ok(!requests.some(r=>r.includes('livekit-client')));
    await old.getByRole('link',{name:'نمای سبک درس (بدون صدا و ویدیو)'}).click();
    await old.getByText('<b>پیام تازهٔ آزمون</b>',{exact:true}).waitFor();
    await old.close();

    const blockedScript=await browser.newPage();
    await blockedScript.route(url=>url.searchParams.get('name')==='assets/classroom.js',route=>route.abort('failed'));
    await blockedScript.goto(origin);
    await blockedScript.getByText(/PAGE-ASSETS/).waitFor();
    assert.equal(await blockedScript.locator('#waiting').getAttribute('data-wait'),'no');
    await blockedScript.close();

    const htmlOnly=await browser.newPage({javaScriptEnabled:false,viewport:{width:320,height:600}});
    await htmlOnly.goto(origin);
    await htmlOnly.getByText(/ورود صوتی به جاوااسکریپت/).waitFor();
    await htmlOnly.getByRole('link',{name:'نمای سبک درس (بدون صدا و ویدیو)'}).click();
    await htmlOnly.getByText('<b>پیام تازهٔ آزمون</b>',{exact:true}).waitFor();
    assert.equal(await htmlOnly.locator('script').count(),0);
    assert.equal(await htmlOnly.locator('.lite-board path').count(),1);
    const start=requests.length;
    await htmlOnly.getByRole('link',{name:'تازه‌کردن',exact:true}).click();
    await htmlOnly.getByText('دریافت فایل اصلی').waitFor();
    assert.ok(requests.slice(start).length<=2,'manual refresh fetches only the page and possibly its stylesheet');
    await htmlOnly.close();

    const page=await browser.newPage();
    await page.routeWebSocket(/\/rtc\b/, ws=>ws.close({code:1011,reason:'isolated signal unavailable'}));
    const cdp=await page.context().newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:280,downloadThroughput:500_000,uploadThroughput:500_000});
    await page.goto(origin);
    await page.getByText(/وقتی اتصال دبیر یا مسئول مدرسه تأیید شود/).waitFor();
    assert.equal(await page.evaluate(()=>getComputedStyle(document.querySelector('.waiting-icon'),'::after').animationName),'classroom-wait');
    await page.emulateMedia({reducedMotion:'reduce'});
    assert.equal(await page.evaluate(()=>getComputedStyle(document.querySelector('.waiting-icon'),'::after').animationName),'none');
    assert.ok(!requests.some(r=>r.includes('livekit-client')),'an idle pupil does not fetch the SDK');
    waiting=false;
    await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
    await page.getByText(/RTC-SIGNAL.*نمای سبک/).waitFor({timeout:20_000});
    await page.getByRole('link',{name:'نمای سبک درس (بدون صدا و ویدیو)'}).click();
    await page.getByText('<b>پیام تازهٔ آزمون</b>',{exact:true}).waitFor();
    t.diagnostic('BACI_ENTRY Chromium HTTP at 4 Mb/s and 280 ms latency, lost WebSocket, lazy SDK, reduced motion and HTML-only fallback passed');
});
