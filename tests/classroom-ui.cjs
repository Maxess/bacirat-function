const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../school-addon/upload/online-classroom/assets/classroom.js'), 'utf8');
const listener = {canPublish:false, canPublishSources:[]};
const mic = {canPublish:true,canPublishSources:[2]};
const camera = {canPublish:true,canPublishSources:[1]};
class Element {
    constructor(tag) { this.tag = tag; this.handlers = {}; this.children = []; this.dataset = {}; this.textContent = ''; this.value = ''; this.disabled = false; this.hidden = false; }
    get options() { return this.children; }
    addEventListener(k,v) { this.handlers[k] = v; }
    setAttribute(k,v) { this[k] = v; }
    append(...items) { for (const node of items) node.parentNode=this; this.children.push(...items); }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(node=>node!==this); }
    cloneNode() { return new Element(this.tag); }
    prepend(...items) { this.children.unshift(...items); }
    replaceChildren(...items) { this.children = []; this.append(...items); }
    querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag)]); }
    async click() { if (!this.disabled && !this.hidden) await this.handlers.click({}); }
}
function fixture(staff, autoSlot = 0) {
    const elements = Object.fromEntries(['classroom','status','status-dot','notice','people','live-panel','waiting','wait-title','wait-message',
        'camera','camera-strip','receive-video','screen-panel','screen-video','screen-label','audio-tracks','chat-log','chat-form','chat-text','leave','microphone','enable-audio','people-count',
        'toggle-people','toggle-chat','people-panel','chat-panel','chat-empty','room-date','lesson-start','lesson-start-time','speaker-strip','speakers',
        ...(staff ? ['screen-toggle','slot','open','close','staff-start','toggle-access','room-permissions','access-menu','camera-unavailable','permission-icons',
            ...['mic','camera','board'].flatMap(f=>[`all-${f}-give`,`all-${f}-cut`])] : ['join','waiting-attendance','waiting-attendance-time','live-attendance','live-attendance-time'])].map(id => [id,new Element(id)]));
    elements['speaker-strip'].hidden = elements.notice.hidden = true;
    elements['people-panel'].hidden = !staff;
    const documentEvents = {};
    if (staff) elements['permission-icons'].content = {querySelector:()=>new Element('svg')};
    const date='2026-11-18';
    const choice = (id, label) => ({id, label});
    elements.classroom.dataset = { moderator:staff?'yes':'no',api:'/fixture',csrf:'session',class:'22',bellDate:date,content:'/content?class=22' };
    const requests=[], scheduled=new Map(), boards=[];
    let timerSeq=0, now=0, lastRoom, cameraAttempts=0, boardConfig, aim, wakeChoice;
    const captures=[], publications=[], subscriptions=[], wakeStates=[];
    const id=staff?'teacher-1':'student-1';
    const srv={phase:'idle',day:date,dayLabel:'چهارشنبه ۱۴۰۵/۰۸/۲۷',lesson:'lesson-11',epoch:'epoch-A',ready:true, staffCanJoin:false,seconds:{'student-1':9,'student-2':21},
        hidden:false, chat:[], moving:false, cameraCodec:'vp8',access:{},profiles:{'student-1':{name:'آزمایشی',photo:false},'student-2':{name:'دوم',photo:false}}, schedule:{date, timed:false, choices:autoSlot ? [choice(autoSlot,'ریاضی')] : [choice(11,'ریاضی'),choice(12,'علوم')], next:null}};
    const person = (identity) => ({identity, name:identity, sid:`PA_${identity}`, permissions:listener, micCalls:[], isMicrophoneEnabled:false,
        isCameraEnabled:false, publications:new Map(), trackPublications:new Map(), getTrackPublication(source) { return this.publications.get(source); },
        async publishTrack(track, opts) {
            assert.ok(['camera','screen_share'].includes(opts.source)); assert.equal(opts.videoCodec,srv.cameraCodec);
            publications.push({track,opts}); track.source=opts.source;
            this.publications.set(opts.source,{track,isMuted:false}); if (opts.source==='camera') this.isCameraEnabled=true;
            lastRoom.emit('LocalTrackPublished');
        },
        async unpublishTrack(track) {
            assert.equal(track.stopped,true); this.publications.delete(track.source); if (track.source==='camera') this.isCameraEnabled=false;
            lastRoom.emit('LocalTrackUnpublished');
        },
        async setMicrophoneEnabled(value) {
            this.micCalls.push(value);
            if (this.error && value) throw this.error;
            this.isMicrophoneEnabled=value;
        }});
    class Room extends EventEmitter {
        constructor(options) {
            super(); this.options=options; lastRoom=this;
            this.localParticipant=person(id);
            this.remoteParticipants=new Map((staff?['student-1','student-2','admin-1']:['teacher-1','student-2']).map(n=>[n,person(n)]));
            this.canPlaybackAudio=true;
        }
        async connect(url,token,opts) { this.connectOptions=opts; this.state='connected'; this.emit('ConnectionStateChanged'); }
        async disconnect() { this.state='disconnected'; this.emit('Disconnected',1); }
        async startAudio() {}
    }
    const fixtureVideo = (source='camera') => ({kind:'video',source,stopped:false, mediaStreamTrack:{addEventListener(){}},
        attach(node) { this.element=node; }, detach(node) { if (this.element===node) this.element=null; }, stop() { this.stopped=true; }});
    const lk={Room,LocalVideoTrack:class { constructor(raw) { const track=fixtureVideo('screen_share'); track.mediaStreamTrack=raw; track.stop=()=>{ track.stopped=true; raw.stop(); }; return track; } },async createLocalVideoTrack() { cameraAttempts++; return srv.capture ? await srv.capture : fixtureVideo(); },ConnectionState:{Connected:'connected'},Track:{Kind:{Audio:'audio',Video:'video'},Source:{Camera:'camera',Microphone:'microphone',ScreenShare:'screen_share'}},DisconnectReason:{1:'CLIENT_INITIATED',2:'DUPLICATE_IDENTITY'},RoomEvent:new Proxy({}, {get:(_,key)=>key})};
    function change(identity, perm) {
        const p=identity===id?lastRoom.localParticipant:lastRoom.remoteParticipants.get(identity);
        const old=p.permissions; p.permissions=perm;
        lastRoom.emit('ParticipantPermissionsChanged',old,p);
    }
    vm.runInNewContext(script, {
        window:{isSecureContext:true,LivekitClient:lk,addEventListener(){},confirm:()=>true,
            navigator:{mediaDevices:{async getDisplayMedia(opts) {
                captures.push(opts); if (srv.screenPrompt) return srv.screenPrompt;
                const raw={readyState:'live', handlers:{}, addEventListener(k,fn){this.handlers[k]=fn;},stop(){this.readyState='ended';},
                    getSettings(){return {width:640,height:360,frameRate:2};}};
                return {getVideoTracks:()=>[raw],getTracks:()=>[raw]};
            }}},
            ClassroomAttention:choose=>{wakeChoice=choose;return {setLive(...args){wakeStates.push(args);},snapshot(){},expand(){}};},
            ClassroomBoard:(_,config)=>{boardConfig=config;return {position:()=>({board:0,ink:0}),takeAim:()=>{
                const value=aim; aim=null; return value ? {aim:value} : {};
            },sync:(...p)=>boards.push(p),reset:()=>boards.push('reset')};}},
        document:{getElementById:id=>elements[id],baseURI:'https://school.fixture.test/',createElement:tag=>new Element(tag),addEventListener(k,f){documentEvents[k]=f;},get hidden(){return srv.hidden;}},
        performance:{now:()=>now},
        setTimeout(fn,ms){const t=++timerSeq; scheduled.set(t,{fn,at:now+ms});return t;}, clearTimeout:t=>scheduled.delete(t), AbortController, URL,
        async fetch(url,opt){
            assert.equal(url,'/fixture'); assert.equal(opt.headers['X-Classroom-CSRF'],'session');
            const input=JSON.parse(opt.body); assert.equal(input.class,22); requests.push(input);
            let reply={ok:true}, status=200;
            if(input.action==='status'){
                if (srv.beforeStatus) await srv.beforeStatus();
                const phased = ['idle','reserved'].includes(srv.phase) ? {phase:srv.phase} : {phase:srv.phase,slot:srv.slot ?? 11, lesson:srv.lesson,epoch:srv.epoch,resumable:srv.resumable,startTime:srv.startTime ?? null,subject:'ریاضی',period:'زنگ یک'};
                reply={...reply,ready:srv.ready,day:srv.day,dayLabel:srv.dayLabel,...phased,joinable:srv.phase==='live'||(staff&&srv.phase==='starting'&&srv.staffCanJoin)};
                if (staff && srv.phase==='idle') reply.schedule=srv.schedule;
                if (!staff && srv.phase==='idle' && srv.practiceAvailable) reply.practiceAvailable=true;
                if (!staff && phased.lesson) reply.attendance=srv.attendance ?? srv.seconds[id] ?? 0;
                if(input.sid && srv.phase==='live' && input.epoch===srv.epoch) {
                    const profilesRev=JSON.stringify(srv.profiles);
                    if (input.people) reply.seconds=srv.seconds;
                    if (input.profilesRev!==profilesRev) reply.profiles=srv.profiles;
                    Object.assign(reply,{admitted:true,profilesRev,chat:srv.chat,board:{rev:1,moving:srv.moving,write:srv.write},
                        cameraCodec:srv.cameraCodec,screen:srv.screen || null,
                        wakeMode:srv.wakeMode || 'live',wakeControl:staff && srv.wakeControl !== false});
                    if (staff) Object.assign(reply,{screenEnabled:!!srv.screenEnabled,access:structuredClone(srv.access),members:Object.fromEntries([lastRoom.localParticipant,
                        ...lastRoom.remoteParticipants.values()].map(p=>[p.identity,p.sid]))});
                }
                if (srv.afterStatus) await srv.afterStatus();
            } else if (input.action==='board-sync') {
                if (srv.phase==='live' && input.epoch===srv.epoch) reply.board={rev:1,moving:srv.moving,write:srv.write};
                else { status=409; reply={ok:false}; }
            } else if(input.action==='open'||input.action==='ticket') {
                if (input.action === 'open' && srv.phase !== 'live') srv.phase = 'starting';
                Object.assign(reply,{url:'wss://media.example.test',token:'opaque',lesson:srv.lesson,epoch:srv.epoch});
            } else if(input.action==='set-access') {
                const {feature,identity,enabled}=input;
                if (identity) {
                    const rule=srv.access[feature] ??= {all:false,members:{}};
                    if(rule.all===enabled) delete rule.members[identity];
                    else rule.members[identity]=enabled;
                } else srv.access[feature]={all:enabled,members:{}};
                const allows=(f,n)=>srv.access[f]?.members[n] ?? srv.access[f]?.all ?? false;
                for(const p of [lastRoom.localParticipant,...lastRoom.remoteParticipants.values()]) {
                    const sources=[...(allows('camera',p.identity)?[1]:[]),...(allows('mic',p.identity)?[2]:[])];
                    change(p.identity,{canPublish:!!sources.length,canPublishSources:sources});
                }
                reply.access=structuredClone(srv.access);
            } else if(input.action==='set-wake') {
                if (!staff || srv.wakeControl === false) { status=403; reply={ok:false}; }
                else { srv.wakeMode=input.mode; reply.wakeMode=input.mode; }
            } else if(input.action==='screen-access') {
                const p=lastRoom.localParticipant;
                const src=p.permissions.canPublishSources.filter(x=>x!==3);
                if (input.enabled) { srv.screen={identity:p.identity,sid:p.sid}; src.push(3); }
                else srv.screen=null;
                change(p.identity,{canPublish:!!src.length,canPublishSources:src});
                reply.screen=srv.screen;
            } else if(input.action==='close') {
                srv.phase='idle'; srv.schedule={...srv.schedule, choices:srv.schedule.choices.filter(n=>n.id!==11)};
            }
            return {ok:status===200,status,json:async()=>reply};
        },
    });
    async function time() {
        const next=[...scheduled.entries()].sort((a,b)=>a[1].at-b[1].at)[0];
        if(next){scheduled.delete(next[0]);now=next[1].at;await next[1].fn();}
        await new Promise(resolve=>setImmediate(resolve));
    }
    async function status() {
        const before = requests.filter(r=>r.action==='status').length;
        for (let i=0;i<12 && requests.filter(r=>r.action==='status').length===before;i++) await time();
        assert.ok(requests.filter(r=>r.action==='status').length>before, 'status heartbeat is not starved');
    }
    async function advance(ms) {
        const target=now+ms;
        while ([...scheduled.values()].some(task=>task.at<=target)) await time();
        now=target;
    }
    function controls(identity){return elements.people.children.find(row=>row.dataset.identity===identity).querySelectorAll('button');}
    function remoteTrack(person, source, track, kind = 'video') {
        const trackSid = `${person.sid}-${source}`;
        const pub = {kind,source,trackSid,isDesired:false,setSubscribed(value){
            subscriptions.push([trackSid,value]); this.isDesired=value;
            this.track=value ? track : null;
            lastRoom.emit(value?'TrackSubscribed':'TrackUnsubscribed',track);
        }};
        person.publications.set(source,pub); person.trackPublications.set(trackSid,pub);
        lastRoom.emit('TrackPublished',pub,person);
        return pub;
    }
    function showVideo(value) { const target=elements['receive-video']; target.value=value; target.handlers.change({target}); }
    function visibility(value) { srv.hidden=value; documentEvents.visibilitychange(); }
    return {elements,srv,requests,boards,captures,publications,subscriptions,showVideo,visibility,remoteTrack,wakeStates,chooseWake:mode=>wakeChoice(mode),time,status,change,controls,fixtureVideo,advance,aim(point){aim=point;boardConfig.aimSoon();},get cameraAttempts(){return cameraAttempts;},get room(){return lastRoom;}};
}

