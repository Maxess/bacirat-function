// Opt-in packaged Chromium test: set MPM_TEST_BACI (pinned, extracted site),
// MPM_TEST_CHROMIUM, MPM_TEST_PLAYWRIGHT (playwright-core module path), and optionally MPM_TEST_PHP.
const assert = require('node:assert/strict');
const {deflateSync} = require('node:zlib');
const test = require('node:test');
const {packaged, serveBrowser, send, skip} = require('./fixtures/classroom-browser.cjs');

function persianPDF() {
    // A deliberately unembedded CID font, with Persian presentation forms and real positions.
    const stream = data => `<< /Length ${Buffer.byteLength(data)} >>\nstream\n${data}\nendstream`;
    const text = n => `0 g BT /F 40 Tf 90 80 Td <${n}> Tj ET\nBT /L 12 Tf 26 30 Td (Page fixture) Tj ET\n`;
    const map = `/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /PersianFixture def\n/CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange
4 beginbfchar\n<0001> <FB7B>\n<0002> <FB94>\n<0003> <FB8B>\n<0004> <FB58>\nendbfchar
endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n`;
    const page = (box, contents, rotation=0) => `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${box}] /Rotate ${rotation} /Contents ${contents} 0 R /Resources << /Font << /F 7 0 R /L 11 0 R >> >> >>`;
    const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
        page('300 180', 5), page('280 160', 6, 90), stream(text('00010002')), stream(text('00030004')),
        '<< /Type /Font /Subtype /Type0 /BaseFont /PersianFixtureMissing /Encoding /Identity-H /DescendantFonts [8 0 R] /ToUnicode 10 0 R >>',
        '<< /Type /Font /Subtype /CIDFontType2 /BaseFont /PersianFixtureMissing /FontDescriptor 9 0 R /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 600 >>',
        '<< /Type /FontDescriptor /FontName /PersianFixtureMissing /Flags 4 /FontBBox [0 -200 1000 950] /ItalicAngle 0 /Ascent 900 /Descent -200 /CapHeight 800 /StemV 100 >>',
        stream(map), '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'];
    let file = '%PDF-1.5\n';
    const offsets = objects.map((data, i) => {
        const offset = file.length;
        file += `${i+1} 0 obj\n${data}\nendobj\n`;
        return `${String(offset).padStart(10,'0')} 00000 n \n`;
    });
    return Buffer.from(`${file}xref\n0 ${objects.length+1}\n0000000000 65535 f \n${offsets.join('')}trailer\n<< /Root 1 0 R /Size ${objects.length+1} >>\nstartxref\n${file.length}\n%%EOF\n`);
}

const docScript = (bytes,pages) => `
const root = document.getElementById('classroom');
document.getElementById('live-panel').hidden = false;
const doc = {id:'sample',mime:'application/pdf',pages:${pages},name:'گچ.pdf',bytes:${bytes}};
let scope = {lesson:'test',epoch:'epoch',sid:'PA_fixture'};
let page = 1, rev = 1;
const sync = () => board.sync({rev,doc,page,docs:[doc],cursor:1,
    ops:[{tool:'pen',id:'fixture',size:9,color:page===1?'#dd0033':'#0044ee',points:[[3500,5500]]}]},scope);
const board = window.ClassroomBoard(root, {canEdit:()=>true,identity:()=> 'teacher-1',onExpand(){},
    notice(text){if (text) window.lastBoardNotice=text;},explain:e=>String(e),
    async send(action,data) {
        if (action !== 'board-page' || data.board !== rev || data.page < 1 || data.page > doc.pages) throw Error('bad page');
        page=data.page; rev++; setTimeout(sync,0); return {boardRev:rev};
    }});
window.changePDFConnection = () => { scope = {...scope,sid:'PA_new_fixture'}; sync(); };
sync();`;

