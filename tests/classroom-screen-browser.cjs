// Packaged room DOM and actual clicks in Chromium. School API and signaling are test-only;
// the display picker returns a synthetic canvas track, never a real screen.
const assert = require('node:assert/strict');
const test = require('node:test');
const {twoPageBoardPDF} = require('./fixtures/classroom-board-docs.cjs');
const {packaged, serveBrowser, send, skip} = require('./fixtures/classroom-browser.cjs');

function fakeMedia(identity, remote, unsupported) {
    const source = {Camera:'camera',Microphone:'microphone',ScreenShare:'screen_share'};
    window.screenFixture = {clicks:[],published:[],raw:null};
    const stats = window.screenFixture;
    const person = id => {
        const trackPublications = new Map();
        return {identity:id, sid:`PA_${id}`, name:id, permissions:{canPublish:true,canPublishSources:[1,2]},
            trackPublications, getTrackPublication(src) { return trackPublications.get(src); }};
    };
    class Room {
        constructor() {
            this.events = new Map();
            this.localParticipant = person(identity);
            this.remoteParticipants = new Map([[remote,person(remote)]]);
            this.canPlaybackAudio = true;
            const local = this.localParticipant;
            stats.remoteScreen = (trackSid, sid=`PA_${remote}`) => {
                const peer=this.remoteParticipants.get(remote), {ScreenShare}=source;
                const previous=peer.getTrackPublication(ScreenShare);
                if (previous) {
                    previous.track.stop();
                    peer.trackPublications.delete(ScreenShare);
                    this.emit('TrackUnsubscribed',previous.track);
                }
                if (!trackSid) return;
                peer.sid=sid;
                peer.permissions={canPublish:true,canPublishSources:[3]};
                const canvas=document.createElement('canvas'); canvas.width=640; canvas.height=360;
                const ctx=canvas.getContext('2d');
                const track=new LocalVideoTrack(canvas.captureStream(10).getVideoTracks()[0]);
                track.source=ScreenShare;
                const pub={trackSid,source:ScreenShare,kind:'video',track,isMuted:false,isDesired:true,
                    setSubscribed(wanted){ this.isDesired=wanted; }};
                peer.trackPublications.set(ScreenShare,pub);
                this.emit('TrackSubscribed',track,pub,peer);
                ctx.fillStyle='#164cfa'; ctx.fillRect(0,0,640,360);
            };
            stats.remoteCamera = (trackSid, id=remote, sid=`PA_${id}`) => {
                const peer = this.remoteParticipants.get(id) ?? person(id), {Camera} = source;
                this.remoteParticipants.set(id, peer);
                const previous = peer.getTrackPublication(Camera);
                if (previous) {
                    previous.track.stop();
                    peer.trackPublications.delete(Camera);
                    this.emit('TrackUnsubscribed',previous.track,previous,peer);
                }
                if (!trackSid) return;
                peer.sid = sid;
                const canvas = document.createElement('canvas'); canvas.width=160; canvas.height=90;
                const pixels = canvas.getContext('2d');
                const track = new LocalVideoTrack(canvas.captureStream(10).getVideoTracks()[0]);
                track.source = Camera;
                const pub = {trackSid,source:Camera,kind:'video',track,isMuted:false,isDesired:true,
                    setSubscribed(wanted){this.isDesired=wanted;}};
                peer.trackPublications.set(Camera,pub);
                this.emit('TrackSubscribed',track,pub,peer);
                pixels.fillStyle='#ac278e'; pixels.fillRect(0,0,160,90);
            };
            stats.permission = allow => {
                const previous = local.permissions;
                local.permissions = {canPublish:true,canPublishSources:allow?[1,2,3]:[1,2]};
                this.emit('ParticipantPermissionsChanged',previous,local);
            };
            local.publishTrack = async (track,opts) => {
                track.source=opts.source;
                local.trackPublications.set(opts.source,{kind:'video',isMuted:false,track,trackSid:`TR_local_${stats.published.length + 1}`});
                stats.published.push(opts);
                this.emit('LocalTrackPublished');
            };
            local.unpublishTrack = async track => {
                local.trackPublications.delete(track.source);
                this.emit('LocalTrackUnpublished');
            };
        }
        on(event, callback) { this.events.set(event,callback); }
        emit(event,...args) { this.events.get(event)?.(...args); }
        async connect() { this.state='connected'; this.emit('ConnectionStateChanged'); }
        async disconnect() { this.state='disconnected'; }
        async startAudio() {}
    }
    class LocalVideoTrack {
        constructor(raw) { this.mediaStreamTrack=raw; this.kind='video'; }
        stop() { this.mediaStreamTrack.stop(); }
        attach(node) { node.srcObject=new MediaStream([this.mediaStreamTrack]); }
        detach(node) { node.srcObject=null; }
    }
    Object.defineProperty(navigator.mediaDevices,'getDisplayMedia',{configurable:true,value:unsupported ? undefined : async opts => {
        const active = navigator.userActivation.isActive;
        stats.clicks.push({opts,active});
        const canvas=document.createElement('canvas'); canvas.width=960; canvas.height=540;
        canvas.getContext('2d').fillRect(10,10,100,100);
        const stream=canvas.captureStream(10);
        stats.raw=stream.getVideoTracks()[0];
        return stream;
    }});
    window.LivekitClient={Room,LocalVideoTrack,Track:{Source:source,Kind:{Video:'video',Audio:'audio'}},ConnectionState:{Connected:'connected'},
        RoomEvent:new Proxy({}, {get:(_,key)=>key})};
}