test('student waits without media, auto enters and sees attendance only for pupils, then waits again',async()=>{
    const f=fixture(false); await f.time();
    assert.equal(f.room,undefined); assert.equal(f.elements['live-panel'].hidden,true);
    assert.equal(f.requests.some(r=>r.action==='ticket'),false);
    f.srv.phase='live'; await f.time(); await f.time();
    assert.equal(f.room.options.singlePeerConnection,false); assert.equal(f.room.connectOptions.autoSubscribe,false);
    assert.equal(f.elements['live-panel'].hidden,false);
    assert.equal(f.elements['live-attendance-time'].textContent,'۰۰:۰۰');
    assert.equal(f.elements['people-panel'].hidden,true);
    assert.deepEqual(f.elements.people.children,[]);
    await f.elements['toggle-people'].click(); await f.status();
    assert.deepEqual(f.elements.people.querySelectorAll('bdi').map(n=>n.textContent),['۰۰:۰۰','۰۰:۰۰']);
    assert.equal(f.elements.people.children[0].className,'person-self');
    const [,detail,self] = f.elements.people.children[0].children[0].children;
    assert.deepEqual(detail.children.map(n=>n.textContent), ['آزمایشی','۰۰:۰۰']);
    assert.equal(self.textContent,'شما');
    assert.ok(f.elements.people.children.slice(1).every(n=>n.className!=='person-self'));
    assert.deepEqual(f.room.localParticipant.micCalls,[]); assert.equal(f.elements.microphone.disabled,true);
    f.change('student-1',mic); assert.deepEqual(f.room.localParticipant.micCalls,[]);
    await f.elements.microphone.click(); assert.equal(f.room.localParticipant.isMicrophoneEnabled,true);
    const first=f.room; f.srv.phase='paused'; await f.status();
    assert.equal(f.elements['live-panel'].hidden,true); assert.equal(first.state,'disconnected'); assert.equal(f.elements.microphone.disabled,true);
    assert.ok(f.boards.includes('reset'));
    assert.equal(f.elements['waiting-attendance-time'].textContent, '۰۰:۰۰');
    assert.equal(f.elements['waiting-attendance'].hidden,false);
    f.srv.epoch='epoch-B'; f.srv.phase='live'; await f.time(); await f.time();
    assert.notEqual(first,f.room); assert.equal(f.elements['live-panel'].hidden,false);
    assert.equal(f.elements.microphone.disabled,true); assert.deepEqual(f.room.localParticipant.micCalls,[]);
});

