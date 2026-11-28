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
const port=17880, tcp=17881, udp=17882, url=`http://127.0.0.1:${port}`;
const room = 'classroom-board-browser';
function signed(claims) {
    const encode = data => Buffer.from(JSON.stringify(data)).toString('base64url');
    const body = encode({alg:'HS256',typ:'JWT'})+'.'+encode({iss:key,exp:Math.floor(Date.now()/1000)+180,
        ...claims});
    return body+'.'+createHmac('sha256',secret).update(body).digest('base64url');
}
function ticket(identity) {
    return signed({sub:identity,video:{roomJoin:true,room,canSubscribe:true,canPublish:false,canPublishData:identity==='teacher-1'},
        exp:Math.floor(Date.now()/1000)+35});
}
async function dataGrant(identity, allowed, media = false) {
    const response=await fetch(url+'/twirp/livekit.RoomService/UpdateParticipant',{
        method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+signed({video:{roomAdmin:true,room}})},
        body:JSON.stringify({room,identity,permission:{canSubscribe:true,canPublish:media,canPublishData:allowed,
            ...(media?{canPublishSources:['MICROPHONE','CAMERA']}:{})}})
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
  tcp_port: ${tcp}
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
        try { if ((await fetch(url)).ok) return () => diag; } catch {}
        await delay(100);
    }
    assert.fail('isolated media server not ready');
}

function observeBoard() {
    const state = window.boardQA={room:null,widget:null,hints:0,dropped:0,drop:false,connectError:null,signal:false};
    const create = window.ClassroomBoard;
    window.ClassroomBoard = (root, options) => {
        state.widget=create(root,options);
        state.send=options.send;
        return state.widget;
    };
}