test('packaged pupil gets a labelled screen button by mic and camera only after the teacher approves this SID', {skip, timeout:38000}, async t => {
    const fixture=packaged(t), staffHTML=fixture.render('teacher',true), pupilHTML=fixture.render('student',true);
    const lesson='fixture-lesson', epoch='fixture-epoch';
    let optIn=false, screen=null;
    const members={'teacher-1':'PA_teacher-1','student-1':'PA_student-1'};
    const requests=[];
    const {browser,origin}=await serveBrowser(t,async (req,res,url) => {
        const ref=new URL(req.headers.referer || '/',url), staff=ref.pathname==='/teacher';
        const identity=staff?'teacher-1':'student-1';
        if (['/teacher','/student','/unsupported','/blocked'].includes(url.pathname)) {
            const policy=url.pathname==='/blocked' ? 'display-capture=()' : 'display-capture=(self)';
            return send(res,'text/html; charset=UTF-8',url.pathname==='/teacher'?staffHTML:pupilHTML,
                {'Permissions-Policy':policy,'Cache-Control':'no-store'});
        }
        if (url.pathname==='/fixture.js') return send(res,'text/javascript',
            `(${fakeMedia})(${JSON.stringify(identity)},${JSON.stringify(staff?'student-1':'teacher-1')},${ref.pathname==='/unsupported'});`,
            {'Cache-Control':'no-store'});
        if (url.searchParams.get('route')==='api') {
            let content=''; for await (const chunk of req) content+=chunk;
            const body=JSON.parse(content);
            requests.push({identity,body});
            assert.equal(body.class,22);
            let result={ok:true};
            if (body.action==='status') {
                result={...result, phase:'live',lesson,epoch,ready:true,joinable:true,slot:11,
                    screenEnabled:staff || optIn,pupilScreenEnabled:staff && optIn,screen, cameraCodec:'vp8',wakeMode:'system'};
                if (body.sid===members[identity]) {
                    Object.assign(result,{admitted:true,members,profiles:{'student-1':{name:'دانش‌آموز آزمایشی'}}, qualityRev:'fixture-quality',
                        quality:{camera:{width:128,height:128,frameRate:8,bitrate:25000},
                            screen:staff?{width:640,height:360,frameRate:5,bitrate:70000}:{width:960,height:540,frameRate:10,bitrate:315000}}});
                    if (body.qualityRev===result.qualityRev) delete result.quality;
                }
            } else if (body.action==='ticket') Object.assign(result,{lesson,epoch,url:'wss://fixture.invalid',token:'fixture'});
            else if (body.action==='screen-access') {
                assert.ok(optIn && staff && body.sid===members[identity] && body.lesson===lesson && body.epoch===epoch);
                assert.ok(body.identity==='student-1' && body.targetSid===members['student-1']);
                screen=body.enabled?{identity:body.identity,sid:body.targetSid}:null;
                result.screen=screen;
            }
            return send(res,'application/json',JSON.stringify(result),{'Cache-Control':'no-store'});
        }
        if (!fixture.serve(url,res)) res.writeHead(404).end();
    });
    const teacher=await browser.newPage(), pupil=await browser.newPage({viewport:{width:320,height:650}});
    const errors=[];
    for (const page of [teacher,pupil]) { page.setDefaultTimeout(7200); page.on('pageerror',e=>errors.push(e.message)); }
    await Promise.all([teacher.goto(`${origin}/teacher`),pupil.goto(`${origin}/student`)]);
    const grant=teacher.locator('#people [data-identity="student-1"] [data-feature="screen"]');
    const share=pupil.locator('#screen-toggle'), hint=pupil.locator('#screen-hint');
    await Promise.all([teacher.waitForFunction(()=>document.querySelector('#classroom').dataset.live==='yes'),pupil.locator('#camera').waitFor()]);
    assert.equal(await share.isVisible(),false);
    assert.equal(await grant.isVisible(),false,'pupil publishing needs global opt-in');
    optIn=true;
    await pupil.locator('#board-expand').click();
    await grant.click();
    await pupil.locator('#board-screen-hint').waitFor();
    assert.match(await pupil.locator('#board-screen-hint').textContent(),/همگام‌سازی/);
    await pupil.locator('#board-expand').click();
    await share.waitFor();
    assert.equal(await share.isDisabled(),true,'a lease alone cannot bypass the signaling permission');
    assert.equal(await share.textContent().then(x=>x.trim()),'اشتراک صفحه');
    assert.deepEqual(await pupil.locator('.room-controls > button').evaluateAll(nodes=>nodes.slice(0,3).map(n=>n.id)),
        ['microphone','camera','screen-toggle']);
    const shareBox=await share.boundingBox(), cameraBox=await pupil.locator('#camera').boundingBox();
    assert.ok(shareBox.x>=0 && shareBox.x+shareBox.width<=320 && Math.abs(shareBox.y-cameraBox.y)<4,'controls fit side-by-side at 320px');
    assert.equal(await teacher.locator('#screen-toggle').isDisabled(),true,'no second presenter while the pupil has a lease');

    await pupil.evaluate(()=>window.screenFixture.permission(true));
    assert.equal(await share.isEnabled(),true);
    assert.match(await hint.textContent(),/دبیر اجازه داد/);
    for (const path of ['/unsupported','/blocked']) {
        const device=await browser.newPage({viewport:{width:320,height:650},isMobile:true,hasTouch:true});
        await device.goto(origin+path);
        const button=device.locator('#screen-toggle'), warning=device.locator('#screen-warning');
        const expand=device.locator('#board-expand');
        await button.waitFor({timeout:6500});
        assert.equal(await button.isEnabled(),true,'the blocked browser can ask for guidance without a synced SFU permission');
        assert.equal(await device.locator('#screen-hint').isVisible(),false);
        assert.equal(await warning.isVisible(),false);
        await expand.tap();
        assert.equal(await device.locator('#board-screen-hint').isVisible(),false);
        await expand.tap();
        await button.tap();
        assert.match(await warning.textContent(),path==='/blocked'?/بسته است/:/مرورگر امکان اشتراک صفحه ندارد/);
        await warning.tap();
        assert.equal(await warning.isVisible(),true,'inside taps do not dismiss the warning');
        await device.locator('.room-name').tap();
        assert.equal(await warning.isVisible(),false,'an outside tap dismisses the warning');
        await button.tap(); assert.equal(await warning.isVisible(),true);
        await button.press('Escape'); assert.equal(await warning.isVisible(),false);
        assert.deepEqual(await device.evaluate(()=>[window.screenFixture.clicks.length,window.screenFixture.published.length]),[0,0]);
        await device.close();
    }
    assert.deepEqual(await teacher.evaluate(()=>window.screenFixture.clicks),[]);
    assert.deepEqual(await pupil.evaluate(()=>window.screenFixture.clicks),[],'approval does not open the picker');
    await share.click();
    await pupil.waitForFunction(()=>window.screenFixture.published.length===1);
    const {clicks,published}=await pupil.evaluate(()=>({clicks:window.screenFixture.clicks,published:window.screenFixture.published}));
    assert.equal(clicks.length,1); assert.ok(clicks[0].active);
    assert.equal(clicks[0].opts.audio,false); assert.equal(clicks[0].opts.systemAudio,'exclude');
    assert.equal(clicks[0].opts.video.width.max,960); assert.equal(published[0].source,'screen_share');
    assert.equal(published[0].simulcast,false);
    assert.equal(requests.some(r=>r.identity==='student-1' && r.body.action==='screen-access'),false,'using the permission opens only the local picker');
    await grant.click();
    await share.waitFor({state:'hidden'});
    assert.equal(await pupil.evaluate(()=>window.screenFixture.raw.readyState),'ended');
    assert.equal(await hint.isVisible(),false);
    assert.deepEqual(requests.filter(r=>r.body.action==='screen-access').map(r=>r.body.enabled),[true,false]);
    assert.deepEqual(errors,[]);
});

