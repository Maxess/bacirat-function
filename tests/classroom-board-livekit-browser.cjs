// Two real Chromium pages, the packaged room/SDK, and an isolated LiveKit SFU.
// School admission/ink is deterministic HTTP here; MariaDB is tested separately.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {createHmac} = require('node:crypto');
const {spawn} = require('node:child_process');
const {mkdtempSync, rmSync, writeFileSync} = require('node:fs');
const {tmpdir} = require('node:os');
const {join} = require('node:path');
const {once} = require('node:events');
const {setTimeout:delay} = require('node:timers/promises');
const {packaged, serveBrowser, send, skip} = require('./fixtures/classroom-browser.cjs');
const livekit = process.env.MPM_TEST_LIVEKIT;
const key = 'qa_board_only_key', secret = 'disposable-board-browser-secret-never-in-a-deployment';
const port=17880, udp=17882, url=`http://127.0.0.1:${port}`;
const room = 'classroom-board-browser';
function signed(claims) {
    const encode = data => Buffer.from(JSON.stringify(data)).toString('base64url');
    const body = encode({alg:'HS256',typ:'JWT'})+'.'+encode({iss:key,exp:Math.floor(Date.now()/1000)+180,
        ...claims});
    return body+'.'+createHmac('sha256',secret).update(body).digest('base64url');
}
function ticket(identity) {
    return signed({sub:identity,video:{roomJoin:true,room,canSubscribe:true,canPublish:false,canPublishData:identity==='teacher-1'}});
}
async function dataGrant(identity, allowed) {
    const response=await fetch(url+'/twirp/livekit.RoomService/UpdateParticipant',{
        method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+signed({video:{roomAdmin:true,room}})},
        body:JSON.stringify({room,identity,permission:{canSubscribe:true,canPublish:false,canPublishData:allowed}})
    });
    assert.equal(response.status,200,await response.text());
}
async function isolatedSfu(t) {
    const dir=mkdtempSync(join(tmpdir(),'baci-board-sfu-'));
    const config=join(dir,'livekit.yaml');
    writeFileSync(config,`port: ${port}
bind_addresses: [127.0.0.1]
rtc:
  node_ip: 127.0.0.1
  use_external_ip: false
  enable_loopback_candidate: true
  tcp_port: 0
  udp_port: ${udp}
  batch_io:
    batch_size: 64
    max_flush_interval: 1ms
room:
  auto_create: true
keys:
  ${key}: ${secret}
logging:
  level: warn
`);
    const proc=spawn(livekit,['--config',config],{stdio:['ignore','ignore','pipe']});
    let diag=''; proc.stderr.on('data',b=>{ diag=(diag+b).slice(-3000); });
    let failure; proc.on('error',e=>{ failure=e; });
    t.after(async()=>{
        if (proc.pid && proc.exitCode===null && proc.signalCode===null) {
            proc.kill('SIGTERM');
            await Promise.race([once(proc,'exit'),delay(2500)]);
            if (proc.exitCode===null && proc.signalCode===null) proc.kill('SIGKILL');
        }
        rmSync(dir,{recursive:true,force:true});
    });
    for (let i=0;i<50;i++) {
        if (proc.exitCode!==null || failure) throw Error('isolated media server did not start: '+(failure?.message ?? diag));
        try { if ((await fetch(url)).ok) return; } catch {}
        await delay(100);
    }
    assert.fail('isolated media server not ready');
}

function observeRoom() {
    const lk = window.LivekitClient;
    const state = window.boardQA={room:null,widget:null,hints:0,dropped:0,drop:false};
    const create = window.ClassroomBoard;
    window.ClassroomBoard = (root, options) => {
        state.widget=create(root,options);
        state.send=options.send;
        return state.widget;
    };
    class ObservedRoom extends lk.Room {
        constructor(...args) {
            super(...args);
            state.room=this;
        }
        on(event,callback) {
            if (event!==lk.RoomEvent.DataReceived) return super.on(event,callback);
            return super.on(event,(payload,person,kind,topic) => {
                if (topic==='baci.board.head.v1') {
                    if (state.drop) { state.dropped++; return; }
                    state.hints++;
                }
                callback(payload,person,kind,topic);
            });
        }
    }
    window.LivekitClient={...lk,Room:ObservedRoom};
}

