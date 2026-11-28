// The room UI can run with a fake DOM/LiveKit on either virtual time (unit tests)
// or a loopback PHP endpoint (load rehearsal). Production JS drives all reads.
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const script = readFileSync(join(__dirname,'../../school-addon/upload/online-classroom/assets/classroom.js'),'utf8');
const listener = {canPublish:false, canPublishSources:[]};
class Element {
    constructor(tag) { this.tag = tag; this.handlers = {}; this.children = []; this.dataset = {}; this.textContent = ''; this.value = ''; this.disabled = false; this.hidden = false; }
    get options() { return this.children; }
    addEventListener(k,v) { this.handlers[k] = v; }
    setAttribute(k,v) { this[k] = v; }
    append(...items) { for (const node of items) node.parentNode=this; this.children.push(...items); }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(node=>node!==this); }
    cloneNode() { return new Element(this.tag); }
    focus() { this.focused=true; }
    showModal() { this.open=true; }
    close() { this.open=false; this.handlers.close?.(); }
    contains(node) { return this === node || this.children.some(child=>child.contains(node)); }
    prepend(...items) { this.children.unshift(...items); }
    replaceChildren(...items) { this.children = []; this.append(...items); }
    querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag)]); }
    async click() { if (!this.disabled && !this.hidden) await this.handlers.click({}); }
}
function fixture(staff, autoSlot = 0, options = {}) {
    const {realTime, http, onPublish, onBoard, classroomScript=script, classId=22} = options;
    const clock = () => realTime ? performance.now() : now;
    const elements = Object.fromEntries(['classroom','status','status-dot','notice','people','live-panel','waiting','wait-title','wait-message',
        'camera','screen-toggle','camera-strip','camera-preview','camera-preview-video','camera-preview-name','screen-panel','screen-video','screen-label','audio-tracks','chat-log','chat-form','chat-text','leave','microphone','enable-audio','people-count',
        'toggle-people','toggle-chat','people-panel','chat-panel','chat-empty','lesson-start','lesson-start-time','speaker-strip','speakers','recording-status',
        ...(staff ? ['slot','open','close','close-label','staff-start','toggle-access','room-permissions','access-menu','access-features','access-camera','camera-unavailable','pupil-screen-hint','permission-icons',
            'quality-open','quality-dialog','quality-form','quality-message','quality-save','quality-cancel',
            'record-start','record-start-dialog','record-start-yes','record-start-cancel',
            'record-close-dialog','record-close-help','record-close-yes','record-close-no','record-close-cancel',
            ...['teacherCamera','studentCamera','teacherScreen','studentScreen'].map(k=>`quality-${k}`),
            ...['mic','camera','board'].flatMap(f=>[`all-${f}-give`,`all-${f}-cut`])] : ['join'])].map(id => [id,new Element(id)]));
    elements['speaker-strip'].hidden = elements.notice.hidden = true;
    elements['people-panel'].hidden = !staff;
    const documentEvents = {};
    if (staff) {
        elements['access-menu'].hidden=true;
        elements['permission-icons'].content = {querySelector:()=>new Element('svg')};
    }
    const date='2026-11-18';
    const choice = (id, label) => ({id, label});
    elements.classroom.dataset = { moderator:staff?'yes':'no',api:'/fixture',csrf:'session',class:String(classId),bellDate:date,content:'/content?class=22' };
    const requests=[], scheduled=new Map(), boards=[];
    let timerSeq=0, now=0, lastRoom, cameraAttempts=0, boardConfig, aim, wakeChoice;
    const captures=[], cameraCaptures=[], crops=[], inputs=[], publications=[], subscriptions=[], wakeStates=[], cuePackets=[], cues=[];
    let lastBoard=null, fastLaserAt=-Infinity;
    const id=options.identity ?? (staff?'teacher-1':'student-1');
    const srv={phase:'idle',lesson:'lesson-11',epoch:'epoch-A',ready:true, staffCanJoin:false,seconds:{'student-1':9,'student-2':21},
        hidden:false, chat:[], moving:false, cameraCodec:'vp8',access:{},profiles:{'student-1':{name:'آزمایشی',photo:false},'student-2':{name:'دوم',photo:false}}, schedule:{date, timed:false, choices:autoSlot ? [choice(autoSlot,'ریاضی')] : [choice(11,'ریاضی'),choice(12,'علوم')], next:null}};
    const levels = () => srv.levels || {teacherCamera:'low',studentCamera:'low',teacherScreen:'low',studentScreen:'low'};
    const quality = () => {
        const role = staff ? 'teacher' : 'student', choices = srv.qualityEnabled ? levels() : {};
        const camera = {low:[128,128,8,25000], medium:[256,256,12,150000], high:[360,360,20,350000]};
        const screen = {low:[640,360,5,70000], medium:[854,480,10,249000], high:[1280,720,15,500000]};
        const profile = ([width,height,frameRate,bitrate]) => ({width,height,frameRate,bitrate});
        return {camera:profile(camera[choices[role+'Camera'] || 'low']),screen:profile(screen[choices[role+'Screen'] || 'low']),
            ...(staff && srv.qualityEnabled ? {levels:{...choices}} : {})};
    };
    const boardReply = input => ({rev:srv.boardRev ?? 1,moving:srv.moving,write:srv.write,laser:srv.laser,navigate:staff && srv.navigate !== false,
        ...(srv.presenter ? {presenter:srv.presenter} : {}),...(srv.boardHead !== undefined ? {head:srv.boardHead,viewSeq:srv.boardView ?? 0,more:srv.more} : {}),
        ...(srv.boardNotice && input.boardNotice ? {notice:true} : {}),...(srv.checkpoint && (!input.board || input.checkpoint) ? {checkpoint:true} : {})});
    const person = (identity) => ({identity, name:identity, sid:`PA_${identity}`, permissions:options.permissions?.(identity) ?? listener, micCalls:[], isMicrophoneEnabled:false,
        isCameraEnabled:false, publications:new Map(), trackPublications:new Map(), getTrackPublication(source) { return this.publications.get(source); },
        async publishTrack(track, opts) {
            assert.ok(['camera','screen_share'].includes(opts.source)); assert.equal(opts.videoCodec,opts.source==='camera'?srv.cameraCodec:'vp8');
            if (srv.beforePublish) await srv.beforePublish();
            publications.push({track,opts}); track.source=opts.source;
            this.publications.set(opts.source,{track,isMuted:false}); if (opts.source==='camera') this.isCameraEnabled=true;
            lastRoom.emit('LocalTrackPublished');
        },
        async unpublishTrack(track) {
            assert.equal(track.stopped,true); this.publications.delete(track.source); if (track.source==='camera') this.isCameraEnabled=false;
            lastRoom.emit('LocalTrackUnpublished');
        },
        async publishData(packet,opts) { if (onPublish) return onPublish(packet,opts); cuePackets.push({packet:Buffer.from(packet),opts:{...opts}}); await srv.cuePending; },
        async setMicrophoneEnabled(value) {
            this.micCalls.push(value);
            if (this.error && value) throw this.error;
            this.isMicrophoneEnabled=value;
        }});
    class Room extends EventEmitter {
        constructor(options) {
            super(); this.options=options; lastRoom=this;
            this.localParticipant=person(id);
            this.remoteParticipants=new Map((options.peers ?? (staff?['student-1','student-2','admin-1']:['teacher-1','student-2'])).map(n=>[n,person(n)]));
            this.canPlaybackAudio=true;
        }
        async connect(url,token,opts) {
            this.connectOptions=opts; this.state='connecting';
            await srv.onConnect?.();
            this.state='connected'; this.emit('ConnectionStateChanged');
        }
        async disconnect() { this.state='disconnected'; this.emit('Disconnected',1); }
        async startAudio() {}
    }
    const fixtureVideo = (source='camera') => ({kind:'video',source,stopped:false, mediaStreamTrack:{addEventListener(){}},
        attach(node) { node.track=this; }, detach(node) { if (node.track===this) node.track=null; }, stop() { this.stopped=true; }});
    const rawVideo = (settings={width:256,height:144,frameRate:8}) => ({readyState:'live',handlers:{},
        addEventListener(k,f){this.handlers[k]=f;}, stop(){this.readyState='ended';}, getSettings(){return settings;},
        async applyConstraints(constraints){this.limitsApplied=true; this.constraints=constraints;}});
    const stream = raw => ({getVideoTracks:()=>[raw],getTracks:()=>[raw]});
    class MediaStream { constructor(tracks){ this.tracks=tracks; } getVideoTracks(){ return this.tracks; } }
    function createElement(tag) {
        const node=new Element(tag);
        if (tag==='canvas') {
            const calls=[];
            node.getContext=()=>({drawImage(...args){calls.push(args);}});
            if (!srv.noCrop) node.captureStream=fps=>{
                const track=rawVideo({width:node.width,height:node.height,frameRate:fps});
                crops.push({track,calls,canvas:node}); return stream(track);
            };
        }
        if (tag==='video') {
            node.play=async()=>{
                inputs.push(node);
                const [track]=node.srcObject.getVideoTracks();
                const {width,height}=track.getSettings();
                Object.assign(node,{videoWidth:width,videoHeight:height,readyState:2});
                if (srv.beforePlay) await srv.beforePlay();
            };
            node.pause=()=>{node.paused=true;};
        }
        return node;
    }
    const lk={Room,LocalVideoTrack:class { constructor(raw) { const track=fixtureVideo(); track.mediaStreamTrack=raw;
        track.stop=()=>{ track.stopped=true; raw.stop(); }; return track; } },
        ConnectionState:{Connected:'connected'},Track:{Kind:{Audio:'audio',Video:'video'},Source:{Camera:'camera',Microphone:'microphone',ScreenShare:'screen_share'}},
        DataPacket_Kind:{LOSSY:1,RELIABLE:0},DisconnectReason:{1:'CLIENT_INITIATED',2:'DUPLICATE_IDENTITY'},RoomEvent:new Proxy({}, {get:(_,key)=>key})};
    function change(identity, perm) {
        const p=identity===id?lastRoom.localParticipant:lastRoom.remoteParticipants.get(identity);
        const old=p.permissions; p.permissions=perm;
        lastRoom.emit('ParticipantPermissionsChanged',old,p);
    }
    vm.runInNewContext(classroomScript, {
        window:{isSecureContext:true,LivekitClient:lk,MediaStream,addEventListener(){},confirm:()=>true,
            navigator:{mediaDevices:{async getUserMedia(opts) {
                cameraAttempts++; cameraCaptures.push(opts); return srv.capture ? await srv.capture : stream(rawVideo());
            },async getDisplayMedia(opts) {
                captures.push(opts); if (srv.screenPrompt) return srv.screenPrompt;
                const raw=rawVideo({width:opts.video.width.max,height:opts.video.height.max,frameRate:opts.video.frameRate.max});
                return {getVideoTracks:()=>[raw],getTracks:()=>[raw]};
            }}},
            ClassroomAttention:choose=>{wakeChoice=choose;return {setLive(...args){wakeStates.push(args);},snapshot(){},expand(){}};},
            ClassroomBoard:(_,config)=>{boardConfig=config;return {position:()=>({board:lastBoard?.rev ?? 0,ink:lastBoard?.cursor ?? 0}),takeView:()=>({}),takeAim:()=>{
                const value=aim; aim=null; return value ? {aim:value} : {};
            },sync:(...p)=>{lastBoard=p[0];boards.push(p); onBoard?.(p[0]);},reset:()=>{lastBoard=null;boards.push('reset');},
            remoteCue:(...p)=>{cues.push(structuredClone(p)); if (p[0]==='l') fastLaserAt=clock();},hasFastLaser:()=>clock()-fastLaserAt<430};}},
        document:{getElementById:id=>elements[id],baseURI:'https://school.fixture.test/',createElement,addEventListener(k,f){documentEvents[k]=f;},get hidden(){return srv.hidden;}},
        performance:{now:clock}, TextEncoder, TextDecoder, Uint8Array,
        setTimeout:realTime ? setTimeout : (fn,ms)=>{const t=++timerSeq; scheduled.set(t,{fn,at:now+ms});return t;},
        clearTimeout:realTime ? clearTimeout : t=>scheduled.delete(t), AbortController, URL,
        async fetch(url,opt){
            assert.equal(url,'/fixture'); assert.equal(opt.headers['X-Classroom-CSRF'],'session');
            const input=JSON.parse(opt.body); assert.equal(input.class,classId);
            if (http) return http(input,opt.signal);
            requests.push(input);
            const fault = srv.fault?.(input);
            if (fault) return {ok:false,status:fault,json:async()=>({ok:false,message:'fixture connection error'})};
            let reply={ok:true}, status=200;
            if(input.action==='status'){
                if (srv.beforeStatus) await srv.beforeStatus();
                const phased = ['idle','reserved'].includes(srv.phase) ? {phase:srv.phase} : {phase:srv.phase,slot:srv.slot ?? 11, lesson:srv.lesson,epoch:srv.epoch,resumable:srv.resumable,startTime:srv.startTime ?? null,subject:'ریاضی',period:'زنگ یک'};
                reply={...reply,ready:srv.ready,...phased,joinable:srv.phase==='live'||(staff&&srv.phase==='starting'&&srv.staffCanJoin)};
                if (srv.startFailure) Object.assign(reply,{startFailure:srv.startFailure,callbackMissing:srv.startFailure==='callback'});
                if (srv.recording) reply.recording=srv.recording;
                if (staff && srv.phase==='idle') reply.schedule=srv.schedule;
                if (!staff && srv.phase==='idle' && srv.practiceAvailable) reply.practiceAvailable=true;
                if(input.sid && srv.phase==='live' && input.epoch===srv.epoch) {
                    const profilesRev=JSON.stringify(srv.profiles);
                    if (input.people) reply.seconds=srv.seconds;
                    if (input.profilesRev!==profilesRev) reply.profiles=srv.profiles;
                    Object.assign(reply,{admitted:true,profilesRev,chat:srv.chat,board:boardReply(input),
                        cameraCodec:srv.cameraCodec,screen:srv.screen || null,mediaMode:srv.mediaMode,cameraTeacher:'teacher-1',
                        wakeMode:srv.wakeMode || 'live',wakeControl:staff && srv.wakeControl !== false});
                    const current = quality();
                    reply.qualityRev=JSON.stringify(current);
                    if (input.qualityRev!==reply.qualityRev) reply.quality=current;
                    if (srv.screenEnabled && (staff || srv.pupilScreenEnabled)) reply.screenEnabled=true;
                    if (staff) Object.assign(reply,{pupilScreenEnabled:!!srv.pupilScreenEnabled,access:structuredClone(srv.access),members:Object.fromEntries([lastRoom.localParticipant,
                        ...lastRoom.remoteParticipants.values()].map(p=>[p.identity,p.sid]))});
                }
                if (srv.afterStatus) await srv.afterStatus();
            } else if (input.action==='board-sync') {
                if (srv.phase==='live' && input.epoch===srv.epoch) reply.board=boardReply(input);
                else { status=409; reply={ok:false}; }
            } else if (input.action==='board-ink'||input.action==='board-page') {
                if (srv.beforeWrite) await srv.beforeWrite(input);
                Object.assign(reply, srv.writeReply);
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
            } else if(input.action==='set-quality') {
                assert.ok(staff && srv.qualityEnabled);
                srv.levels=input.levels;
                reply.quality=quality(); reply.qualityRev=JSON.stringify(reply.quality);
            } else if(input.action==='screen-access') {
                const p=input.identity ? lastRoom.remoteParticipants.get(input.identity) : lastRoom.localParticipant;
                if (input.identity) assert.equal(input.targetSid,p.sid);
                const src=p.permissions.canPublishSources.filter(x=>x!==3);
                if (input.enabled) { srv.screen={identity:p.identity,sid:p.sid}; src.push(3); }
                else srv.screen=null;
                change(p.identity,{canPublish:!!src.length,canPublishSources:src});
                reply.screen=srv.screen;
            } else if (input.action==='start-recording') {
                srv.recording={id:'fixture-recording',notice:'preparing',confirm:true,canSend:true,group:'گروه کلاس',messenger:'بله',delayMinutes:60};
                reply.recording=srv.recording;
            } else if(input.action==='close') {
                if (typeof input.sendRecording==='boolean') reply.recording=input.sendRecording ? 'queued' : 'discarded';
                srv.recording=null;
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
        for (let i=0;i<140 && requests.filter(r=>r.action==='status').length===before;i++) await time();
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
            lastRoom.emit(value?'TrackSubscribed':'TrackUnsubscribed',track,this,person);
        }};
        person.publications.set(source,pub); person.trackPublications.set(trackSid,pub);
        lastRoom.emit('TrackPublished',pub,person);
        return pub;
    }
    function visibility(value) { srv.hidden=value; documentEvents.visibilitychange(); }
    return {elements,srv,requests,boards,captures,cameraCaptures,crops,inputs,publications,subscriptions,visibility,remoteTrack,rawVideo,stream,wakeStates,cuePackets,cues,publishCue:(...p)=>boardConfig.publishCue(...p),boardSend:(...args)=>boardConfig.send(...args),chooseWake:mode=>wakeChoice(mode),time,status,change,controls,fixtureVideo,advance,aim(point){aim=point;boardConfig.aimSoon();},get position(){return lastBoard;},get cameraAttempts(){return cameraAttempts;},get room(){return lastRoom;}};
}

module.exports = {fixture, listener, Element};