test('only a recording presenter can compose the archive, apart from live sharing and unsupported-browser guidance', {skip, timeout:38000}, async t => {
    const fixture=packaged(t), staff=fixture.render('teacher',true), admin=fixture.render('admin',true), pupil=fixture.render('student',true);
    const pages={'/teacher':staff,'/unsupported-staff':staff,'/admin':admin,'/pupil':pupil};
    const lesson='archive-lesson', epoch='archive-epoch', id='d'.repeat(32), requests=[];
    const screen={identity:'student-1',sid:'PA_student-1'};
    let presenter='teacher-1', activeScreen=null, session='off', archive={board:false,screen:null};
    const {browser,origin}=await serveBrowser(t,async (req,res,url)=>{
        const path=new URL(req.headers.referer || '/',url).pathname;
        const identity=path==='/admin'?'admin-1':path==='/pupil'?'student-1':'teacher-1';
        if (Object.hasOwn(pages,url.pathname)) return send(res,'text/html; charset=UTF-8',pages[url.pathname]);
        if (url.pathname==='/fixture.js') return send(res,'text/javascript',
            `(${fakeMedia})(${JSON.stringify(identity)},${JSON.stringify(identity==='student-1'?'teacher-1':'student-1')},${path==='/unsupported-staff'});`,
            {'Cache-Control':'no-store'});
        if (url.searchParams.get('route')==='api') {
            let buf=''; for await (const chunk of req) buf+=chunk;
            const input=JSON.parse(buf), result={ok:true};
            requests.push({identity,input});
            const admitted=input.lesson===lesson && input.epoch===epoch && input.sid===`PA_${identity}`;
            const board={rev:1,cursor:0,write:identity!=='student-1',navigate:identity===presenter,owner:'c'.repeat(16),
                ...(input.board===1?{}:{reset:true,doc:null,ops:[],docs:[],page:1})};
            switch(input.action) {
                case 'status':
                    Object.assign(result,{phase:'live',lesson,epoch,ready:true,joinable:true,slot:11,admitted});
                    if (admitted) Object.assign(result,{board,screen:activeScreen,screenEnabled:true,wakeMode:'system',
                        quality:{screen:{width:640,height:360,frameRate:10,bitrate:180000}},
                        recording: session==='off'?null:{id,notice:'live',...(session==='new' && identity===presenter?{archive}: {})}});
                    break;
                case 'board-sync':
                    assert.ok(admitted); result.board=board; break;
                case 'ticket':
                    Object.assign(result,{lesson,epoch,url:'wss://fixture.invalid',token:'fixture'}); break;
                case 'record-source':
                    assert.ok(admitted && identity===presenter && session==='new');
                    if (input.source==='board') archive.board=input.enabled;
                    else {
                        assert.equal(input.publisherSid,screen.sid);
                        assert.match(input.track,/^TR_screen[12]$/);
                        archive.screen=input.enabled?input.track:null;
                    }
                    result.archive=archive; break;
                default: assert.fail(`unexpected fixture action ${input.action}`);
            }
            return send(res,'application/json',JSON.stringify(result));
        }
        if (!fixture.serve(url,res)) res.writeHead(404).end();
    });
    async function open(path, width=540) {
        const page=await browser.newPage({viewport:{width,height:680},isMobile:width===320});
        page.setDefaultTimeout(8500);
        await page.goto(origin+path);
        await page.locator('#classroom[data-live=yes]').waitFor();
        return page;
    }
    const phone=await open('/unsupported-staff',320);
    const share=phone.locator('#screen-toggle'), warning=phone.locator('#screen-warning');
    await share.waitFor();
    assert.ok(await share.isEnabled());
    assert.equal(await warning.isVisible(),false);
    await share.click();
    assert.match(await warning.textContent(),/محدودیت از مرورگر است، نه اجازهٔ کلاس/);
    assert.deepEqual(await phone.evaluate(()=>window.screenFixture.clicks),[]);
    assert.ok(!requests.some(r=>r.input.action==='screen-access'),'no lease reserved by an unsupported teacher');
    await phone.close();
    const [teacher,deputy,student]=await Promise.all([open('/teacher'),open('/admin'),open('/pupil',320)]);
    const btn=teacher.locator('#record-board-display'), shareBtn=teacher.locator('#record-screen-display');
    assert.equal(await student.locator('.archive-source').count(),0);
    assert.equal(await btn.isVisible(),false);
    session='old';
    await teacher.locator('#classroom[data-recording=yes]').waitFor();
    assert.equal(await btn.isVisible(),false,'a protocol-v1 worker without layout support has no toggles');
    session='new'; activeScreen=screen;
    await btn.waitFor();
    assert.equal((await btn.textContent()).trim(),'نمایش در ضبط');
    assert.equal(await btn.getAttribute('aria-pressed'),'false');
    assert.equal(await shareBtn.isVisible(),false,'the lease alone is not a published screen');
    await teacher.evaluate(()=>window.screenFixture.remoteScreen('TR_screen1','PA_Former'));
    assert.equal(await teacher.locator('#screen-panel').isVisible(),false);
    await teacher.evaluate(()=>window.screenFixture.remoteScreen('TR_screen1'));
    await shareBtn.waitFor();
    assert.equal((await shareBtn.textContent()).trim(),'نمایش در ضبط');
    const panel=teacher.locator('#screen-panel'), before=await panel.boundingBox();
    await btn.click();
    await teacher.locator('#record-board-display[aria-pressed=true]').waitFor();
    await shareBtn.click();
    await teacher.locator('#record-screen-display[aria-pressed=true]').waitFor();
    assert.deepEqual(await panel.boundingBox(),before,'archive controls do not change the size or visibility of the live screen');
    const layoutRequests=()=>requests.filter(r=>r.input.action==='record-source');
    assert.deepEqual(layoutRequests().map(r=>r.input),[
        {class:22,action:'record-source',lesson,epoch,sid:'PA_teacher-1',source:'board',enabled:true},
        {class:22,action:'record-source',lesson,epoch,sid:'PA_teacher-1',source:'screen',enabled:true,track:'TR_screen1',publisherSid:screen.sid},
    ]);
    await teacher.evaluate(()=>window.screenFixture.remoteScreen(null));
    await shareBtn.waitFor({state:'hidden'});
    await teacher.evaluate(()=>window.screenFixture.remoteScreen('TR_screen2'));
    await shareBtn.waitFor();
    assert.equal(await shareBtn.getAttribute('aria-pressed'),'false','a replacement publication is not automatically promoted');
    const replacementPanel=await panel.boundingBox();
    await btn.click();
    await teacher.locator('#record-board-display[aria-pressed=false]').waitFor();
    assert.deepEqual(await panel.boundingBox(),replacementPanel);
    assert.equal(await deputy.locator('.archive-source:visible').count(),0);
    presenter='admin-1';
    await deputy.locator('#record-board-display').waitFor();
    await btn.waitFor({state:'hidden'});
    assert.equal(await shareBtn.isVisible(),false);
    assert.equal(await panel.isVisible(),true,'presenter handoff hides recorder controls, not the live screen');
    session='off';
    await deputy.locator('#record-board-display').waitFor({state:'hidden'});
    assert.equal(layoutRequests().length,3);
});