async function pdfFixture(t,pdf,pages) {
    const fixture = packaged(t), html = fixture.render();
    const requests = [], staticRequests = [];
    const delivery = {ignoreRange:false, pause:null};
    const {browser,origin} = await serveBrowser(t, (req,res,url) => {
        const route = url.searchParams.get('route');
        if (url.pathname === '/') {
            res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; font-src 'self' blob: data:; worker-src 'self'");
            return send(res,'text/html; charset=UTF-8',html,{'Cache-Control':'no-store'});
        }
        if (url.pathname === '/fixture.js') return send(res,'text/javascript',docScript(pdf.length,pages),{'Cache-Control':'no-store'});
        if (route === 'content') {
            const range = req.headers.range;
            const spans = !range || delivery.ignoreRange ? [] : range.slice(6).split(',').map(part=>part.split('-').map(Number));
            const entry = {status:spans.length ? 206 : 200, queued:0, range, sid:url.searchParams.get('sid'), closed:false};
            requests.push(entry);
            res.on('close',() => { entry.closed = true; });
            const headers = {'Content-Type':'application/pdf','Accept-Ranges':'bytes','Cache-Control':'no-store, private, no-transform'};
            const boundary='baci-fixture';
            let data=pdf;
            if (spans.length === 1) {
                const [a,b]=spans[0], end=Math.min(b,pdf.length-1);
                headers['Content-Range'] = `bytes ${a}-${end}/${pdf.length}`;
                data=pdf.subarray(a,end+1);
            } else if (spans.length > 1) {
                headers['Content-Type']=`multipart/byteranges; boundary=${boundary}`;
                data=Buffer.concat([...spans.flatMap(([a,b])=>[Buffer.from(`--${boundary}\r\nContent-Type: application/pdf\r\nContent-Range: bytes ${a}-${b}/${pdf.length}\r\n\r\n`),
                    pdf.subarray(a,b+1),Buffer.from('\r\n')]),Buffer.from(`--${boundary}--\r\n`)]);
            }
            res.writeHead(entry.status,{...headers,'Content-Length':data.length});
            let offset=0;
            const pump = () => {
                if (res.destroyed) return;
                while (offset < data.length) {
                    const next = Math.min(data.length,offset+65536);
                    const ready = res.write(data.subarray(offset,next));
                    entry.queued += next-offset; offset=next;
                    if (!ready) { res.once('drain',pump); return; }
                }
                res.end();
            };
            if (delivery.pause) delivery.pause(entry,pump); else pump();
            return;
        }
        if (route === 'asset' || route === 'pdf') staticRequests.push(url.search);
        if (!fixture.serve(url,res)) res.writeHead(404).end();
    });
    return {browser,origin,requests,staticRequests,delivery};
}

