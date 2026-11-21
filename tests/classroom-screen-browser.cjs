// Packaged room DOM and actual clicks in Chromium. School API and signaling are test-only;
// the display picker returns a synthetic canvas track, never a real screen.
const assert = require('node:assert/strict');
const test = require('node:test');
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
            stats.permission = allow => {
                const previous = local.permissions;
                local.permissions = {canPublish:true,canPublishSources:allow?[1,2,3]:[1,2]};
                this.emit('ParticipantPermissionsChanged',previous,local);
            };
            local.publishTrack = async (track,opts) => {
                track.source=opts.source;
                local.trackPublications.set(opts.source,{kind:'video',isMuted:false,track});
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
        const device=await browser.newPage({viewport:{width:320,height:650}});
        await device.goto(origin+path);
        await device.locator('#screen-toggle').waitFor({timeout:6500});
        assert.equal(await device.locator('#screen-toggle').isDisabled(),true);
        assert.match(await device.locator('#screen-hint').textContent(),path==='/blocked'?/بسته است/:/مرورگر امکان اشتراک صفحه ندارد/);
        assert.equal(await device.evaluate(()=>window.screenFixture.clicks.length),0);
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

test('packaged teacher and admin recording controls, pupil notice and explicit yes/no on close', {skip, timeout:38000}, async t => {
    const fixture=packaged(t), pages={teacher:fixture.render('teacher',true),admin:fixture.render('admin',true),pupil:fixture.render('student',true)};
    const lesson='active-lesson', epoch='epoch', rec='a'.repeat(32), decisions=[];
    let recording=false, closed=false, operator='teacher';
    const {browser,origin}=await serveBrowser(t,async (req,res,url) => {
        const role=new URL(req.headers.referer || '/',url).pathname.slice(1);
        const staff=role==='teacher' || role==='admin';
        const identity=staff?`${role}-1`:'student-1';
        if (Object.hasOwn(pages,url.pathname.slice(1))) return send(res,'text/html; charset=UTF-8',pages[url.pathname.slice(1)]);
        if (url.pathname==='/fixture.js') return send(res,'text/javascript',`(${fakeMedia})(${JSON.stringify(identity)},${JSON.stringify(staff?'student-1':`${operator}-1`)},false);`);
        if (url.searchParams.get('route')==='api') {
            let buf=''; for await (const chunk of req) buf+=chunk;
            const input=JSON.parse(buf), result={ok:true};
            const controls=()=>recording ? {notice:'live', ...(staff?{id:rec,confirm:true,canSend:true,group:'گروه آزمایشی',messenger:'روبیکا'}:{})}
                : staff ? {start:true}:null;
            if (input.action==='status') {
                Object.assign(result,closed?{phase:'idle',ready:true}:{phase:'live',lesson,epoch,ready:true,joinable:true,slot:11,
                    admitted:input.sid===`PA_${identity}`,recording:controls(), quality:{levels:{}}});
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
        const page=await browser.newPage({viewport:{width:540,height:700}});
        const errs=[];
        page.on('pageerror',error=>errs.push(error.message));
        await page.goto(`${origin}/${role}`);
        await page.locator('#toggle-access').click();
        const start=page.locator('#record-start');
        await start.waitFor({timeout:10000});
        assert.equal(await start.evaluate(el=>el.nextElementSibling.id),'quality-open','between board access and settings');
        assert.equal(await start.textContent().then(s=>s.trim()),'ضبط کلاس');
        await start.click();
        assert.match(await page.locator('#record-start-help').textContent(),/دبیر همین زنگ یا مدیر مجاز/);
        assert.equal(recording,false,'opening the dialog does not start capture');
        await page.locator('#record-start-yes').click();
        await page.locator('#recording-notice').waitFor();
        await pupil.locator('#recording-notice').waitFor({timeout:8500});
        assert.match(await pupil.locator('#recording-notice').textContent(),/کلاس در حال ضبط است/);
        assert.equal(await pupil.getByText('گروه آزمایشی').count(),0,'group is private to staff');
        await page.locator('#toggle-access').click();
        await page.getByRole('button',{name:'بستن کلاس برای همه'}).click();
        const dialog=page.getByRole('dialog',{name:'آیا فیلم ضبط شده از کلاس در پیامرسان ارسال شود ؟'});
        await dialog.waitFor();
        assert.match(await dialog.textContent(),/روبیکا — گروه آزمایشی/);
        const before=decisions.length;
        await dialog.getByRole('button',{name:'انصراف از بستن کلاس'}).click();
        assert.equal(decisions.length,before,'cancel does not close, grant or send');
        await page.locator('#toggle-access').click();
        await page.getByRole('button',{name:'بستن کلاس برای همه'}).click();
        await dialog.getByRole('button',{name:yes?'بله':'خیر',exact:true}).click();
        await page.locator('#recording-notice').waitFor({state:'hidden'});
        assert.deepEqual(decisions.at(-1),{action:'close',class:22,lesson,recording:rec,sendRecording:yes});
        assert.deepEqual(errs,[]);
        await page.close();
        recording=false; closed=false;
    }
});