test('a moderator uses icon-only individual toggles and one menu for class permissions',async()=>{
    const f=fixture(true); await f.time(); assert.equal(f.elements.open.disabled,true);
    f.elements.slot.value='11'; f.elements.slot.handlers.change(); await f.elements.open.click();
    assert.equal(f.requests.find(r=>r.action==='open').slot,11);
    await f.time();
    assert.equal(f.requests.some(r=>r.action==='ticket'),false);
    f.srv.staffCanJoin=true; await f.time();
    assert.deepEqual(f.room.localParticipant.micCalls,[]);
    f.srv.phase='live'; await f.time();
    await f.elements['toggle-access'].click();
    assert.equal(f.elements['access-menu'].hidden,false);
    const toggles = f.controls('student-1');
    const [micIcon,camIcon,penIcon] = toggles;
    assert.deepEqual(toggles.map(b=>[b.dataset.feature,b.textContent,b['aria-pressed']]),[['mic','','false'],['camera','','false'],['board','','false']]);
    await micIcon.click(); assert.equal(micIcon.dataset.granted,'true');
    assert.equal(micIcon.dataset.on,'false');
    const pupil=f.room.remoteParticipants.get('student-1');
    pupil.isMicrophoneEnabled=true; f.room.emit('TrackUnmuted',{},pupil);
    assert.equal(micIcon.dataset.on,'true');
    await camIcon.click(); await penIcon.click();
    assert.deepEqual(pupil.permissions.canPublishSources,[1,2]);
    assert.equal(penIcon['aria-pressed'],'true');
    await f.elements['all-camera-give'].click();
    assert.deepEqual(f.room.localParticipant.permissions.canPublishSources,[1]);
    assert.equal(f.room.localParticipant.isCameraEnabled,false);
    assert.equal(f.cameraAttempts,0);
    await f.elements['all-camera-cut'].click();
    assert.deepEqual(pupil.permissions.canPublishSources,[2]);
    await f.elements['all-mic-cut'].click();
    assert.deepEqual(pupil.permissions.canPublishSources,[]);
    assert.equal(f.elements.notice.hidden,true,'all permission changes are quiet');
    assert.ok(f.requests.filter(r=>r.action==='set-access').every(r=>r.sid==='PA_teacher-1' && r.lesson===f.srv.lesson));
    await f.elements.close.click(); await f.time();
    assert.equal(f.requests.filter(r=>r.action==='open').length,1); assert.equal(f.elements['live-panel'].hidden,true);
});