test('packaged webcam tile has an independent recorder toggle only for the active presenter with r3 support', {skip,timeout:38000}, async t=>{
    const fixture = packaged(t), lessons = {staff:fixture.render('teacher',true),pupil:fixture.render('student',true)};
    const lesson = 'camera-lesson', epoch = 'camera-epoch', id = 'a'.repeat(32), clicked = [];
    let supportsCamera = false, pinned = null, presenter = true;
    const {browser,origin} = await serveBrowser(t, async (req,res,url)=>{
        const who = new URL(req.headers.referer || '/',url).pathname === '/pupil' ? 'student-1' : 'teacher-1';
        const remote = who === 'teacher-1' ? 'student-1' : 'teacher-1';
        if (url.pathname.slice(1) in lessons) return send(res,'text/html; charset=UTF-8',lessons[url.pathname.slice(1)]);
        if (url.pathname === '/fixture.js') return send(res,'text/javascript',`(${fakeMedia})('${who}','${remote}',false);`);
        if (url.searchParams.get('route') === 'api') {
            let raw=''; for await (const chunk of req) raw += chunk;
            const body = JSON.parse(raw), admitted = body.lesson === lesson && body.epoch === epoch && body.sid === `PA_${who}`;
            const navigate = presenter && who === 'teacher-1';
            const board = {rev:1,cursor:0,navigate,write:navigate,owner:'a'.repeat(16),reset:true,doc:null,ops:[],docs:[],page:1};
            const archive = () => ({board:true,screen:null,...(supportsCamera?{camera:pinned}:{})});
            const out = {ok:true};
            if (body.action === 'status') Object.assign(out,{phase:'live',lesson,epoch,ready:true,joinable:true,slot:11,admitted,
                ...(admitted ? {board,cameraCodec:'vp8',mediaMode:'standard',recording:{id,notice:'live',...(navigate ? {archive:archive()} : {})}} : {})});
            else if (body.action === 'board-sync') out.board = board;
            else if (body.action === 'ticket') Object.assign(out,{lesson,epoch,url:'wss://fixture.invalid',token:'fixture'});
            else if (body.action === 'record-source') {
                assert.ok(admitted && navigate && supportsCamera && body.source === 'camera');
                assert.equal(body.publisherSid, `PA_${body.identity}`);
                clicked.push([body.identity,body.track,body.enabled]);
                pinned = body.enabled ? {identity:body.identity,sid:body.publisherSid,track:body.track} : null;
                out.archive = archive();
            } else assert.fail(`unexpected fixture action ${body.action}`);
            return send(res,'application/json',JSON.stringify(out));
        }
        if (!fixture.serve(url,res)) res.writeHead(404).end();
    });
    const open = async role => {
        const page = await browser.newPage({viewport:{width:360,height:680}});
        page.setDefaultTimeout(8000);
        await page.goto(`${origin}/${role}`);
        await page.locator('#classroom[data-live=yes]').waitFor();
        return page;
    };
    const [teacher,pupil] = await Promise.all([open('staff'),open('pupil')]);
    await teacher.evaluate(()=>{
        window.screenFixture.remoteCamera('TR_pupil1');
        window.screenFixture.remoteCamera('TR_pupil2','student-2');
    });
    await pupil.evaluate(()=>window.screenFixture.remoteCamera('TR_teacher1'));
    const tile = id => teacher.locator(`#camera-strip figure[data-identity="${id}"]`);
    const archive = id => tile(id).locator('.camera-archive-source');
    const first = archive('student-1'), second = archive('student-2');
    await tile('student-1').locator('video').waitFor();
    await pupil.locator('#camera-strip video').waitFor();
    assert.equal(await pupil.locator('.camera-archive-source').count(),0);
    assert.equal(await first.isVisible(),false,'earlier workers can still control the board, not a webcam');
    assert.ok(await teacher.locator('#record-board-display').isVisible());
    supportsCamera=true;
    await first.waitFor();
    assert.equal((await first.textContent()).trim(),'نمایش در ضبط');
    assert.equal(await first.evaluate(button=>button.nextElementSibling.className),'camera-tile-button');
    const preview = teacher.locator('#camera-preview');
    const thumb = tile('student-1').locator('.camera-tile-button');
    const before = await thumb.boundingBox();
    await first.click();
    await tile('student-1').locator('[aria-pressed=true]').waitFor();
    assert.equal(await preview.isVisible(),false);
    assert.deepEqual(await thumb.boundingBox(),before,'pinning changes the recording only');
    await second.click();
    await tile('student-2').locator('[aria-pressed=true]').waitFor();
    assert.equal(await first.getAttribute('aria-pressed'),'false','only one camera at a time');
    await teacher.evaluate(()=>window.screenFixture.remoteCamera('TR_new','student-2'));
    await second.waitFor();
    assert.equal(await second.getAttribute('aria-pressed'),'false','a new track is never implicitly selected');
    await second.click();
    await tile('student-2').locator('[aria-pressed=true]').waitFor();
    await second.click();
    await tile('student-2').locator('.camera-archive-source[aria-pressed=false]').waitFor();
    await thumb.click();
    await preview.waitFor();
    await teacher.keyboard.press('Escape');
    presenter=false;
    await first.waitFor({state:'hidden'});
    await thumb.waitFor();
    assert.deepEqual(clicked,[['student-1','TR_pupil1',true],['student-2','TR_pupil2',true],
        ['student-2','TR_new',true],['student-2','TR_new',false]]);
});

