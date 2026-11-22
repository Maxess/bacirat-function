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
        const page=await browser.newPage({viewport:{width:540,height:700},reducedMotion:role==='admin'?'reduce':'no-preference'});
        const errs=[];
        page.on('pageerror',error=>errs.push(error.message));
        await page.goto(`${origin}/${role}`);
        await page.locator('#toggle-access').click();
        const start=page.locator('#record-start');
        await start.waitFor({timeout:10000});
        assert.equal(await start.evaluate(el=>el.nextElementSibling.id),'quality-open','between board access and settings');
        assert.equal(await start.textContent().then(s=>s.trim()),'ضبط کلاس');
        const header = await page.locator('.room-top').boundingBox();
        const pupilHeader = await pupil.locator('.room-top').boundingBox();
        await start.click();
        assert.match(await page.locator('#record-start-help').textContent(),/دبیر همین زنگ یا مدیر مجاز/);
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
        await page.locator('#classroom[data-recording=no]').waitFor();
        await pupil.locator('#classroom[data-recording=no]').waitFor({timeout:9000});
        assert.deepEqual(decisions.at(-1),{action:'close',class:22,lesson,recording:rec,sendRecording:yes});
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