test('microphone access failure is shown without exposing browser diagnostics',async()=>{
    const f=fixture(false); f.srv.phase='live'; await f.time(); await f.time();
    f.change('student-1',mic);
    f.room.localParticipant.error=Object.assign(new Error('sensitive-device-detail'),{name:'NotAllowedError'});
    await f.elements.microphone.click();
    assert.match(f.elements.notice.textContent,/MIC-ACCESS/);
    assert.doesNotMatch(f.elements.notice.textContent,/sensitive-device-detail/);
    await f.advance(4100);
    assert.equal(f.elements.notice.hidden,true); assert.equal(f.elements.notice.textContent,'');
});


test('on entry staff auto-opens the unmistakable bell, and never auto-opens the next one', async()=>{
    const f=fixture(true, 11);
    await f.time(); await f.time();
    assert.equal(f.requests.filter(r=>r.action==='open').length,1);
    assert.equal(f.requests.find(r=>r.action==='open').slot,11);
    f.srv.staffCanJoin=true;
    await f.time(); await f.time();
    f.srv.phase='live'; await f.time();
    assert.equal(f.elements['live-panel'].hidden,false);
    f.srv.phase='idle'; await f.time(); await f.time();
    assert.equal(f.requests.filter(r=>r.action==='open').length,1);
});


test('server clock selects the timed bell without a reload; waiting and the next bell do not auto-start another room', async()=>{
    const f=fixture(true);
    f.srv.schedule={...f.srv.schedule,timed:true,choices:[],next:'زنگ ۱ · ریاضی · 08:30 تا 09:15'};
    await f.time(); await f.time();
    assert.ok(f.elements.open.disabled);
    assert.match(f.elements['wait-message'].textContent,/۰۸:۳۰ تا ۰۹:۱۵/);
    assert.equal(f.requests.filter(r=>r.action==='open').length,0);
    f.srv.hidden=true;
    f.srv.schedule={...f.srv.schedule, choices:[{id:11,label:'زنگ ۱ · ریاضی · 08:30 تا 09:15'}],next:null};
    await f.time();
    assert.equal(f.requests.filter(r=>r.action==='open').length,0, 'a hidden tab does not start the next bell');
    f.srv.hidden=false;
    await f.time(); await f.time();
    assert.deepEqual(f.requests.filter(r=>r.action==='open').map(r=>r.slot),[11]);
    f.srv.phase='live'; await f.time(); await f.time();
    const room=f.room;
    f.srv.phase='idle'; f.srv.schedule={...f.srv.schedule,choices:[{id:12,label:'زنگ ۲ · علوم · 09:15 تا 10:00'}]};
    await f.time(); await f.time();
    assert.equal(room.state,'disconnected');
    assert.equal(f.elements['live-panel'].hidden,true);
    assert.deepEqual(f.elements.slot.options.map(n=>n.value),['','12']);
    assert.deepEqual(f.requests.filter(r=>r.action==='open').map(r=>r.slot),[11]);
    assert.equal(f.elements.open.disabled,false, 'staff can explicitly start the newly available period');
});

