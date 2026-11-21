// Opt-in packaged Chromium test: set MPM_TEST_BACI (pinned, extracted site),
// MPM_TEST_CHROMIUM, MPM_TEST_PLAYWRIGHT (playwright-core module path), and optionally MPM_TEST_PHP.
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {createServer} = require('node:http');
const {mkdtempSync, readFileSync, rmSync} = require('node:fs');
const {join, extname} = require('node:path');
const {tmpdir} = require('node:os');
const test = require('node:test');
const {MPM_TEST_BACI:site, MPM_TEST_CHROMIUM:chromium, MPM_TEST_PLAYWRIGHT:playwright, MPM_TEST_PHP:php='php'} = process.env;

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

const docScript = bytes => `
const root = document.getElementById('classroom');
document.getElementById('live-panel').hidden = false;
const doc = {id:'sample',mime:'application/pdf',pages:2,name:'گچ.pdf',bytes:${bytes}};
const scope = {lesson:'test',epoch:'epoch',sid:'PA_fixture'};
let page = 1, rev = 1;
const sync = () => board.sync({rev,doc,page,docs:[doc],cursor:1,
    ops:[{tool:'pen',id:'fixture',size:9,color:page===1?'#dd0033':'#0044ee',points:[[3500,5500]]}]},scope);
const board = window.ClassroomBoard(root, {canEdit:()=>true,identity:()=> 'teacher-1',onExpand(){},
    notice(text){if (text) window.lastBoardNotice=text;},explain:e=>String(e),
    async send(action,data) {
        if (action !== 'board-page' || data.board !== rev || data.page < 1 || data.page > 2) throw Error('bad page');
        page=data.page; rev++; setTimeout(sync,0); return {boardRev:rev};
    }});
sync();`;

const mime = {'.css':'text/css','.js':'text/javascript','.mjs':'text/javascript','.woff2':'font/woff2','.pdf':'application/pdf'};
test('packaged PDF draws Persian fallback glyphs and keeps page rotation, navigation and ink aligned', {
    skip:!(site && chromium && playwright), timeout:35000,
}, async t => {
    const work = mkdtempSync(join(tmpdir(),'baci-pdf-browser-'));
    t.after(()=>rmSync(work,{recursive:true,force:true}));
    execFileSync('unzip',['-q',join(__dirname,'../artifacts/school-classroom-plugin-v0.4.14.zip'),'-d',work]);
    const html = execFileSync(php,[join(__dirname,'fixtures/classroom-browser-room.php')],{env:{...process.env,MPM_TEST_CLASSROOM_ROOT:work}});
    assert.match(html.toString(), /id="classroom"/);
    const pdf = persianPDF();
    let documentRequests = 0;
    const server = createServer((req,res) => {
        const url = new URL(req.url,'http://fixture');
        const route = url.searchParams.get('route'), name = url.searchParams.get('name') || '';
        const send = (type,bytes) => res.writeHead(200,{'Content-Type':type,'Content-Length':Buffer.byteLength(bytes)}).end(bytes);
        try {
            if (url.pathname === '/') {
                res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; font-src 'self' blob: data:; worker-src 'self'");
                return send('text/html; charset=UTF-8',html);
            }
            if (url.pathname === '/fixture.js') return send(mime['.js'],docScript(pdf.length));
            if (route === 'content') { documentRequests++; return send(mime['.pdf'],pdf); }
            if (route === 'asset' && /^assets\/[\w.-]+\.(css|js|mjs)$/.test(name)) return send(mime[extname(name)],readFileSync(join(work,name)));
            if (route === 'pdf' && /^(cmaps|standard_fonts|wasm|iccs)\/[A-Za-z0-9_.-]+$/.test(name)) {
                return send(mime[extname(name)] || 'application/octet-stream',execFileSync('unzip',['-p',join(work,'assets/pdf-resources.zip'),name]));
            }
            if (/^\/assets\/fonts\/screen\/Vazirmatn-(Regular|Bold)\.woff2$/.test(url.pathname)) {
                return send(mime['.woff2'],readFileSync(join(site,url.pathname)));
            }
        } catch (error) { res.writeHead(500).end(String(error)); return; }
        res.writeHead(404).end();
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    t.after(()=>{server.closeAllConnections(); server.close();});
    const browser = await require(playwright).chromium.launch({executablePath:chromium,headless:true,args:['--no-sandbox']});
    try {
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
        await page.goto(`http://127.0.0.1:${server.address().port}/`);
        assert.equal(await page.locator('#quality-open + #leave').count(),1);
        assert.equal(await page.locator('#quality-dialog select').count(),4);
        assert.deepEqual(await page.locator('#quality-dialog select').evaluateAll(nodes => nodes.map(node=>[...node.options].map(opt=>opt.value))),
            Array.from({length:4},()=>['low','medium','high']));
        await page.locator('#board-load-file').waitFor();
        assert.equal(documentRequests,0,'PDF is not downloaded before an explicit click');
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
        assert.equal(documentRequests,1,'render the current page using the same PDF, not a second file per page');
        assert.deepEqual(pageErrors,[]);
    } finally { await browser.close(); }
});