test('packaged teacher and admin recording controls, Friday retry, pupil notice and explicit yes/no on close', {skip, timeout:38000}, async t => {
    const fixture=packaged(t), pages={teacher:fixture.render('teacher',true),admin:fixture.render('admin',true),pupil:fixture.render('student',true)};
    const lesson='active-lesson', epoch='epoch', rec='a'.repeat(32), decisions=[];
    let recording=false, closed=false, operator='teacher', slot=11, opens=0;
    const {browser,origin}=await serveBrowser(t,async (req,res,url) => {
        const role=new URL(req.headers.referer || '/',url).pathname.slice(1);
        const staff=role==='teacher' || role==='admin';
        const identity=staff?`${role}-1`:'student-1';
        if (Object.hasOwn(pages,url.pathname.slice(1))) return send(res,'text/html; charset=UTF-8',pages[url.pathname.slice(1)]);
        if (url.pathname==='/fixture.js') return send(res,'text/javascript',`(${fakeMedia})(${JSON.stringify(identity)},${JSON.stringify(staff?'student-1':`${operator}-1`)},false);`);
        if (url.searchParams.get('route')==='api') {
            let buf=''; for await (const chunk of req) buf+=chunk;
            const input=JSON.parse(buf), result={ok:true};
            const controls=()=>recording ? {notice:'live', ...(staff?{id:rec,confirm:true,canSend:true,group:'گروه آزمایشی',messenger:'روبیکا',delayMinutes:165}:{})}
                : staff ? {start:true}:null;
            if (input.action==='status') {
                Object.assign(result,closed?{phase:'idle',ready:true}:{phase:'live',lesson,epoch,ready:true,joinable:true,slot,
                    admitted:input.sid===`PA_${identity}`,recording:controls(), quality:{levels:{}}});
            } else if (input.action==='open') {
                assert.equal(input.slot,'friday-test');
                assert.equal(++opens,1,'other requests reuse the existing Friday room');
                res.writeHead(503,{'Content-Type':'application/json'}).end(JSON.stringify({ok:false,message:'temporary fixture error'}));
                return;
            } else if (input.action==='ticket') Object.assign(result,{lesson,epoch,url:'wss://fixture.invalid',token:'fixture'});
            else if (input.action==='start-recording') {
                assert.ok(staff && role===operator && input.lesson===lesson && input.sid===`PA_${identity}` && input.epoch===epoch);
                recording=true; result.recording=controls();
            } else if (input.action==='close') {
                assert.ok(staff && role===operator);
                decisions.push(input);
                result.recording=input.sendRecording?'queued':'discarded'; closed=true;
            }
            return send(res,'application/json',JSON.stringify(result));
        }
        if (!fixture.serve(url,res)) res.writeHead(404).end();
    });
    const pupil=await browser.newPage({viewport:{width:320,height:600}});
    await pupil.goto(origin+'/pupil');
    assert.equal(await pupil.locator('#record-start').count(),0);
    for (const [role,yes] of [['teacher',false],['admin',true]]) {
        operator=role;
        const demo=role==='admin';
        slot=demo?'friday-test':11;
        const page=await browser.newPage({viewport:{width:540,height:700},reducedMotion:role==='admin'?'reduce':'no-preference'});
        const errs=[];
        page.on('pageerror',error=>errs.push(error.message));
        await page.goto(`${origin}/${role}`);
        if (demo) {
            await page.getByRole('button',{name:'ورود به آزمایش جمعه',exact:true}).click();
            await page.locator('#classroom[data-live=yes]').waitFor({timeout:5000});
            assert.equal(opens,1);
        }
        await page.locator('#toggle-access').click();
        const start=page.locator('#record-start');
        await start.waitFor({timeout:10000});
        assert.equal(await start.evaluate(el=>el.nextElementSibling.id),'quality-open','between board access and settings');
        assert.equal(await start.textContent().then(s=>s.trim()),'ضبط کلاس');
        const header = await page.locator('.room-top').boundingBox();
        const pupilHeader = await pupil.locator('.room-top').boundingBox();
        await start.click();
        assert.match(await page.locator('#record-start-help').textContent(),/دبیر همین زنگ یا مدیر مجاز/);
        assert.equal(await page.locator('#record-practice-help').isVisible(),demo);
        assert.equal(recording,false,'opening the dialog does not start capture');
        await page.locator('#record-start-yes').click();
        const activeDot = (p, timeout=8000) => p.locator('#classroom[data-recording=yes]').waitFor({timeout});
        await activeDot(page); await activeDot(pupil);
        assert.match(await pupil.locator('#recording-status').textContent(),/کلاس در حال ضبط است/);
        assert.equal(await pupil.locator('#recording-status').evaluate(el=>el.getBoundingClientRect().height),1);
        assert.equal(await page.locator('#recording-notice').count(),0);
        assert.deepEqual(await page.locator('.room-top').boundingBox(),header);
        assert.deepEqual(await pupil.locator('.room-top').boundingBox(),pupilHeader);
        const light = async (p,sel) => p.locator(sel).evaluate(el=>{
            const style=getComputedStyle(el,'::before');
            return [style.backgroundColor,style.animationName,style.borderRadius];
        });
        assert.deepEqual(await light(page,'#status-dot'),['rgb(255, 77, 98)',role==='admin'?'none':'recording-blink','50%']);
        await pupil.locator('#board-expand').click();
        assert.deepEqual(await light(pupil,'.board-signal'),['rgb(255, 77, 98)','recording-blink','50%']);
        await pupil.locator('#board-expand').click();
        assert.equal(await pupil.getByText('گروه آزمایشی').count(),0,'group is private to staff');
        await page.locator('#toggle-access').click();
        await page.getByRole('button',{name:demo?'بستن اتاق آزمایشی برای همه':'بستن کلاس برای همه'}).click();
        const dialog=page.getByRole('dialog',{name:'فیلم کلاس بعداً ساخته و در پیام‌رسان ارسال شود؟'});
        await dialog.waitFor();
        assert.match(await dialog.textContent(),/روبیکا — گروه آزمایشی/);
        assert.match(await dialog.textContent(),/۱۶۵ دقیقه پس از پایان کلاس/);
        const before=decisions.length;
        await dialog.getByRole('button',{name:'انصراف از بستن کلاس'}).click();
        assert.equal(decisions.length,before,'cancel does not close, grant or send');
        await page.locator('#toggle-access').click();
        await page.getByRole('button',{name:demo?'بستن اتاق آزمایشی برای همه':'بستن کلاس برای همه'}).click();
        await dialog.getByRole('button',{name:yes?'بله':'خیر',exact:true}).click();
        await page.locator('#classroom[data-recording=no]').waitFor();
        await pupil.locator('#classroom[data-recording=no]').waitFor({timeout:9000});
        assert.deepEqual(decisions.at(-1),{action:'close',class:22,lesson,recording:rec,sendRecording:yes});
        if (demo) assert.match(await page.locator('#notice').textContent(),/گزارشی ثبت نشد.*در صف/);
        assert.deepEqual(errs,[]);
        await page.close();
        recording=false; closed=false;
    }
});