test('a shared weekly bell requires a manual start even when only one teacher option is visible', async()=>{
    const f=fixture(true);
    const shared={id:24,label:'زنگ ۱ · ریاضی · تک‌زنگ',manual:true};
    f.srv.schedule={...f.srv.schedule,timed:true,choices:[shared]};
    await f.status(); await f.status();
    assert.deepEqual(f.requests.filter(r=>r.action==='open'),[]);
    assert.equal(f.elements.slot.value,'24');
    assert.match(f.elements['wait-message'].textContent,/درسِ این هفته/);
    await f.elements.open.click();
    assert.equal(f.requests.find(r=>r.action==='open').slot,24);

    const admin=fixture(true);
    admin.srv.schedule={...admin.srv.schedule,timed:true,choices:[shared,{id:25,label:'زنگ ۱ · علوم · تک‌زنگ',manual:true}]};
    await admin.status();
    assert.equal(admin.elements.slot.value,'');
    assert.ok(admin.elements.open.disabled);
    admin.srv.schedule={...admin.srv.schedule,choices:[{id:26,label:'زنگ ۲'}]};
    await admin.status(); await admin.status();
    assert.deepEqual(admin.requests.filter(r=>r.action==='open'),[], 'waiting on a shared bell does not auto-open a later one');
});

test('a waiting tab left open overnight does not auto-start the following day', async()=>{
    const f=fixture(true);
    f.srv.schedule={...f.srv.schedule,timed:true,choices:[],next:null};
    await f.time();
    f.srv.schedule={...f.srv.schedule,date:'2026-11-19',choices:[{id:13,label:'زنگ ۱'}]};
    await f.time(); await f.time();
    assert.equal(f.elements.slot.value,'13');
    assert.equal(f.requests.filter(r=>r.action==='open').length,0);
});


test('Friday test requires a click on both idle and running rooms and has no report message', async()=>{
    const slot='friday-test';
    const f=fixture(true,slot);
    f.srv.slot=slot;
    await f.time(); await f.time();
    assert.equal(f.requests.filter(r=>r.action==='open').length,0);
    assert.equal(f.elements.slot.value,slot);
    assert.match(f.elements.open.textContent,/آزمایش جمعه/);
    await f.elements.open.click();
    assert.equal(f.requests.find(r=>r.action==='open').slot,slot);
    f.srv.phase='live'; await f.time();
    assert.equal(f.elements['live-panel'].hidden,false);
    await f.elements.close.click(); await f.time();
    assert.match(f.elements.notice.textContent,/گزارشی ثبت نشد/);
    assert.equal(f.requests.filter(r=>r.action==='open').length,1);

    const arriving=fixture(true,slot); arriving.srv.slot=slot; arriving.srv.phase='live';
    await arriving.time(); await arriving.time();
    assert.equal(arriving.room,undefined);
    assert.equal(arriving.requests.filter(r=>r.action==='ticket').length,0);
    await arriving.elements.open.click();
    assert.equal(arriving.requests.find(r=>r.action==='open').slot,slot);
});


test('opted-in pupil waits for the teacher, sees a server-timed demo, and returns to waiting without adding time', async()=>{
    const f = fixture(false);
    f.srv.slot='friday-test'; f.srv.attendance=0; f.srv.practiceAvailable=true;
    await f.time(); await f.time();
    assert.equal(f.elements['waiting-attendance'].hidden,true);
    assert.equal(f.elements['lesson-start'].hidden,true);
    assert.match(f.elements['wait-message'].textContent,/آزمایش جمعه/);
    f.srv.phase='starting'; await f.time();
    assert.equal(f.requests.some(r=>r.action==='ticket'),false);
    f.srv.phase='live'; f.srv.startTime='08:31'; f.srv.seconds['student-1']=f.srv.attendance=75;
    await f.time(); await f.time();
    const selfTime=()=>f.elements['live-attendance-time'].textContent;
    await f.elements['toggle-people'].click(); await f.status();
    assert.equal(selfTime(),'۰۰:۰۱');
    assert.equal(f.elements['lesson-start-time'].textContent,'۰۸:۳۱');
    assert.equal(f.elements['lesson-start'].hidden,false);
    assert.equal(f.elements['waiting-attendance'].hidden,true);
    assert.equal(f.elements['live-panel'].hidden,false);
    assert.equal(f.elements['people-count'].textContent,'۳');
    await f.elements['toggle-chat'].click();
    assert.equal(f.elements['chat-panel'].hidden,true);
    await f.elements['toggle-chat'].click();
    assert.equal(f.elements['chat-panel'].hidden,false);
    f.srv.phase='paused'; await f.status();
    assert.equal(f.elements['waiting-attendance-time'].textContent,'۰۰:۰۱');
    assert.equal(f.elements['waiting-attendance'].hidden,false);
    assert.equal(f.elements['waiting'].hidden,false);
    assert.equal(f.elements['toggle-chat'].disabled,true);
    f.srv.phase='live'; f.srv.epoch='epoch-B'; f.srv.seconds['student-1']=f.srv.attendance=138;
    await f.time(); await f.time();
    assert.equal(selfTime(),'۰۰:۰۲');
    assert.equal(f.elements['lesson-start-time'].textContent,'۰۸:۳۱');
    assert.equal(f.requests.filter(r=>r.action==='ticket').length,2);
});


test('chat prepends new batches in newest-first order and ignores repeats', async()=>{
    const f=fixture(false); f.srv.phase='live';
    f.srv.chat=[{n:1,by:'Teacher',text:'اول'},{n:2,by:'Student',text:'دوم'}];
    await f.time(); await f.time();
    const entries=()=>f.elements['chat-log'].children.map(n=>n.children[1].textContent);
    assert.deepEqual(entries(), ['دوم','اول']);
    f.srv.chat.push({n:3,by:'Teacher',text:'سوم'});
    await f.status(); await f.status();
    assert.deepEqual(entries(), ['سوم','دوم','اول']);
    assert.equal(f.requests.filter(r=>r.action==='status').at(-1).chat,3);
});

