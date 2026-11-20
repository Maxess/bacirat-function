const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname,'../school-addon/upload/online-classroom/assets/room-attention.js'),'utf8');

function fixture({teacher=false, acquire, supported=true}={}) {
    let now=Date.UTC(2026,10,18), seq=0, count=0;
    const nodes=new Map(), events=new Map(), tasks=new Map(), locks=[], changes=[];
    const el=id=>{
        if (!teacher && id.startsWith('wake-')) return null;
        if (!nodes.has(id)) nodes.set(id,{dataset:{},textContent:'',hidden:true,handlers:{},addEventListener(k,fn){this.handlers[k]=fn;}});
        return nodes.get(id);
    };
    const sentinel=()=>{
        const item={released:false, releases:0, addEventListener(_,fn){this.onRelease=fn;},async release(){
            this.released=true; this.releases++; this.onRelease?.();
        }};
        locks.push(item); return item;
    };
    const document={getElementById:el,hidden:false,addEventListener(k,fn){events.set(k,fn);}};
    const window={isSecureContext:true,localStorage:{getItem:()=>{throw Error('class wake mode is not local');}},navigator:supported ? {wakeLock:{async request(type){
        assert.equal(type,'screen'); count++; return acquire ? acquire(count,sentinel) : sentinel();
    }}} : {}};
    vm.runInNewContext(script,{window,document,Date:class extends Date {static now(){return now;}},
        setTimeout(fn,delay){const t=++seq;tasks.set(t,{at:now+delay,fn});return t;},clearTimeout:t=>tasks.delete(t)});
    const attention=window.ClassroomAttention(mode=>{changes.push(mode);});
    const tick=()=>new Promise(resolve=>setImmediate(resolve));
    async function move(ms){
        const end=now+ms;
        while (true) {
            const task=[...tasks.entries()].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
            if (!task) break;
            tasks.delete(task[0]); now=task[1].at; task[1].fn(); await tick();
        }
        now=end; await tick();
    }
    return {el,attention,locks,changes,move,tick,get requests(){return count;},hidden(value){document.hidden=value;events.get('visibilitychange')();},
        event(type,trusted=true){events.get(type)({type,isTrusted:trusted});},select(value){el('wake-mode').value=value;el('wake-mode').handlers.change();}};
}

test('default wake lock is only for an admitted, visible class, is reacquired safely and has a system opt-out',async()=>{
    const f=fixture(); await f.tick(); assert.equal(f.requests,0);
    f.attention.setLive(true); await f.tick(); assert.equal(f.requests,1);
    await f.move(5*60*1000+5); assert.equal(f.locks[0].released,false,'passive pupils keep seeing the lesson');
    f.hidden(true); assert.equal(f.locks[0].released,true);
    f.hidden(false); await f.tick(); assert.equal(f.requests,2);
    assert.equal(f.el('wake-mode'),null,'there is no pupil control');
    f.attention.setLive(true,'system'); assert.equal(f.locks[1].released,true);
    f.event('pointerdown'); await f.tick(); assert.equal(f.requests,2);
    f.attention.setLive(true,'live'); await f.tick(); assert.equal(f.requests,3);
    f.attention.setLive(false); assert.equal(f.locks[2].released,true);
});

test('idle releases after five minutes of real inactivity, ignores polls and synthetic input, and resumes on touch',async()=>{
    const f=fixture(); f.attention.setLive(true,'idle'); await f.tick();
    await f.move(250000); f.event('pointerdown');
    await f.move(299999); assert.equal(f.locks[0].released,false);
    f.attention.setLive(true,'idle'); f.event('pointerdown',false);
    await f.move(1); assert.equal(f.locks[0].released,true);
    f.hidden(true); f.hidden(false); await f.tick(); assert.equal(f.requests,1);
    f.event('pointerdown'); await f.tick(); assert.equal(f.requests,2); assert.equal(f.locks[1].released,false);
});

test('a late wake-lock resolution after leaving is released immediately; unavailable devices make no fake lock',async()=>{
    let finish;
    const f=fixture({teacher:true,acquire:(_, sentinel)=>new Promise(resolve=>{finish=()=>resolve(sentinel());})});
    f.attention.setLive(true); await f.tick(); f.attention.setLive(false); finish(); await f.tick();
    assert.equal(f.locks[0].releases,1); assert.equal(f.el('wake-control').dataset.awake,'false');
    const unavailable=fixture({teacher:true,supported:false}); unavailable.attention.setLive(true,'live',true); await unavailable.tick();
    assert.equal(unavailable.requests,0); assert.equal(unavailable.el('wake-mode').disabled,false,'a teacher can set the class policy without local wake support');
});

test('only the authorized teacher menu can propose a mode, and only a server acknowledgment applies it',async()=>{
    const f=fixture({teacher:true});
    f.attention.setLive(true,'live',false); await f.tick();
    assert.equal(f.el('wake-control').hidden,true); f.select('system'); assert.deepEqual(f.changes,[]);
    f.attention.setLive(true,'live',true); assert.equal(f.el('wake-control').hidden,false);
    f.select('system'); assert.deepEqual(f.changes,['system']);
    assert.equal(f.locks[0].released,false, 'the local selection does not change the policy');
    f.attention.setLive(true,'system',true); assert.equal(f.locks[0].released,true);
    f.attention.setLive(true,'system',true,true); f.select('idle'); assert.deepEqual(f.changes,['system']);
    f.attention.setLive(false,'system',true); assert.equal(f.el('wake-control').hidden,true);
});

test('power-policy refusal does not trigger background retry loops',async()=>{
    const f=fixture({acquire:i=>{throw new Error('battery saver');}}); f.attention.setLive(true); await f.tick();
    for(let i=0;i<40;i++)f.attention.setLive(true);
    await f.move(5000); f.event('pointerdown'); await f.tick(); assert.equal(f.requests,1);
    f.hidden(true);f.hidden(false);await f.tick();assert.equal(f.requests,2);
});

test('expanded board shows fresh pupil chat for two seconds and newly admitted names for one, not history or staff chat',async()=>{
    const f=fixture(); const a=f.attention, chat=f.el('board-chat-toast'), join=f.el('board-join-toast');
    const one={'student-1':{name:'احسانی'}}, two={...one,'student-2':{name:'صادقی'}};
    const message={author:'student-1',text:'قدیمی',recent:true};
    a.setLive(true); a.expand(true); a.snapshot(one,[message],false,'teacher-1');
    assert.equal(chat.hidden,true); assert.equal(join.hidden,true);
    const family='👩‍👩‍👧‍👦';
    a.snapshot(two,[{...message,text:family.repeat(35)},{author:'teacher-2',text:'نباید دیده شود',recent:true}],false,'teacher-1');
    assert.equal(chat.textContent,`احسانی: ${family.repeat(29)}…`);
    assert.equal(join.textContent,'صادقی وارد کلاس شد');
    await f.move(999); assert.equal(join.hidden,false); await f.move(1);
    assert.equal(join.hidden,true); assert.equal(chat.hidden,false);
    await f.move(1000); assert.equal(chat.hidden,true);
    a.snapshot(two,[{...message,recent:false}],false,'teacher-1'); assert.equal(chat.hidden,true);
    a.expand(false); a.snapshot(two,[message],false,'teacher-1'); assert.equal(chat.hidden,true);
    a.expand(true); a.snapshot(two,[message],true,'teacher-1'); a.snapshot(two,[message],false,'teacher-1'); assert.equal(chat.hidden,true);
    a.snapshot(two,[message],false,'teacher-1'); assert.equal(chat.hidden,false); f.hidden(true); assert.equal(chat.hidden,true);
});