test('real lossy board notices are commit-only, recover from loss and respect SID and data grants',
    {skip:skip||!livekit, timeout:56_000}, async t => {
    await isolatedSfu(t);
    const fixture=packaged(t);
    const sdk = '/plugins/school-plugins.php?plugin=school-classroom&amp;route=asset&amp;name=assets/livekit-client.umd.js';
    const render = role => fixture.render(role,true).toString().replace('<script defer src="/fixture.js">',
        `<script defer src="${sdk}"></script><script defer src="/fixture.js">`);
    const staffHTML=render('teacher'), pupilHTML=render('student');
    const members = {}, ops = [], log = [];
    const lesson='qa-lesson', epoch='qa-epoch', owner='teacher-1', pupil='student-1';
    let head=0, seq=0, unlock, replay=false;
    const {browser,origin}=await serveBrowser(t,async (req,res,pageUrl)=>{
        if (pageUrl.pathname==='/teacher') return send(res,'text/html; charset=UTF-8',staffHTML);
        if (pageUrl.pathname==='/student') return send(res,'text/html; charset=UTF-8',pupilHTML);
        if (pageUrl.pathname==='/fixture.js') {
            // Load the real SDK first (the test fixture normally supplies a stub).
            return send(res,'text/javascript',`/* Loaded after the pinned SDK. */\n(${observeRoom})();`);
        }
        if (pageUrl.searchParams.get('route')==='api') {
            const staff=new URL(req.headers.referer).pathname==='/teacher', identity=staff?owner:pupil;
            let raw=''; for await (const chunk of req) raw+=chunk;
            const input=JSON.parse(raw);
            assert.equal(input.class,22);
            assert.equal(req.headers['x-classroom-csrf'],'fixture');
            log.push({action:input.action,staff,sid:input.sid,ink:input.ink,at:performance.now()});
            const result={ok:true,phase:'live',lesson,epoch,ready:true,joinable:true,slot:11,mediaMode:'audio'};
            if (input.action==='ticket') Object.assign(result,{url:url.replace('http','ws'),token:ticket(identity)});
            else if (input.action==='leave') {
                if (members[identity]===input.sid) delete members[identity];
            } else if (input.action==='board-ink') {
                assert.ok(staff && input.sid===members[owner] && input.board===1 && input.seq===seq+1);
                if (unlock) await unlock;
                const [op]=input.strokes;
                assert.equal(op.tool,'pen');
                const from=head;
                head+=op.points.length; seq++;
                ops.push({...op,from,seq:head,author:owner});
                Object.assign(result,{ink:head,seq});
            } else if (input.sid) {
                members[identity]=input.sid;
                result.admitted=true;
                const reset=input.board!==1;
                const checkpoint=reset||input.checkpoint;
                const from=reset?0:input.ink;
                const deltas=ops.filter(op=>op.seq>from).slice(0,(!staff && replay)?1:Infinity);
                const cursor=deltas.at(-1)?.seq ?? from;
                result.board={rev:1,cursor,head,more:cursor<head,write:staff,owner:staff?'e'.repeat(16):null,
                    notice:input.boardNotice===true,
                    presenter:members[owner]?{identity:owner,sid:members[owner]}:null,
                    ops:deltas,
                    ...(checkpoint?{doc:null,page:1,docs:[],reset,checkpoint:true,writeSeq:seq}:{}),...(staff?{navigate:true}:{})};
            }
            return send(res,'application/json',JSON.stringify(result),{'Cache-Control':'no-store'});
        }
        if (!fixture.serve(pageUrl,res)) res.writeHead(404).end();
    });
    async function open(path) {
        const page=await browser.newPage();
        page.on('pageerror',error=>t.diagnostic(error.message));
        // Model two different active devices rather than two tabs with one foreground.
        await page.addInitScript(()=>Object.defineProperty(document,'hidden',{get:()=>false}));
        await page.goto(origin+path);
        await page.waitForFunction(()=>window.boardQA?.widget?.position().board===1,undefined,{timeout:12_000});
        return page;
    }
    const teacher=await open('/teacher'), student=await open('/student');
    await student.waitForFunction(()=>window.boardQA.hints>=1,{},{timeout:8_000});
    assert.equal(await student.evaluate(()=>window.boardQA.room.localParticipant.permissions.canPublishData),false);
    assert.equal(await teacher.evaluate(()=>window.boardQA.room.localParticipant.permissions.canPublishData),true);
    const initial = await student.evaluate(()=>window.boardQA.hints);
    const sendStroke = (page, seq) => page.evaluate(seq=>window.boardQA.send('board-ink',{board:1,seq,strokes:[{
        tool:'pen',id:'e'.repeat(16)+seq.toString(16).padStart(16,'0'),part:0,end:true,size:12,color:'#d01091',points:[[400,100],[800,200]]}]}),seq);
    let commit;
    t.after(()=>commit?.());
    unlock=new Promise(resolve=>{commit=resolve;});
    const writer=sendStroke(teacher,1);
    for (let i=0;i<35 && !log.some(r=>r.action==='board-ink'&&r.staff);i++) await delay(25);
    assert.ok(log.some(r=>r.action==='board-ink'&&r.staff),'write reached the fixture; its commit is still paused');
    assert.equal(await student.evaluate(()=>window.boardQA.widget.position().ink),0);
    const began=performance.now();
    commit(); await writer; unlock=null;
    await student.waitForFunction(()=>window.boardQA.widget.position().ink===2,{},{timeout:1600});
    const normal = performance.now()-began;
    assert.ok(normal<850,'healthy WebRTC invalidation should beat a legacy idle poll');
    assert.ok(await student.evaluate(n=>window.boardQA.hints>n, initial));
    await student.evaluate(()=>{window.boardQA.drop=true;});
    const lostAt=performance.now();
    await sendStroke(teacher,2);
    await student.waitForFunction(()=>window.boardQA.widget.position().ink===4,{},{timeout:3000});
    assert.ok(await student.evaluate(()=>window.boardQA.dropped>0),'must really drop a data-channel notice');
    assert.ok(log.some(r=>r.action==='board-sync'&&!r.staff),'authoritative HTTP path still renders the delta');
    const lostMs=performance.now()-lostAt;

    await student.evaluate(()=>{window.boardQA.drop=false;});
    await dataGrant(owner,false);
    await student.waitForFunction(owner=>window.boardQA.room.remoteParticipants.get(owner)?.permissions.canPublishData===false,owner,{timeout:5500});
    const beforeRevoked=await student.evaluate(()=>window.boardQA.hints);
    await sendStroke(teacher,3);
    await student.waitForFunction(()=>window.boardQA.widget.position().ink===6,undefined,{timeout:3000});
    assert.equal(await student.evaluate(()=>window.boardQA.hints),beforeRevoked,'revoked teacher stays on HTTPS/HTTP reads and writes');
    await dataGrant(owner,true);
    await student.waitForFunction(owner=>window.boardQA.room.remoteParticipants.get(owner)?.permissions.canPublishData===true,owner,{timeout:5500});

    const oldSID=await student.evaluate(()=>window.boardQA.room.localParticipant.sid);
    await student.close();
    await sendStroke(teacher,4);
    replay=true;
    const rejoined=await open('/student');
    const newSID=await rejoined.evaluate(()=>window.boardQA.room.localParticipant.sid);
    assert.notEqual(oldSID,newSID);
    await rejoined.waitForFunction(()=>window.boardQA.widget.position().ink===8,undefined,{timeout:3000});
    for (const cursor of [0,2,4,6]) assert.ok(log.some(r=>!r.staff && r.sid===newSID && r.ink===cursor),`new SID consumes page after cursor ${cursor}`);
    t.diagnostic(`BACI_BOARD_RTC Chromium teacher+pupil; ink after DB-ack fixture ${normal.toFixed(1)} ms; dropped recovery ${lostMs.toFixed(1)} ms; real permission revoke/restore, new SID, and paged checkpoints passed`);
});