test('board reads speed up while writing without renewing the full heartbeat or reading while hidden', async()=>{
    const f=fixture(false); f.srv.phase='live'; f.srv.moving=true;
    await f.time(); await f.time();
    const before=f.requests.length;
    await f.time(); await f.time(); await f.time();
    assert.deepEqual(f.requests.slice(before).map(r=>r.action), ['board-sync','board-sync','board-sync']);
    assert.ok(f.requests.slice(before).every(r=>r.sid==='PA_student-1' && !Object.hasOwn(r,'chat')));
    f.srv.hidden=true;
    await f.time(); await f.time();
    assert.deepEqual(f.requests.slice(-2).map(r=>r.action), ['status','status']);
    await f.elements.leave.click();
    assert.equal(f.room.state,'disconnected');
    assert.equal(f.requests.at(-1).action,'leave');
    assert.deepEqual(f.requests.filter(r=>r.action==='close'), []);
});


test('lesson wake policy is read by pupils and only changed through a confirmed, bound teacher request',async()=>{
    const pupil=fixture(false); pupil.srv.phase='live'; await pupil.time(); await pupil.time();
    assert.deepEqual(pupil.wakeStates.at(-1),[true,'live',false,false]);
    pupil.srv.wakeMode='system'; await pupil.status();
    assert.deepEqual(pupil.wakeStates.at(-1),[true,'system',false,false]);
    await pupil.chooseWake('live'); assert.equal(pupil.requests.some(r=>r.action==='set-wake'),false);
    pupil.srv.phase='paused'; await pupil.status(); assert.equal(pupil.wakeStates.at(-1)[0],false);
    const teacher=fixture(true); teacher.srv.phase='live'; await teacher.time(); await teacher.time();
    assert.deepEqual(teacher.wakeStates.at(-1),[true,'live',true,false]);
    await teacher.chooseWake('idle');
    assert.deepEqual(teacher.requests.find(r=>r.action==='set-wake'),{
        action:'set-wake',class:22,lesson:'lesson-11',epoch:'epoch-A',sid:'PA_teacher-1',mode:'idle',
    });
    assert.deepEqual(teacher.wakeStates.at(-1),[true,'idle',true,false]);
    teacher.srv.wakeControl=false; await teacher.status(); await teacher.chooseWake('live');
    assert.equal(teacher.requests.filter(r=>r.action==='set-wake').length,1);
});

test('a status response captured before the teacher save cannot restore an older wake mode',async()=>{
    const f=fixture(true); f.srv.phase='live'; await f.time(); await f.time();
    let release;
    const started=new Promise(resolve=>{
        f.srv.afterStatus=()=>new Promise(done=>{ release=done; resolve(); });
    });
    const old=f.status(); await started;
    f.srv.afterStatus=null;
    await f.chooseWake('system');
    release(); await old;
    assert.deepEqual(f.wakeStates.at(-1),[true,'system',true,false]);
});

test('a laser trail queued during a status read rides the next board-only request and does not create an idle stream',async()=>{
    const f=fixture(true); f.srv.phase='live'; await f.time(); await f.time();
    let calls=0;
    f.srv.beforeStatus=()=>{ calls++; if(calls===1){f.aim([[111,222,0]]); f.aim([[333,444,0]]);} };
    await f.status();
    assert.equal(f.requests.filter(r=>r.aim).length,0,'status requests are not laser mutations');
    const before=f.requests.length;
    await f.advance(450);
    assert.deepEqual(f.requests.slice(before).map(r=>[r.action,r.aim]), [['board-sync',[[333,444,0]]]]);
    await f.advance(400);
    assert.equal(f.requests.length,before+1,'no continual fast polls for an idle point');
});


test('only speaking participants appear, including multiple speakers; silence, mute and teardown clear them', async()=>{
    const f=fixture(false); f.srv.phase='live'; await f.time(); await f.time();
    const room=f.room, local=room.localParticipant, peer=room.remoteParticipants.get('teacher-1');
    const strip=f.elements['speaker-strip'];
    const names=()=>f.elements.speakers.children.flatMap(row=>row.children.map(node=>node.textContent));
    f.change(local.identity,mic);
    await f.elements.microphone.click();
    assert.equal(strip.hidden,true,'opening the mic with permission is not speech');
    const activity=(...people)=>room.emit('ActiveSpeakersChanged',people);
    Object.assign(local,{isSpeaking:true,audioLevel:0});
    activity(local);
    assert.equal(strip.hidden,true,'speaking flag with zero audio level is not enough');
    Object.assign(local,{isSpeaking:true,audioLevel:.5});
    Object.assign(peer,{isSpeaking:true,audioLevel:.2,isMicrophoneEnabled:true,name:'دبیر نمونه'});
    activity(peer,local);
    assert.equal(strip.hidden,false);
    assert.deepEqual(names(),['آزمایشی','دبیر نمونه']);
    activity(local,peer);
    assert.deepEqual(names(),['آزمایشی','دبیر نمونه'],'volume order does not reorder labels');
    peer.isMicrophoneEnabled=false;
    room.emit('TrackMuted',{},peer);
    assert.deepEqual(names(),['آزمایشی']);
    peer.isMicrophoneEnabled=true;
    room.emit('TrackUnmuted',{},peer);
    assert.deepEqual(names(),['آزمایشی'],'unmute without a new activity event cannot restore a speaker');
    activity();
    assert.equal(strip.hidden,true);
    activity(local,peer);
    await f.elements.microphone.click();
    assert.deepEqual(names(),['دبیر نمونه']);
    room.remoteParticipants.delete(peer.identity);
    room.emit('ParticipantDisconnected',peer);
    assert.equal(strip.hidden,true);
    await f.elements.microphone.click();
    activity(local);
    assert.equal(strip.hidden,false);
    f.srv.phase='paused'; await f.status();
    assert.equal(strip.hidden,true);
    f.srv.phase='live'; f.srv.epoch='epoch-B'; await f.time(); await f.time();
    assert.notEqual(room,f.room);
    activity(local,peer);
    assert.equal(strip.hidden,true,'old-room events cannot restore speakers');
    assert.deepEqual(names(),[]);
});