function observeRoom() {
    const lk = window.LivekitClient, state = window.boardQA;
    class ObservedRoom extends lk.Room {
        constructor(...args) {
            super(...args);
            state.room=this;
            this.on(lk.RoomEvent.SignalConnected,()=>{ state.signal=true; });
            const connect=this.connect.bind(this);
            this.connect=async (...args) => {
                try { return await connect(...args); }
                catch (error) { state.connectError=error.reasonName || error.name; throw error; }
            };
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

test('real peers open a cold bell, recover their SID, sync the board and can stop receiving only video',
    {skip:skip||!livekit, timeout:110_000}, async t => {
    const sfuDiag=await isolatedSfu(t);
    const fixture=packaged(t);
    const staffHTML=fixture.render('teacher',true), pupilHTML=fixture.render('student',true);
    const bellDate=staffHTML.toString().match(/data-bell-date="([^"]+)"/)[1];
    const members = {}, ops = [], log = [];
    const lesson='qa-lesson', epoch='qa-epoch', owner='teacher-1', pupil='student-1';
    let head=0, seq=0, unlock, replay=false, startedAt, admittedAt;
    let lastStage='first admission';
    const {browser,origin}=await serveBrowser(t,async (req,res,pageUrl)=>{
        if (pageUrl.pathname==='/teacher') return send(res,'text/html; charset=UTF-8',staffHTML);
        if (pageUrl.pathname==='/student') return send(res,'text/html; charset=UTF-8',pupilHTML);
        if (pageUrl.pathname==='/fixture.js') return send(res,'text/javascript',`(${observeBoard})();`);
        if (pageUrl.searchParams.get('name') === 'assets/livekit-client.umd.js') {
            return send(res,'text/javascript',Buffer.concat([fixture.asset('livekit-client.umd.js'),Buffer.from(`\n;(${observeRoom})();`)]));
        }
        if (pageUrl.searchParams.get('route')==='api') {
            const staff=new URL(req.headers.referer).pathname==='/teacher', identity=staff?owner:pupil;
            let raw=''; for await (const chunk of req) raw+=chunk;
            const input=JSON.parse(raw);
            assert.equal(input.class,22);
            assert.equal(req.headers['x-classroom-csrf'],'fixture');
            log.push({action:input.action,staff,sid:input.sid,ink:input.ink,at:performance.now()});
            const result={ok:true,phase:'live',lesson,epoch,ready:true,joinable:true,slot:11,mediaMode:'standard',cameraCodec:'vp8'};
            if (input.action==='open') {
                assert.equal(input.slot,11);
                assert.ok(staff && !startedAt,'the cold bell is opened exactly once, by staff');
                startedAt=performance.now();
                Object.assign(result,{phase:'starting',url:url.replace('http','ws'),token:ticket(identity)});
            } else if (!startedAt) {
                assert.equal(input.action,'status');
                return send(res,'application/json',JSON.stringify({ok:true,phase:'idle',ready:true,
                    schedule:{date:bellDate,choices:[{id:11,label:'زنگ ۱'}]}}));
            } else if (input.action==='ticket') Object.assign(result,{url:url.replace('http','ws'),token:ticket(identity)});
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
                assert.equal(input.lesson,lesson); assert.equal(input.epoch,epoch);
                if (!admittedAt) {
                    assert.ok(staff && performance.now()-startedAt<32_000, 'staff connects within the school startup window');
                    admittedAt=performance.now();
                }
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
    }, ['--use-fake-device-for-media-stream','--autoplay-policy=no-user-gesture-required']);
    async function open(path, beforeGoto) {
        const page=await browser.newPage({permissions:path==='/teacher'?['microphone','camera']:[]});
        page.on('pageerror',error=>t.diagnostic(error.message));
        // Model two different active devices rather than two tabs with one foreground.
        await page.addInitScript(()=>Object.defineProperty(document,'hidden',{get:()=>false}));
        await beforeGoto?.(page);
        await page.goto(origin+path,{waitUntil:'domcontentloaded'});
        try {
            await page.waitForFunction(()=>window.boardQA?.widget?.position().board===1,undefined,{timeout:22_000});
        } catch (error) {
            const state=await page.evaluate(()=>({
                sdk:!!window.LivekitClient,
                room:window.boardQA?.room?.state,
                board:window.boardQA?.widget?.position(),
                signal:window.boardQA?.signal,connectError:window.boardQA?.connectError,
                waiting:document.getElementById('wait-message')?.textContent,
            }));
            throw new Error(`${lastStage} ${path}: ${JSON.stringify({state,actions:log.filter(r=>r.staff===(path==='/teacher')).slice(-8).map(r=>[r.action,!!r.sid,r.ink]),
                sfu:sfuDiag().replace(/eyJ[\w.-]+/g,'[test token]').slice(-550)})}`,{cause:error});
        }
        return page;
    }
    const isMediaSdk=url=>url.searchParams.get('name')==='assets/livekit-client.umd.js';
    let releaseSdk, sdkRequested;
    const sdkHold=new Promise(resolve=>{releaseSdk=resolve;});
    const sdkRequest=new Promise(resolve=>{sdkRequested=resolve;});
    t.after(releaseSdk);
    const coldTeacher=open('/teacher',page=>page.route(isMediaSdk,async route=>{
        sdkRequested(page); await sdkHold; return route.continue();
    }));
    // Assert while the actual SDK response is held, not from a comment or timer that may miss a race.
    await Promise.race([sdkRequest, coldTeacher.then(()=>assert.fail('missing cold SDK request')), delay(8000).then(()=>assert.fail('SDK not requested'))]);
    try {
        assert.equal(startedAt,undefined,'no server-side startup window or ticket starts before the SDK loads');
        const page=await sdkRequest;
        await page.getByText('در حال بارگیری بخش صدا و ویدیو…',{exact:true}).waitFor({timeout:2000});
        assert.equal(await page.locator('#waiting').getAttribute('data-wait'),'yes');
    } finally { releaseSdk(); }
    const teacher=await coldTeacher, student=await open('/student');
    assert.ok(admittedAt-startedAt>=0 && admittedAt-startedAt<32_000);
    await student.waitForFunction(()=>window.boardQA.hints>=1,{},{timeout:8_000});
    assert.equal(await student.evaluate(()=>window.boardQA.room.localParticipant.permissions.canPublishData),false);
    assert.equal(await teacher.evaluate(()=>window.boardQA.room.localParticipant.permissions.canPublishData),true);
    const sidBeforeLoss=await student.evaluate(()=>window.boardQA.room.localParticipant.sid);
    const callsBeforeLoss=log.length;
    let lostStatusAt=0, releaseStatus;
    const heldStatus=new Promise(resolve=>{releaseStatus=resolve;});
    t.after(releaseStatus);
    const isSchoolApi=url=>url.searchParams.get('route')==='api';
    await student.route(isSchoolApi,async route=>{
        const input=route.request().postDataJSON();
        if (input.action==='status' && input.sid) {
            if (!lostStatusAt) { lostStatusAt=performance.now(); return route.abort('connectionreset'); }
            await heldStatus; // A slow next response isn't allowed to hide the recovery UI before the assertion.
        }
        return route.continue();
    });
    await student.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
    try { await student.getByText(/SITE-STATUS/).waitFor({timeout:8000}); }
    catch (error) {
        throw new Error(`school-response fault=${!!lostStatusAt} wait=${await student.locator('#wait-message').textContent()}`,{cause:error});
    }
    assert.equal(await student.locator('#live-panel').isVisible(),false);
    releaseStatus();
    await student.locator('#live-panel').waitFor({state:'visible',timeout:6500});
    await student.unroute(isSchoolApi);
    const schoolRecovery=performance.now()-lostStatusAt;
    assert.equal(await student.evaluate(()=>window.boardQA.room.localParticipant.sid),sidBeforeLoss);
    assert.ok(!log.slice(callsBeforeLoss).some(r=>!r.staff && ['ticket','leave'].includes(r.action)), 'one dropped HTTP response does not churn a real media SID');
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
    lastStage='paged pupil rejoin';
    const rejoined=await open('/student');
    const newSID=await rejoined.evaluate(()=>window.boardQA.room.localParticipant.sid);
    assert.notEqual(oldSID,newSID);
    await rejoined.waitForFunction(()=>window.boardQA.widget.position().ink===8,undefined,{timeout:3000});
    for (const cursor of [0,2,4,6]) assert.ok(log.some(r=>!r.staff && r.sid===newSID && r.ink===cursor),`new SID consumes page after cursor ${cursor}`);
    t.diagnostic(`BACI_BOARD_RTC Chromium teacher+pupil; same SID after dropped school response ${schoolRecovery.toFixed(1)} ms; ink after DB-ack fixture ${normal.toFixed(1)} ms; dropped recovery ${lostMs.toFixed(1)} ms; real permission revoke/restore, new SID, and paged checkpoints passed`);
    await dataGrant(owner,true,true);
    const sourceGrant = () => window.boardQA.room.localParticipant.permissions?.canPublishSources?.includes(2);
    await teacher.waitForFunction(sourceGrant,undefined,{timeout:5000});
    await rejoined.getByRole('checkbox',{name:/دریافت فقط صدا/}).uncheck();
    await teacher.evaluate(async () => {
        // Chromium supplies synthetic tracks; the real SDK and SFU carry their RTP.
        const raw=await navigator.mediaDevices.getUserMedia({audio:true,video:{width:320,height:240,frameRate:10}});
        const lk=window.LivekitClient, local=window.boardQA.room.localParticipant;
        await local.publishTrack(raw.getAudioTracks()[0],{source:lk.Track.Source.Microphone,dtx:false});
        await local.publishTrack(raw.getVideoTracks()[0],{source:lk.Track.Source.Camera,videoCodec:'vp8',simulcast:false});
    });
    const decoded = async ({owner,minAudio,minFrames,videoOff})=>{
        const peer=window.boardQA.room.remoteParticipants.get(owner);
        const mic=peer?.getTrackPublication('microphone'), camera=peer?.getTrackPublication('camera');
        const metric=async (pub,kind,key)=>{
            const report=await pub?.track?.getRTCStatsReport();
            return report ? [...report.values()].find(row=>row.type==='inbound-rtp' && row.kind===kind)?.[key] ?? 0 : 0;
        };
        const packets=await metric(mic,'audio','packetsReceived');
        if (packets<=minAudio) return false;
        if (videoOff ? camera?.isDesired!==false || camera.isSubscribed : await metric(camera,'video','framesDecoded')<minFrames) return false;
        return packets;
    };
    const received=await (await rejoined.waitForFunction(decoded,{owner,minAudio:10,minFrames:3,videoOff:false},{timeout:10_000})).jsonValue();
    const priorTickets=log.filter(r=>r.action==='ticket').length;
    await rejoined.getByRole('checkbox',{name:/دریافت فقط صدا/}).check();
    await rejoined.waitForFunction(decoded,{owner,minAudio:received+10,minFrames:0,videoOff:true},{timeout:6000});
    assert.equal(await rejoined.evaluate(()=>window.boardQA.room.localParticipant.sid),newSID);
    assert.equal(log.filter(r=>r.action==='ticket').length,priorTickets);
    assert.equal(await rejoined.locator('#live-panel').isVisible(),true);
    t.diagnostic(`BACI_ENTRY cold staff: SDK first, admission ${(admittedAt-startedAt).toFixed(1)} ms after open in the fixture; real camera frames before audio-only, ongoing audio RTP without video or a new SID after toggle`);
});