test('manager-led board restores real upload, zoom and grants; the arriving teacher takes over', {skip,timeout:50000}, async t=>{
    const fixture=packaged(t), html=Object.fromEntries(['admin','teacher','student'].map(role=>[role,fixture.render(role,true)]));
    const lesson='active-lesson', epoch='epoch', pdf=twoPageBoardPDF();
    const members={'admin-1':'PA_admin-1','teacher-1':'PA_teacher-1','student-1':'PA_student-1'};
    let rev=1, pageNumber=1, view, doc=null, ink=[], granted=false, presenter='admin';
    const documents=new Map();
    const requests=[], files=[];
    const {browser,origin}=await serveBrowser(t,async (req,res,url)=>{
        const role=new URL(req.headers.referer || '/',url).pathname.slice(1), staff=role!=='student';
        const identity=`${role}-1`;
        if (Object.hasOwn(html,url.pathname.slice(1))) return send(res,'text/html; charset=UTF-8',html[url.pathname.slice(1)]);
        if (url.pathname==='/fixture.js') return send(res,'text/javascript',`(${fakeMedia})('${identity}','${staff?'student':'admin'}-1',false);`);
        if (url.searchParams.get('route')==='content') {
            assert.equal(url.searchParams.get('sid'),members[identity]);
            assert.equal(url.searchParams.get('doc'),doc.id);
            files.push([identity,doc.mime]);
            return send(res,doc.mime,documents.get(doc.id).bytes,{'Cache-Control':'no-store'});
        }
        if (url.searchParams.get('route')==='api') {
            const chunks=[]; for await (const chunk of req) chunks.push(chunk);
            const buf=Buffer.concat(chunks), type=req.headers['content-type'];
            let input, uploaded;
            if (type.startsWith('multipart/form-data;')) {
                const form=await new Response(buf,{headers:{'Content-Type':type}}).formData();
                uploaded=form.get('file');
                input={...Object.fromEntries(form),action:'upload',board:Number(form.get('board')),pages:Number(form.get('pages'))};
                delete input.file;
            } else input=JSON.parse(buf);
            const access=()=>({board:{all:granted,members:{}}});
            requests.push({identity,input});
            const board=()=>{
                const reset=input.board!==rev, write=granted||staff;
                return {rev,cursor:ink.length,write,owner:write?(staff?'a':'b').repeat(16):undefined,
                    navigate:role===presenter,view,viewMoving:!!input.view,ops:ink.slice(reset?0:input.ink),
                    ...(reset?{reset,doc,page:pageNumber,docs:staff?[...documents.values()].map(d=>d.meta):[]}:{})};
            };
            let out;
            const admitted=input.sid===members[identity] && input.epoch===epoch && input.lesson===lesson;
            if (input.action==='status' && admitted && role==='teacher') presenter='teacher';
            if (input.action==='status') out={phase:'live',ready:true,lesson,epoch,joinable:true,slot:11,wakeMode:'system',admitted,
                ...(admitted?{members,access:access(),board:board()}: {})};
            else if (input.action==='ticket') out={lesson,epoch,url:'wss://fixture.invalid',token:'fixture'};
            else {
                assert.ok(admitted);
                if (input.action==='board-sync') {
                    if (input.view && role===presenter && input.board===rev) view=input.view;
                    out={board:board()};
                } else if (input.action==='upload') {
                    assert.ok(role===presenter && input.board===rev);
                    const bytes=Buffer.from(await uploaded.arrayBuffer());
                    assert.equal(input.pages,uploaded.type==='application/pdf'?2:1);
                    if (uploaded.type==='application/pdf') assert.deepEqual(bytes,pdf);
                    doc={id:`file-${rev}`,mime:uploaded.type,name:uploaded.name,bytes:bytes.length,pages:input.pages};
                    documents.set(doc.id,{meta:doc,bytes});
                    pageNumber=1; view=undefined; ink=[]; out={boardRev:++rev};
                } else if (input.action==='board-page') {
                    assert.ok(role===presenter && input.doc===doc.id && input.board===rev);
                    pageNumber=input.page; view=undefined; ink=[]; out={boardRev:++rev};
                } else if (input.action==='set-access') {
                    assert.ok(role===presenter && input.feature==='board'); granted=input.enabled; out={access:access()};
                } else if (input.action==='board-ink') {
                    assert.ok((staff || granted) && input.board===rev);
                    ink.push(...input.strokes); out={ink:ink.length};
                } else throw new Error(`unexpected request: ${input.action}`);
            }
            return send(res,'application/json',JSON.stringify({ok:true,...out}));
        }
        if (!fixture.serve(url,res)) res.writeHead(404).end();
    });
    const manager=await browser.newPage({viewport:{width:860,height:760}}), pupil=await browser.newPage({viewport:{width:320,height:690}});
    const teacher=await browser.newPage({viewport:{width:1080,height:790}});
    const errors=[];
    for (const p of [manager,teacher,pupil]) {p.setDefaultTimeout(6800); p.on('pageerror',e=>errors.push(e.message));}
    const color=(p,name,rgb,at=[5000,5000])=>p.waitForFunction(({name,rgb,at})=>{
        const canvas=document.getElementById(name);
        const data=canvas.getContext('2d').getImageData(Math.floor(canvas.width*at[0]/10000),Math.floor(canvas.height*at[1]/10000),1,1).data;
        return rgb.every((v,i)=>data[i]===v);
    },{name,rgb,at});
    await Promise.all([manager.goto(`${origin}/admin`),pupil.goto(`${origin}/student`)]);
    await manager.locator('#toggle-files').click();
    const image=await manager.evaluate(()=>{
        const c=document.createElement('canvas'); c.width=8; c.height=8;
        const ctx=c.getContext('2d'); ctx.fillStyle='#ea1766'; ctx.fillRect(0,0,8,8);
        return c.toDataURL('image/png').split(',')[1];
    });
    const upload=async (buffer,name,mimeType)=>{
        await manager.locator('#board-file').setInputFiles({buffer,name,mimeType});
        await manager.locator('#board-upload button').click();
    };
    await upload(Buffer.from(image,'base64'),'image.png','image/png');
    await Promise.all([manager,pupil].map(p=>color(p,'board-paper',[234,23,102])));
    await upload(pdf,'fixture.pdf','application/pdf');
    await Promise.all([manager,pupil].map(p=>color(p,'board-paper',[255,255,0])));
    await manager.locator('#page-next').click(); await color(pupil,'board-paper',[0,255,255]);
    await manager.locator('#page-prev').click(); await color(pupil,'board-paper',[255,255,0]);
    await manager.locator('#board-zoom-in').click();
    const waitZoom = expected => pupil.waitForFunction(expected=>document.getElementById('board-zoom-shared').textContent===expected,expected);
    await waitZoom('۱۲۵٪');
    await manager.locator('#board-zoom-out').click(); await waitZoom('۱۰۰٪');
    await manager.locator('#toggle-access').click();
    await manager.locator('#all-board-give').click(); await pupil.locator('#pen').waitFor();
    assert.equal(await pupil.locator('#board-zoom-in,#page-next,#toggle-files').count(),0,'grant still only gives ink');
    await manager.locator('#all-board-cut').click(); await pupil.locator('#pen').waitFor({state:'hidden'});

    await teacher.goto(`${origin}/teacher`);
    await color(teacher,'board-paper',[255,255,0]);
    await manager.locator('#board-zoom-in').waitFor({state:'hidden'});
    await manager.locator('#all-board-give').waitFor({state:'hidden'});
    assert.deepEqual(files.filter(([,mime])=>mime==='application/pdf').map(([who])=>who).sort(),['admin-1','student-1','teacher-1']);
    const received=files.length;
    assert.equal(await pupil.locator('#board-load-file').isVisible(),false);
    assert.equal(await pupil.locator('#pen').isVisible(),false);
    for (let i=0;i<3;i++) await teacher.locator('#board-zoom-in').click();
    await pupil.waitForFunction(()=>document.getElementById('board-zoom-shared').textContent==='۲۰۰٪');
    const rect=(p,follow)=>p.evaluate(follow=>{
        const clip=document.getElementById(follow?'board-bed':'board-scroll'), stage=document.getElementById('board-stage').getBoundingClientRect();
        const box=clip.getBoundingClientRect();
        return [[box.left-stage.left,stage.width],[box.top-stage.top,stage.height],
            [box.left+clip.clientWidth-stage.left,stage.width],[box.top+clip.clientHeight-stage.top,stage.height]]
            .map(([n,d])=>Math.round(Math.min(1,Math.max(0,n/d))*10000));
    },follow);
    const close=(a,b)=>a.every((n,i)=>Math.abs(n-b[i])<55);
    const waitCrop=async () => {
        const want=await rect(teacher,false);
        for(let i=0;i<40;i++) {
            const have=await rect(pupil,true);
            if(close(have,want)) return have;
            await new Promise(r=>setTimeout(r,60));
        }
        assert.fail(`crop ${await rect(pupil,true)} did not converge to teacher ${want}`);
    };
    await waitCrop();
    await teacher.locator('#pan').click();
    const boardBox=await teacher.locator('#board-scroll').boundingBox();
    const x=boardBox.x+boardBox.width*.6,y=boardBox.y+boardBox.height*.65;
    await teacher.mouse.move(x,y); await teacher.mouse.down(); await teacher.mouse.move(x-190,y-130,{steps:10}); await teacher.mouse.up();
    const crop=await waitCrop();
    assert.ok(crop[0]>900 && crop[1]>1300 && crop[3]-crop[1]<6000,'not a centered/full-page approximation');
    await pupil.locator('#board-expand').click();
    await pupil.setViewportSize({width:440,height:730});
    await waitCrop();
    await teacher.locator('#toggle-access').click();
    await teacher.locator('#all-board-give').click();
    await pupil.locator('#pen').waitFor();
    assert.equal(await pupil.locator('#pan,#page-next,#board-zoom-in,#toggle-files').count(),0);
    const clip=pupil.locator('#board-scroll');
    await clip.press('+'); await clip.press('ArrowRight');
    await clip.dispatchEvent('wheel',{ctrlKey:true,deltaY:-120});
    assert.deepEqual(await clip.evaluate(el=>{el.scrollLeft=400;el.scrollTop=400;return [el.scrollLeft,el.scrollTop];}),[0,0]);
    assert.ok(close(await rect(pupil,true),crop));
    const mark=[Math.round((crop[0]+crop[2])/2),Math.round((crop[1]+crop[3])/2)];
    await pupil.locator('#ink-color').fill('#cc0077');
    const touch=()=>pupil.locator('#board-ink').evaluate((c,mark)=>{const r=c.getBoundingClientRect();return [r.left+r.width*mark[0]/10000,r.top+r.height*mark[1]/10000];},mark);
    await pupil.mouse.click(...await touch());
    await color(teacher,'board-ink',[204,0,119,255],mark);
    await pupil.locator('#eraser').click(); await pupil.mouse.click(...await touch());
    await color(teacher,'board-ink',[0,0,0,0],mark);
    await teacher.locator('#all-board-cut').click();
    await pupil.locator('#pen').waitFor({state:'hidden'});
    await pupil.mouse.click(...await touch());
    await teacher.locator('#toggle-access').click();
    await teacher.locator('#page-next').click();
    await color(pupil,'board-paper',[0,255,255]);
    assert.equal(await pupil.locator('#board-page-label').textContent(),'۲');
    assert.equal(await pupil.locator('#board-zoom-shared').textContent(),'۱۰۰٪');
    assert.ok(close(await waitCrop(),[0,0,10000,10000]));
    assert.equal(files.length,received,'zooming, erasing, page changes and resize do not reload the same PDF');
    const writes=requests.filter(({identity,input})=>identity==='student-1' && !['status','board-sync','ticket'].includes(input.action));
    assert.deepEqual(writes.map(({input})=>input.strokes[0].tool),['pen','erase-stroke']);
    assert.ok(!requests.some(({identity,input})=>identity==='student-1' && input.view));
    assert.deepEqual(errors,[]);
});