test('render the school photo and shortened roster label without raw profile URL or full name',async()=>{
    const f=fixture(false); f.srv.phase='live';
    f.srv.profiles={'student-1':{name:'ح\u200cاحسانی',photo:true},'student-2':{name:'ع\u200cاحسانی',photo:false}};
    await f.time(); await f.time();
    await f.elements['toggle-people'].click(); await f.status();
    const [avatar,detail] = f.elements.people.children[0].children[0].children;
    const [image] = avatar.children;
    assert.equal(detail.children[0].textContent,'ح‌احسانی');
    assert.equal(avatar.textContent,'ح');
    const url=new URL(image.src);
    assert.equal(url.origin,'https://school.fixture.test');
    assert.deepEqual(Object.fromEntries(url.searchParams),{class:'22', lesson:'lesson-11',epoch:'epoch-A',sid:'PA_student-1',photo:'student-1'});
    f.change('student-1',mic); assert.equal(f.elements.people.querySelectorAll('img')[0],image,'mic changes do not reload the photo');
    image.handlers.error(); assert.equal(avatar.children.length,0); assert.equal(avatar.textContent,'ح');
});

test('camera grants never start capture, only camera is displayed and revoke ends capture without stopping the mic',async()=>{
    const f=fixture(false); f.srv.phase='live'; await f.time(); await f.time();
    assert.equal(f.elements.camera.hidden,true);
    const local=f.room.localParticipant;
    f.change(local.identity,{canPublish:true,canPublishSources:[1,2]});
    assert.equal(f.elements.camera.hidden,false); assert.equal(f.cameraAttempts,0);
    await f.elements.microphone.click(); await f.elements.camera.click();
    assert.equal(f.cameraAttempts,1); assert.equal(f.elements.camera['aria-pressed'],'true');
    const track=local.getTrackPublication('camera').track;
    assert.equal(track.element.muted,true); assert.equal(track.element.playsInline,true);
    const peer=f.room.remoteParticipants.get('teacher-1');
    f.change(peer.identity,camera);
    assert.equal(f.publications[0].opts.videoEncoding.maxBitrate,85000);
    assert.equal(f.publications[0].opts.videoEncoding.maxFramerate,8);
    const screen=f.remoteTrack(peer,'screen_share',f.fixtureVideo('screen_share'));
    const remote=f.remoteTrack(peer,'camera',f.fixtureVideo());
    assert.equal(f.elements['camera-strip'].children.length,1);
    assert.deepEqual(f.subscriptions,[], 'published video is never requested by default');
    f.showVideo(screen.trackSid);
    assert.deepEqual(f.subscriptions,[], 'unleased screen never offered');
    f.showVideo(remote.trackSid);
    assert.equal(f.elements['camera-strip'].children.length,2);
    f.change(peer.identity,listener);
    assert.equal(f.elements['camera-strip'].children.length,1);
    assert.deepEqual(f.subscriptions,[[remote.trackSid,true],[remote.trackSid,false]]);
    f.change(local.identity,mic);
    assert.equal(track.stopped,true); assert.equal(f.elements['camera-strip'].hidden,true);
    assert.equal(local.isMicrophoneEnabled,true); assert.equal(f.elements.camera.hidden,true); assert.equal(f.elements.notice.hidden,true);
});

test('a camera prompt resolved after revocation is stopped and reconnection never enables capture',async()=>{
    const f=fixture(false); f.srv.phase='live'; await f.time(); await f.time();
    f.change('student-1',camera);
    let finish;
    f.srv.capture=new Promise(resolve=>{finish=resolve;});
    const click=f.elements.camera.click();
    const track=f.fixtureVideo();
    f.change('student-1',listener); finish(track); await click;
    assert.equal(track.stopped,true); assert.equal(f.room.localParticipant.isCameraEnabled,false);
    f.srv.phase='paused'; await f.status();
    f.srv.phase='live'; f.srv.epoch='epoch-B'; await f.time(); await f.time();
    f.change('student-1',camera);
    assert.equal(f.cameraAttempts,1); assert.equal(f.elements.camera['aria-pressed'],'false');
});