test('packaged PDF draws Persian fallback glyphs and keeps page rotation, navigation and ink aligned', {skip, timeout:35000}, async t => {
    const {browser,origin,requests} = await pdfFixture(t,persianPDF(),2);
    const page = await browser.newPage();
    page.setDefaultTimeout(6500);
    const pageErrors = [];
    page.on('pageerror',e=>pageErrors.push(e.message));
    await page.addInitScript(() => {
        window.paintedGlyphs = [];
        const fill = CanvasRenderingContext2D.prototype.fillText;
        CanvasRenderingContext2D.prototype.fillText = function(char,...args) {
            if (this.canvas.id === 'board-paper' && /[\uFB50-\uFDFF]/u.test(char)) window.paintedGlyphs.push([char,this.font]);
            return fill.call(this,char,...args);
        };
    });
    await page.goto(origin);
    assert.equal(await page.locator('#quality-open + #leave').count(),1);
    assert.equal(await page.locator('#quality-dialog select').count(),4);
    assert.deepEqual(await page.locator('#quality-dialog select').evaluateAll(nodes => nodes.map(node=>[...node.options].map(opt=>opt.value))),
        Array.from({length:4},()=>['default','low','medium','high']));
    await page.locator('#board-load-file').waitFor();
    assert.equal(requests.length,0,'PDF is not downloaded before an explicit click');
    await page.locator('#board-load-file').click();
    const waitPaint = n => page.waitForFunction(n=>window.paintedGlyphs.length >= n,n,{timeout:6000});
    await waitPaint(2);
    const inspect = () => page.evaluate(() => {
        const paper=document.getElementById('board-paper'), ink=document.getElementById('board-ink');
        const px = ink.getContext('2d').getImageData(Math.floor(ink.width*.35),Math.floor(ink.height*.55),1,1).data;
        const raster = paper.getContext('2d').getImageData(0,0,paper.width,paper.height).data;
        let dark=0;
        for (let i=0;i<raster.length;i+=4) if (raster[i]<100) dark++;
        return {glyphs:window.paintedGlyphs,width:paper.width,height:paper.height,ink:[...px],dark,
            aligned:paper.width===ink.width && paper.height===ink.height, label:document.getElementById('board-page-label').textContent,
            loaded:[...document.fonts].some(f=>f.family==='ClassroomPDFPersian' && f.status==='loaded')};
    });
    const first = await inspect();
    assert.equal(first.loaded,true);
    assert.deepEqual(first.glyphs.map(g=>g[0]),['\ufb7b','\ufb94']);
    assert.ok(first.glyphs.every(g=>g[1].includes('ClassroomPDFPersian')));
    assert.ok(first.aligned && first.width>first.height && first.dark>500);
    assert.deepEqual(first.ink,[221,0,51,255]); assert.equal(first.label,'۱');
    await page.locator('#page-next').click(); await waitPaint(4);
    const second = await inspect();
    assert.ok(second.aligned && second.height>second.width && second.dark>500);
    assert.deepEqual(second.ink,[0,68,238,255]); assert.equal(second.label,'۲');
    await page.locator('#page-prev').click(); await waitPaint(6);
    const again=await inspect();
    assert.equal(again.label,'۱'); assert.equal(again.dark,first.dark); assert.deepEqual(again.ink,first.ink);
    assert.equal(requests.length,1,'render the current page using the same PDF, not a second file per page');
    assert.deepEqual(pageErrors,[]);
});

function scanPDF(pages) {
    // Synthetic scan-like pages: independent deflated RGB images, no real pupil or lesson data.
    let seed=0x12345678, size=0;
    const chunks=[], xref=[];
    const push = value => { const chunk=Buffer.from(value); size+=chunk.length; chunks.push(chunk); };
    const object = (id,value,binary) => {
        xref[id]=size; push(`${id} 0 obj\n${value}`);
        if (binary) { push('\nstream\n'); push(binary); push('\nendstream'); }
        push('\nendobj\n');
    };
    push('%PDF-1.5\n');
    object(1,'<< /Type /Catalog /Pages 2 0 R >>');
    object(2,`<< /Type /Pages /Kids [${Array.from({length:pages},(_,i)=>`${3+3*i} 0 R`).join(' ')}] /Count ${pages} >>`);
    for (let i=0;i<pages;i++) {
        const id=3+3*i, rgb=Buffer.alloc(420*224*3);
        for (let j=0;j<rgb.length;j++) {
            seed^=seed<<13; seed^=seed>>>17; seed^=seed<<5;
            rgb[j]=seed&255;
        }
        const scan=deflateSync(rgb);
        const color = i === 0 ? '1 0 1' : '0 1 1';
        const paint=Buffer.from(`q 320 0 0 224 0 0 cm /I Do Q ${color} rg 130 88 60 50 re f`);
        object(id,`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 224] /Contents ${id+2} 0 R /Resources << /XObject << /I ${id+1} 0 R >> >> >>`);
        object(id+1,`<< /Type /XObject /Subtype /Image /Width 420 /Height 224 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length ${scan.length} >>`,scan);
        object(id+2,`<< /Length ${paint.length} >>`,paint);
    }
    const table=size;
    push(`xref\n0 ${xref.length}\n0000000000 65535 f \n`);
    for (const addr of xref.slice(1)) push(`${String(addr).padStart(10,'0')} 00000 n \n`);
    push(`trailer\n<< /Root 1 0 R /Size ${xref.length} >>\nstartxref\n${table}\n%%EOF\n`);
    return Buffer.concat(chunks);
}