test('packaged mobile board keeps touch tools accessible and opens only the full pupil chat notice', {skip, timeout:34000}, async t => {
    const fixture=packaged(t), html=fixture.render('teacher',true), pdf=twoPageBoardPDF();
    const lesson='mobile-lesson', epoch='mobile-epoch', sid='PA_teacher-1';
    const doc={id:'a'.repeat(32),mime:'application/pdf',name:'تختهٔ آزمایشی.pdf',bytes:pdf.length,pages:2};
    let rev=1, pageNumber=1, profiles={'student-1':{name:'احسانی'}};
    const chat=[{n:1,author:'student-1',by:'احسانی',recent:true,text:'پیام قبلی'}];
    const {browser,origin}=await serveBrowser(t,async (req,res,url)=>{
        if (url.pathname==='/teacher') return send(res,'text/html; charset=UTF-8',html);
        if (url.pathname==='/fixture.js') return send(res,'text/javascript',`(${fakeMedia})('teacher-1','student-1',false);`);
        if (url.searchParams.get('route')==='content') return send(res,doc.mime,pdf);
        if (url.searchParams.get('route')==='api') {
            let content=''; for await (const chunk of req) content+=chunk;
            const input=JSON.parse(content), admitted=input.sid===sid && input.epoch===epoch && input.lesson===lesson;
            const board=()=>({rev,cursor:0,write:true,navigate:true,owner:'a'.repeat(16),ops:[],
                ...(input.board===rev?{}:{reset:true,doc,page:pageNumber,docs:[doc]})});
            let reply={ok:true};
            if (input.action==='ticket') Object.assign(reply,{lesson,epoch,url:'wss://fixture.invalid',token:'fixture'});
            else if (input.action==='status') {
                Object.assign(reply,{lesson,epoch,phase:'live',ready:true,joinable:true,slot:11,admitted});
                if (admitted) Object.assign(reply,{profiles,chat:chat.filter(row=>row.n>input.chat),board:board(),wakeMode:'system',wakeControl:true});
            } else {
                assert.ok(admitted);
                if (input.action==='board-sync') reply.board=board();
                else if (input.action==='board-page') { pageNumber=input.page; reply.boardRev=++rev; }
                else assert.fail(`unexpected mobile board action ${input.action}`);
            }
            return send(res,'application/json',JSON.stringify(reply));
        }
        if (!fixture.serve(url,res)) res.writeHead(404).end();
    });
    const page=await browser.newPage({viewport:{width:320,height:650},isMobile:true,hasTouch:true});
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.setDefaultTimeout(7200);
    await page.goto(origin+'/teacher');
    await page.locator('#page-next:enabled').waitFor();
    await page.getByText('پیام قبلی',{exact:true}).waitFor();
    const expand=page.locator('#board-expand'), info=page.locator('#board-join-toast'), preview=page.locator('#board-chat-preview');
    const detail=page.locator('#board-chat-detail'), message=page.locator('#board-chat-full');
    async function toolsFit(width) {
        const geometry=await page.locator('.board-toolbar').evaluate(toolbar=>{
            const tools=[...toolbar.querySelectorAll('button,summary,input')].filter(node=>node.getClientRects().length);
            return {width:document.documentElement.scrollWidth,
                tools:tools.map(node=>{ const r=node.getBoundingClientRect(); return [node.id||node.localName,r.left,r.right,r.height]; })};
        });
        assert.ok(geometry.width<=width,'no page overflow');
        for (const [id,left,right,height] of geometry.tools) {
            assert.ok(left>=0 && right<=width && height>=44,`${id} should fit horizontally and have a 44px touch target`);
        }
    }
    await toolsFit(320);
    await page.locator('#eraser').tap();
    assert.equal(await page.locator('#eraser').getAttribute('aria-pressed'),'true');
    await page.locator('#page-next').tap();
    await page.waitForFunction(()=>document.getElementById('board-page-label').textContent==='۲');
    await expand.tap();
    assert.equal(await expand.getAttribute('aria-pressed'),'true');
    await toolsFit(320);
    assert.equal(await preview.isVisible(),false,'chat history is not a new notice');
    assert.equal(await info.isVisible(),false,'the initial roster is not announced');
    await page.locator('#board-zoom-in').tap();
    assert.notEqual(await page.locator('#board-zoom').textContent(),'۱۰۰٪');

    const whole='الف'.repeat(66)+'\nمتن کامل <em>به‌صورت نوشته</em> — '+('ادامهٔ همان پیام دانش‌آموز. '.repeat(16));
    profiles={...profiles,'student-2':{name:'صادقی'}};
    chat.push({n:2,author:'student-1',by:'احسانی',recent:true,text:whole});
    await preview.waitFor();
    assert.equal(await page.locator('#board-chat-snippet').textContent(),'احسانی: '+whole.slice(0,59)+'…');
    assert.equal(await info.textContent(),'صادقی وارد کلاس شد');
    // The join announcement lets board input through; it has no button or detail action.
    assert.equal(await info.evaluate(node=>getComputedStyle(node).pointerEvents),'none');
    await preview.tap();
    assert.equal(await detail.isVisible(),true);
    assert.equal(await message.textContent(),whole);
    assert.equal(await message.locator('em').count(),0);
    await message.tap(); assert.equal(await detail.isVisible(),true);
    await info.waitFor({state:'hidden'});
    await page.locator('.board-heading h2').tap();
    assert.equal(await detail.isVisible(),false,'an outside tap closes the message, not the board');
    assert.equal(await expand.getAttribute('aria-pressed'),'true');
    chat.push({n:3,author:'student-1',by:'احسانی',recent:true,text:'پیام دوم'});
    await preview.tap();
    assert.equal(await message.textContent(),'پیام دوم');
    const close=page.getByRole('button',{name:'بستن پیام'});
    assert.equal(await close.locator('svg.icon-close').count(),1);
    await close.tap(); assert.equal(await detail.isVisible(),false);

    await page.setViewportSize({width:390,height:740});
    await toolsFit(390);
    await page.setViewportSize({width:650,height:320});
    await toolsFit(650);
    assert.ok((await page.locator('#board-scroll').boundingBox()).height>=125,'expanded landscape leaves room to draw');
    await page.locator('#page-prev').tap();
    await page.waitForFunction(()=>document.getElementById('board-page-label').textContent==='۱');
    await expand.tap(); assert.equal(await expand.getAttribute('aria-pressed'),'false');
    assert.deepEqual(errors,[]);
});