test('the pilot screen needs two explicit actions, never asks for system audio, and stopping preserves the mic',async()=>{
    const f=fixture(true); f.srv.phase='live'; await f.time(); await f.time();
    assert.equal(f.room.options.publishDefaults.audioPreset.maxBitrate,12000);
    assert.equal(f.room.options.publishDefaults.dtx,true);
    assert.equal(f.room.options.publishDefaults.forceStereo,false);
    const button=f.elements['screen-toggle']; assert.equal(button.hidden,true);
    f.srv.screenEnabled=true; await f.status(); assert.equal(button.hidden,false);
    f.change('teacher-1',mic); await f.elements.microphone.click();
    await button.click(); assert.equal(button.dataset.prepared,'true'); assert.equal(f.captures.length,0);
    await button.click();
    assert.equal(f.captures.length,1); assert.equal(f.captures[0].audio,false);
    assert.equal(f.captures[0].video.frameRate.max,2);
    assert.equal(f.captures[0].video.width.max,640);
    assert.equal(f.captures[0].video.height.max,360);
    const {track,opts}=f.publications.at(-1);
    assert.equal(opts.source,'screen_share'); assert.equal(opts.screenShareEncoding.maxBitrate,70000);
    assert.equal(opts.screenShareEncoding.maxFramerate,2);
    assert.equal(opts.simulcast,false); assert.equal(opts.backupCodec,false);
    assert.equal(track.mediaStreamTrack.contentHint,'detail');
    assert.equal(f.elements['screen-panel'].hidden,false);
    await button.click();
    assert.equal(track.mediaStreamTrack.readyState,'ended'); assert.equal(f.elements['screen-panel'].hidden,true);
    assert.equal(f.room.localParticipant.isMicrophoneEnabled,true);
    assert.deepEqual(f.room.localParticipant.permissions.canPublishSources,[2]);
});

test('an open picker cannot start screen capture in a newer transport',async()=>{
    const f=fixture(true); f.srv.phase='live'; f.srv.screenEnabled=true; await f.time(); await f.time();
    await f.elements['screen-toggle'].click();
    let choose;
    f.srv.screenPrompt=new Promise(resolve=>{ choose=resolve; });
    const click=f.elements['screen-toggle'].click();
    f.srv.phase='paused'; await f.status();
    let stopped=false;
    const raw={stop(){stopped=true;}};
    choose({getVideoTracks:()=>[raw],getTracks:()=>[raw]}); await click;
    assert.equal(stopped,true); assert.equal(f.publications.length,0);
});


test('only one remote picture is selected, never screen sound; microphone works before and through switches', async()=>{
    const f=fixture(false); f.srv.phase='live'; await f.time(); await f.time();
    const person=f.room.remoteParticipants.get('teacher-1');
    f.change(person.identity,{canPublish:true,canPublishSources:[1,2,3]});
    const audio={kind:'audio',source:'microphone',attach:()=>new Element('audio'),detach(){return []}};
    const micPub=f.remoteTrack(person,'microphone',audio,'audio');
    const screenAudio=f.remoteTrack(person,'screen_share_audio',audio,'audio');
    const camera=f.remoteTrack(person,'camera',f.fixtureVideo());
    const screen=f.remoteTrack(person,'screen_share',f.fixtureVideo('screen_share'));
    f.srv.screen={identity:person.identity,sid:person.sid}; await f.status();
    assert.equal(f.elements['audio-tracks'].children.length,1);
    assert.deepEqual(f.subscriptions,[[micPub.trackSid,true]]);
    f.showVideo(camera.trackSid); f.showVideo(screen.trackSid);
    assert.deepEqual(f.subscriptions.slice(1),[[camera.trackSid,true],[camera.trackSid,false],[screen.trackSid,true]]);
    assert.equal(f.elements['screen-panel'].hidden,false);
    assert.equal(f.elements['camera-strip'].hidden,true);
    f.visibility(true); assert.equal(screen.isDesired,false); assert.equal(micPub.isDesired,true);
    f.visibility(false); assert.equal(screen.isDesired,true);
    f.srv.screen={identity:person.identity,sid:'PA_other'}; await f.status();
    assert.equal(screen.isDesired,false); assert.equal(f.elements['receive-video'].value,'');
    assert.equal(screenAudio.isDesired,false); assert.equal(micPub.isDesired,true);
});

test('pupil status uses profile validation and does not request everybody’s timer unless roster is open',async()=>{
    const f=fixture(false); f.srv.phase='live'; await f.time(); await f.time();
    const status=()=>f.requests.filter(r=>r.action==='status').at(-1);
    await f.status();
    assert.equal(status().compact,true); assert.equal(status().people,false); assert.ok(status().profilesRev);
    await f.elements['toggle-people'].click(); await f.status();
    assert.equal(status().people,true);
    assert.equal(f.elements.people.children[0].children[0].children[1].children[0].textContent,'آزمایشی');
    f.srv.profiles={'student-1':{name:'نام تازه',photo:false}}; await f.status();
    assert.equal(f.elements.people.children[0].children[0].children[1].children[0].textContent,'نام تازه');
    await f.elements['toggle-people'].click(); await f.status();
    assert.equal(status().people,false);
    assert.deepEqual(f.elements.people.children,[]);
});


test('the server supplies the visible Shamsi day; midnight does not authorize an old tab to start', async()=>{
    const f=fixture(true);
    const date=f.elements['room-date'];
    await f.time();
    assert.equal(date.textContent,'چهارشنبه ۱۴۰۵/۰۸/۲۷');
    assert.equal(date.dateTime,'2026-11-18');
    f.srv.day='2026-11-19';
    f.srv.dayLabel='پنجشنبه ۱۴۰۵/۰۸/۲۸';
    f.srv.schedule={...f.srv.schedule,date:f.srv.day,choices:[{id:11,label:'زنگ ۱'}]};
    await f.status(); await f.status();
    assert.equal(date.textContent,f.srv.dayLabel);
    assert.equal(date.dateTime,f.srv.day);
    assert.equal(f.elements.classroom.dataset.bellDate,'2026-11-18');
    assert.equal(f.requests.some(r=>r.action==='open'),false);
});
