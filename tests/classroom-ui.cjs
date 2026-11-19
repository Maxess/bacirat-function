const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../school-addon/upload/online-classroom/assets/classroom.js'), 'utf8');
const listener = {canPublish:false, canPublishSources:[]};
const mic = {canPublish:true,canPublishSources:[2]};
class Element {
    constructor(tag) { this.tag = tag; this.handlers = {}; this.children = []; this.dataset = {}; this.textContent = ''; this.value = ''; this.disabled = false; this.hidden = false; this.options = []; }
    addEventListener(k,v) { this.handlers[k] = v; }
    setAttribute(k,v) { this[k] = v; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
    querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag)]); }
    async click() { if (!this.disabled && !this.hidden) await this.handlers.click(); }
}
function fixture(staff) {
    const elements = Object.fromEntries(['classroom','status','lecture-title','notice','mic-feedback','people','live-panel','waiting','wait-title','wait-message',
        'audio-tracks','chat-log','chat-form','chat-text','leave','microphone','enable-audio', ...(staff ? ['slot','open','close','clear-floor'] : ['join'])].map(id => [id,new Element(id)]));
    elements.classroom.dataset = { moderator:staff?'yes':'no',api:'/fixture',csrf:'session',class:'22' };
    if (staff) elements.slot.options = [{value:'11', remove(){ elements.slot.options=[]; }}];
    const requests=[], scheduled=new Map(), boards=[];
    let timerSeq=0, lastRoom;
    const id=staff?'teacher-1':'student-1';
    const srv={phase:'idle',lesson:'lesson-11',epoch:'epoch-A',watch:true,seconds:{'student-1':9,'student-2':21}};
    const person = (identity) => ({identity, name:identity, sid:`PA_${identity}`, permissions:listener, micCalls:[], isMicrophoneEnabled:false,
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
        async connect() { this.state='connected'; this.emit('ConnectionStateChanged'); }
        async disconnect() { this.state='disconnected'; this.emit('Disconnected',1); }
        async startAudio() {}
    }
    const lk={Room,ConnectionState:{Connected:'connected'},Track:{Kind:{Audio:'audio'}},DisconnectReason:{1:'CLIENT_INITIATED',2:'DUPLICATE_IDENTITY'},RoomEvent:new Proxy({}, {get:(_,key)=>key})};
    function change(identity, perm) {
        const p=identity===id?lastRoom.localParticipant:lastRoom.remoteParticipants.get(identity);
        const old=p.permissions; p.permissions=perm;
        lastRoom.emit('ParticipantPermissionsChanged',old,p);
    }
    vm.runInNewContext(script, {
        window:{isSecureContext:true,LivekitClient:lk,addEventListener(){},confirm:()=>true,ClassroomBoard:()=>({position:()=>({board:0,ink:0}),sync:(...p)=>boards.push(p),reset:()=>boards.push('reset')})},
        document:{getElementById:id=>elements[id],createElement:tag=>new Element(tag),addEventListener(){}},
        setTimeout(fn,ms){const t=++timerSeq; scheduled.set(t,{fn,ms});return t;}, clearTimeout:t=>scheduled.delete(t), AbortController,
        async fetch(url,opt){
            assert.equal(url,'/fixture'); assert.equal(opt.headers['X-Classroom-CSRF'],'session');
            const input=JSON.parse(opt.body); assert.equal(input.class,22); requests.push(input);
            let reply={ok:true};
            if(input.action==='status'){
                const phased = srv.phase==='idle' ? {phase:'idle'} : {phase:srv.phase,slot:11, lesson:srv.lesson,epoch:srv.epoch,resumable:srv.resumable,subject:'ریاضی',period:'زنگ یک'};
                reply={...reply,watch:srv.watch,...phased,joinable:srv.phase==='live'||(staff&&srv.phase==='starting'&&srv.ready)};
                if(input.sid && srv.phase==='live' && input.epoch===srv.epoch) Object.assign(reply,{admitted:true,seconds:srv.seconds,chat:[],board:{rev:1}});
            } else if(input.action==='open' && srv.phase!=='live') {
                srv.phase='starting'; reply.preparing=true;
            } else if(input.action==='open'||input.action==='ticket') {
                Object.assign(reply,{url:'wss://media.example.test',token:'opaque',lesson:srv.lesson,epoch:srv.epoch});
            } else if(input.action==='give-mic') change(input.identity,mic);
            else if(input.action==='cut-mic') change(input.identity,listener);
            else if(input.action==='cut-all-mics') for(const p of [lastRoom.localParticipant,...lastRoom.remoteParticipants.values()]) change(p.identity,listener);
            else if(input.action==='close') srv.phase='idle';
            return {ok:true,json:async()=>reply};
        },
    });
    async function time() {
        const next=[...scheduled.entries()].sort((a,b)=>a[1].ms-b[1].ms)[0];
        if(next){scheduled.delete(next[0]);await next[1].fn();}
        await new Promise(resolve=>setImmediate(resolve));
    }
    function controls(identity){return elements.people.children.find(row=>row.children[0].children[0].textContent.startsWith(identity)).querySelectorAll('button');}
    return {elements,srv,requests,boards,time,change,controls,get room(){return lastRoom;}};
}

test('student waits without media, auto enters and sees attendance only for pupils, then waits again',async()=>{
    const f=fixture(false); await f.time();
    assert.equal(f.room,undefined); assert.equal(f.elements['live-panel'].hidden,true);
    assert.equal(f.requests.some(r=>r.action==='ticket'),false);
    f.srv.phase='live'; await f.time(); await f.time();
    assert.equal(f.room.options.singlePeerConnection,false); assert.equal(f.elements['live-panel'].hidden,false);
    assert.deepEqual(f.elements.people.querySelectorAll('bdi').map(n=>n.textContent),['00:00:09','00:00:21']);
    assert.deepEqual(f.room.localParticipant.micCalls,[]); assert.equal(f.elements.microphone.disabled,true);
    f.change('student-1',mic); assert.deepEqual(f.room.localParticipant.micCalls,[]);
    await f.elements.microphone.click(); assert.equal(f.room.localParticipant.isMicrophoneEnabled,true);
    const first=f.room; f.srv.phase='paused'; await f.time();
    assert.equal(f.elements['live-panel'].hidden,true); assert.equal(first.state,'disconnected'); assert.equal(f.elements.microphone.disabled,true);
    assert.ok(f.boards.includes('reset'));
    f.srv.epoch='epoch-B'; f.srv.phase='live'; await f.time(); await f.time();
    assert.notEqual(first,f.room); assert.equal(f.elements['live-panel'].hidden,false);
    assert.equal(f.elements.microphone.disabled,true); assert.deepEqual(f.room.localParticipant.micCalls,[]);
});

test('assigned moderator starts selected period, has independent mic and video controls, and can finish',async()=>{
    const f=fixture(true); await f.time(); assert.equal(f.elements.open.disabled,true);
    f.elements.slot.value='11'; f.elements.slot.handlers.change(); await f.elements.open.click();
    assert.equal(f.requests.find(r=>r.action==='open').slot,11);
    await f.time();
    assert.equal(f.requests.some(r=>r.action==='ticket'),false);
    f.srv.ready=true; await f.time();
    assert.deepEqual(f.room.localParticipant.micCalls,[]);
    f.srv.phase='live'; await f.time();
    const [give]=f.controls('student-1');
    await give.click(); await f.controls('student-1').at(-1).click();
    assert.equal(f.room.remoteParticipants.get('student-1').permissions,mic);
    await f.controls('student-1').find(b=>b.textContent==='قطع میکروفون').click(); assert.equal(f.room.remoteParticipants.get('student-1').permissions,listener);
    await f.elements['clear-floor'].click();
    assert.deepEqual(f.requests.filter(r=>r.action.startsWith('cut-')).map(r=>r.action),['cut-video','cut-mic','cut-all-mics']);
    await f.elements.close.click(); await f.time();
    assert.equal(f.requests.filter(r=>r.action==='open').length,1); assert.equal(f.elements['live-panel'].hidden,true);
});

test('microphone access failure is shown without exposing browser diagnostics',async()=>{
    const f=fixture(false); f.srv.phase='live'; await f.time(); await f.time();
    f.change('student-1',mic);
    f.room.localParticipant.error=Object.assign(new Error('sensitive-device-detail'),{name:'NotAllowedError'});
    await f.elements.microphone.click();
    assert.match(f.elements['mic-feedback'].textContent,/MIC-ACCESS/);
    assert.doesNotMatch(f.elements['mic-feedback'].textContent,/sensitive-device-detail/);
});
