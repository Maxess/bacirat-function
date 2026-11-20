const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');
const script = fs.readFileSync(path.join(__dirname, '../school-addon/upload/online-classroom/assets/board.js'), 'utf8');

// The real board, with DOM/canvas and the network acknowledgement stubbed.
function fixture() {
    const elements = new Map(), timers = new Map(), calls = [];
    let time = 0, seq = 0, access = true, response;
    const display = { strokes:[] };
    const context = {
        save(){}, restore(){}, beginPath(){}, moveTo(){}, lineTo(){}, arc(){}, fillRect(){},
        clearRect(){ display.strokes=[]; },
        stroke(){ display.strokes.push([this.globalCompositeOperation,this.strokeStyle]); },
        fill(){ this.stroke(); },
    };
    const rect = { left:0,top:0,width:1000,height:500 };
    function el(id) {
        if (!elements.has(id)) {
            const classes = new Set(), events = {};
            elements.set(id, {
                id, dataset:{}, style:{}, width:1600, height:1000, clientWidth:1000, clientHeight:500, scrollWidth:1000, scrollHeight:500,
                value: id==='ink-color' ? '#0099ff' : id==='ink-size' ? '4' : '',
                classList:{contains: k=>classes.has(k), toggle(k,v){ if (v) classes.add(k); else classes.delete(k); }, remove:k=>classes.delete(k)},
                addEventListener(k,fn){ events[k]=fn; }, setAttribute(k,v){ this[k]=v; }, getContext:()=>context,
                querySelector: k=>el(k), querySelectorAll:()=>[], replaceChildren(){}, getBoundingClientRect:()=>rect,
                setPointerCapture(id){this.capture=id;}, hasPointerCapture(id){return id===this.capture;}, releasePointerCapture(){this.capture=null;},
                fire(type, more={}) { return events[type]?.({type,button:0,clientX:0,clientY:0,pointerId:1,pointerType:'mouse',preventDefault(){},...more}); },
            });
        }
        return elements.get(id);
    }
    const window = { addEventListener(){}, confirm:()=>true };
    vm.runInNewContext(script, {
        document:{getElementById:el,createElement:el}, window, crypto:webcrypto,
        setTimeout(fn,ms){const id=++seq;timers.set(id,{at:time+ms,fn});return id;}, clearTimeout:id=>timers.delete(id),
    });
    const root = el('classroom'); root.dataset.moderator='yes';
    const board = window.ClassroomBoard(root, {
        canEdit:()=>access,
        send(action,data){
            const call={action,...structuredClone(data)};
            calls.push(call);
            return response ? response(call) : Promise.resolve(action==='board-ink' ? {ink:calls.length} : {boardRev:2});
        },
        explain: e=>e.message,
    });
    function sync(ops=[], extras={}, scope={lesson:'L',epoch:'E',sid:'PA_staff'}) {
        board.sync({rev:1,reset:false,doc:null,page:1,docs:[],cursor:0,ops,...extras}, scope);
    }
    async function flush() {
        const next = [...timers.entries()].sort((a,b)=>a[1].at-b[1].at)[0];
        if (next) {timers.delete(next[0]);time=next[1].at;next[1].fn();}
        await new Promise(resolve=>setImmediate(resolve));
    }
    return {el,board,sync,display,calls,flush,delay:s=>response=s, access:v=>access=v, draw(type,x,y,other){el('board-ink').fire(type,{clientX:x,clientY:y,...other});}};
}
const drawOp = (id, points, color='#0099ff') => ({tool:'pen',size:4,id,points,color});

test('pen flushes while held, with stable id, continuous anchors and bounded packets', async()=>{
    const f=fixture(); f.sync();
    f.draw('pointerdown',100,155);
    f.draw('pointermove',120,155);
    await f.flush();
    const first = f.calls[0].strokes[0];
    assert.match(first.id,/^[a-f0-9]{32}$/);
    assert.deepEqual(first.points,[[1000,3100],[1200,3100]]);
    for (let i=1;i<=120;i++) f.draw('pointermove',120+i*2,155);
    await f.flush();
    f.draw('pointerup',480,152);
    await f.flush();
    const collected = f.calls.flatMap(call=>call.strokes);
    assert.ok(collected.length>=4 && collected.every(part=>part.id===first.id && part.points.length<=96));
    assert.ok(collected.slice(1).every((part,i)=>JSON.stringify(part.points[0])===JSON.stringify(collected[i].points.at(-1))));
    assert.deepEqual(collected.at(-1).points.at(-1),[4800,3040]);
    f.sync(collected,{cursor:f.calls.length});
    assert.deepEqual(f.display.strokes,Array.from({length:3},()=>['source-over',first.color]));
});

test('tap erases all fragments; a quick sweep catches crossings and retains unrelated ink and legacy masks', async()=>{
    const f=fixture();
    const a='a'.repeat(32), b='b'.repeat(32), c='old-00000004';
    f.sync([drawOp(a,[[900,3000],[4600,3000]]),drawOp(a,[[4600,3000],[8100,4900]]),drawOp(b,[[2000,7800],[9000,7800]],'#ff6600'),
        drawOp(c,[[6000,8100],[7000,9900]],'#88ffbb'), {...drawOp(undefined,[[350,650],[350,750]]),tool:'erase'}],{cursor:6});
    f.delay(() => Promise.resolve({ink:7}));
    f.el('eraser').fire('click');
    f.draw('pointerdown',205,150); f.draw('pointerup',205,150);
    assert.deepEqual(f.display.strokes,[['source-over','#ff6600'],['source-over','#88ffbb'],['destination-out','#0099ff']]);
    f.draw('pointerdown',300,337); f.draw('pointermove',300,450); f.draw('pointerup',300,450);
    await f.flush();
    assert.deepEqual(f.calls[0].strokes,[{tool:'erase-stroke',targets:[a,b]}]);
    assert.deepEqual(f.display.strokes,[['source-over','#88ffbb'],['destination-out','#0099ff']]);
});

test('old draft cannot leak across a page revision or transport change', async()=>{
    const f=fixture(); f.sync();
    let reply;
    f.delay(()=>new Promise(resolve=>{reply=resolve;}));
    f.draw('pointerdown',90,100);
    await f.flush();
    f.draw('pointermove',120,115);
    assert.equal(f.calls.length,1);
    f.sync([],{rev:2,reset:true});
    reply({ink:3}); await new Promise(resolve=>setImmediate(resolve));
    await f.flush();
    assert.equal(f.calls.length,1);
    assert.deepEqual(f.display.strokes,[]);
    f.delay(()=>Promise.resolve({ink:1}));
    f.draw('pointerdown',120,115); f.draw('pointerup',120,115);
    f.sync([],{rev:2},{lesson:'L2',epoch:'E',sid:'PA_staff'});
    await f.flush();
    assert.equal(f.calls.length,1);
});

test('clear drains pending ink, waits for the new revision and never sends a stale gesture', async()=>{
    const f=fixture(); f.sync();
    f.draw('pointerdown',50,50); f.draw('pointermove',100,50); f.draw('pointerup',100,50);
    f.el('board-clear').fire('click');
    await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(f.calls.map(call=>[call.action,call.board]),[['board-ink',1],['board-clear',1]]);
    assert.equal(f.el('pen').disabled,true);
    f.draw('pointerdown',280,80); f.draw('pointerup',280,80);
    await f.flush();
    assert.equal(f.calls.length,2);
    f.sync([],{rev:2,reset:true});
    assert.equal(f.el('pen').disabled,false);
    assert.deepEqual(f.display.strokes,[]);
});