async function waitColor(page, rgb) {
    await page.waitForFunction(rgb => {
        const canvas=document.getElementById('board-paper');
        const actual=canvas.getContext('2d').getImageData(canvas.width>>1,canvas.height>>1,1,1).data;
        return rgb.every((value,i)=>actual[i]===value);
    },rgb,{timeout:6500});
}
const payload = entries => ({requests:entries.length, bytes:entries.reduce((sum,r)=>sum+r.queued,0)});

test('large PDF reads ranges only, and hiding or revisiting pages does not transfer them again', {skip, timeout:40000}, async t => {
    const pages=32, pdf=scanPDF(pages);
    const {browser,origin,requests,staticRequests,delivery} = await pdfFixture(t,pdf,pages);
    const page = await browser.newPage(), file=page.locator('#board-load-file');
    page.setDefaultTimeout(6500);
    await page.goto(origin);
    await file.waitFor();
    assert.equal(requests.length,0);
    assert.equal(staticRequests.filter(s=>s.includes('route=pdf')).length,0,'the runtime is lazy too');
    await file.click(); await waitColor(page,[255,0,255]);
    const first=payload(requests), firstRequests=requests.slice();
    await page.locator('#page-next').click(); await waitColor(page,[0,255,255]);
    const second=payload(requests.slice(first.requests));
    await page.locator('#page-prev').click(); await waitColor(page,[255,0,255]);
    const revisit=payload(requests.slice(first.requests+second.requests));
    const beforeHide=requests.length;
    await file.click(); await waitColor(page,[255,255,255]);
    await file.click(); await waitColor(page,[255,0,255]);
    const reopen=payload(requests.slice(beforeHide));
    t.diagnostic(`PDF ${pdf.length} B; first=${JSON.stringify(first)}, first 206 bodies=${payload(firstRequests.filter(r=>r.status===206)).bytes} B; `
        + `next=${JSON.stringify(second)}, prev=${JSON.stringify(revisit)}, hide/show=${JSON.stringify(reopen)}`);
    assert.equal(firstRequests[0].range,'bytes=0-16383','no unbounded GET to discover range support');
    assert.ok(requests.every(r=>r.status===206 && r.range && r.sid==='PA_fixture'));
    assert.ok(first.bytes < pdf.length/8 && first.requests < 12);
    assert.ok(second.bytes > 0 && second.bytes < pdf.length/8 && second.requests < 12);
    assert.deepEqual(revisit,{requests:0,bytes:0});
    assert.deepEqual(reopen,{requests:0,bytes:0});

    // A fresh connection remains opt-in and cannot reuse private lesson bytes from the old SID.
    const oldStatic=staticRequests.filter(s=>s.includes('route=pdf')).length;
    const fresh=requests.length;
    await page.evaluate(() => window.changePDFConnection());
    await waitColor(page,[255,255,255]);
    assert.equal(requests.length,fresh);
    // Hide during the first real range request; it must abort without leaving a reader in flight.
    const paused = new Promise(resolve => { delivery.pause = r => resolve(r); });
    await file.click();
    const inflight = await paused;
    assert.equal(inflight.sid,'PA_new_fixture');
    await file.click();
    for (let i=0;i<60 && !inflight.closed;i++) await new Promise(resolve=>setTimeout(resolve,15));
    assert.ok(inflight.closed && !inflight.queued, 'hiding the loading PDF cancels the HTTP request');
    delivery.pause=null;
    await file.click(); await waitColor(page,[255,0,255]);
    assert.equal(staticRequests.filter(s=>s.includes('route=pdf')).length,oldStatic,'recreating one worker uses cacheable non-personal assets');
    const beforeNav=requests.length;
    await page.goto(`${origin}/?return=1`);
    await file.waitFor();
    assert.equal(requests.length,beforeNav);
    delivery.ignoreRange=true;
    await file.click(); await waitColor(page,[255,0,255]);
    assert.deepEqual(payload(requests.slice(beforeNav)),{requests:1,bytes:pdf.length},'range-ignoring proxy serves one full fallback, not two transfers');
    assert.equal(staticRequests.filter(s=>s.includes('route=pdf')).length,oldStatic,'fresh navigation does not revalidate fingerprinted PDF assets');
});
