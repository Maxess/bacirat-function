// Shared standalone browser fixture helpers. No production site or media service is contacted.
const {execFileSync} = require('node:child_process');
const {createServer} = require('node:http');
const {mkdtempSync, readFileSync, rmSync} = require('node:fs');
const {join, extname} = require('node:path');
const {tmpdir} = require('node:os');
const {MPM_TEST_BACI:site, MPM_TEST_CHROMIUM:chromium, MPM_TEST_PLAYWRIGHT:playwright, MPM_TEST_PHP:php='php',
    MPM_TEST_CLASSROOM_ZIP:archive=join(__dirname,'../../artifacts/school-classroom-plugin-v0.4.25.zip')} = process.env;
const mime = {'.css':'text/css','.js':'text/javascript','.mjs':'text/javascript','.woff2':'font/woff2','.pdf':'application/pdf',
    '.wasm':'application/wasm','.ttf':'font/ttf'};
function send(res, type, bytes, headers={}) {
    res.writeHead(200,{'Content-Type':type,'Content-Length':Buffer.byteLength(bytes),...headers}).end(bytes);
}
function packaged(t) {
    const work = mkdtempSync(join(tmpdir(),'baci-classroom-browser-'));
    t.after(()=>rmSync(work,{recursive:true,force:true}));
    execFileSync('unzip',['-q',archive,'-d',work]);
    const render = (role='teacher', live=false) => execFileSync(php,[join(__dirname,'classroom-browser-room.php')],{env:{...process.env,
        MPM_TEST_CLASSROOM_ROOT:work, MPM_TEST_ROOM_ROLE:role, MPM_TEST_ROOM_LIVE:live?'yes':'no'}});
    const serve = (url,res) => {
        const route = url.searchParams.get('route'), name = url.searchParams.get('name') || '';
        const headers = {'Cache-Control':url.searchParams.has('v') ? 'private, max-age=31536000, immutable' : 'private, no-cache'};
        if (route === 'asset' && /^assets\/[\w.-]+\.(css|js|mjs)$/.test(name)) {
            send(res,mime[extname(name)],readFileSync(join(work,name)),headers); return true;
        }
        if (route === 'pdf' && /^(cmaps|standard_fonts|wasm|iccs|runtime)\/[A-Za-z0-9_.-]+$/.test(name)) {
            send(res,mime[extname(name)] || 'application/octet-stream',execFileSync('unzip',['-p',join(work,'assets/pdf-resources.zip'),name],{maxBuffer:4*1024*1024}),headers);
            return true;
        }
        if (/^\/assets\/fonts\/screen\/Vazirmatn-(Regular|Bold)\.woff2$/.test(url.pathname)) {
            send(res,mime['.woff2'],readFileSync(join(site,url.pathname)),{'Cache-Control':'private, max-age=86400'}); return true;
        }
        return false;
    };
    return {render,serve};
}
async function serveBrowser(t, handler) {
    const server = createServer((req,res) => {
        Promise.resolve().then(() => handler(req,res,new URL(req.url,'http://fixture'))).catch(error=>{
            if (!res.headersSent) res.writeHead(500).end(String(error)); else res.destroy(error);
        });
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    t.after(()=>{server.closeAllConnections(); server.close();});
    const browser = await require(playwright).chromium.launch({executablePath:chromium,headless:true,args:['--no-sandbox']});
    t.after(()=>browser.close());
    return {browser,origin:`http://127.0.0.1:${server.address().port}`};
}
module.exports = {skip:!(site && chromium && playwright), packaged, serveBrowser, send};
