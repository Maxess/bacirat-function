const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');
const script = fs.readFileSync(path.join(__dirname, '../school-addon/upload/online-classroom/assets/board.js'), 'utf8');

// The real board, with DOM/canvas and the network acknowledgement stubbed.
function fixture({staff=true,reducedMotion=false}={}) {
    const elements = new Map(), timers = new Map(), frames = new Map(), calls = [], notices = [];
    let time = 0, seq = 0, access = staff, response, points;
    const display = { strokes:[], paths:[] };
    const context = {
        save(){}, restore(){}, beginPath(){ points=[]; },
        moveTo(x,y){ points.push([x,y]); }, lineTo(x,y){ points.push([x,y]); }, arc(x,y){ points.push([x,y]); }, fillRect(){},
        clearRect(){ display.strokes=[]; display.paths=[]; },
        stroke(){ display.strokes.push([this.globalCompositeOperation,this.strokeStyle]); display.paths.push(points); },
        fill(){ this.stroke(); },
    };
    const rect = { left:0,top:0,width:1000,height:500 };
    function el(id) {
        if (!staff && ['eraser','eraser-tip','board-file','board-doc','board-upload','board-clear','page-prev','page-next','page-go','page-number'].includes(id)) return null;
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
    const window = { addEventListener(){}, confirm:()=>true, matchMedia:()=>({matches:reducedMotion}) };
    vm.runInNewContext(script, {
        document:{getElementById:el,createElement:el}, window, crypto:webcrypto, performance:{now:()=>time},
        requestAnimationFrame(fn){const id=++seq;frames.set(id,fn);return id;}, cancelAnimationFrame:id=>frames.delete(id),
        setTimeout(fn,ms){const id=++seq;timers.set(id,{at:time+ms,fn});return id;}, clearTimeout:id=>timers.delete(id),
    });
    const root = el('classroom'); root.dataset.moderator=staff ? 'yes' : 'no';
    const board = window.ClassroomBoard(root, {
        canEdit:()=>access, notice:text=>notices.push(text),
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
    function frame(ms) {
        time += ms;
        const planned=[...frames.values()]; frames.clear();
        for (const callback of planned) callback(time);
    }
    return {el,board,sync,display,calls,notices,flush,frame,delay:s=>response=s, access:v=>access=v, draw(type,x,y,other){el('board-ink').fire(type,{clientX:x,clientY:y,...other});}};
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


const digit = drawOp('b'.repeat(32),[[3200,2300],[3900,1600],[4550,2100],[4550,2800],[3300,4300],[4800,4300]]);
const px = (f,[x,y]) => [f.el('board-ink').width*x/10000, f.el('board-ink').height*y/10000];

test('a quick digit arriving in one delta is revealed along its path instead of stamped all at once', ()=>{
    const f=fixture({staff:false}); f.sync();
    f.sync([digit],{moving:true,cursor:6});
    assert.equal(f.display.paths.length,0);
    f.frame(40);
    assert.ok(f.display.paths.length>0);
    assert.deepEqual(f.display.paths[0][0],px(f,digit.points[0]));
    assert.ok(f.display.paths.every(path=>path.every(([,y])=>y<px(f,digit.points.at(-1))[1])),'end of digit not visible at the first frame');
    f.frame(245);
    assert.deepEqual(f.display.paths.at(-1).at(-1),px(f,digit.points.at(-1)));
    assert.deepEqual({...f.board.position()},{board:1,ink:6},'animation does not delay the reader cursor');
});

test('no playback on reset pages, catch-up or reduced motion; old playback cannot survive erase or reset', ()=>{
    const f=fixture({staff:false}); f.sync([digit],{moving:true,cursor:6});
    assert.deepEqual(f.display.paths.at(-1).at(-1),px(f,digit.points.at(-1)));
    const second={...digit,id:'c'.repeat(32)};
    f.sync([second],{moving:true,cursor:12,more:true});
    assert.deepEqual(f.display.paths.at(-1).at(-1),px(f,digit.points.at(-1)));
    const third={...digit,id:'d'.repeat(32)};
    f.sync([third],{moving:true,cursor:18});
    assert.equal(f.display.paths.length,3,'last catch-up packet is painted immediately as well');
    const fourth={...digit,id:'e'.repeat(32)};
    f.sync([fourth],{moving:true,cursor:24});
    assert.equal(f.display.paths.length,3);
    f.frame(40);
    f.sync([{tool:'erase-stroke',targets:[digit.id,second.id,third.id,fourth.id]}],{moving:true,cursor:25});
    f.frame(250);
    assert.deepEqual(f.display.paths,[]);
    f.sync([third],{moving:true,rev:2,cursor:6});
    f.sync([second],{moving:true,rev:2,cursor:12});
    f.sync([],{rev:3,reset:true});
    f.frame(250);
    assert.deepEqual(f.display.paths,[]);
    const accessible=fixture({staff:false,reducedMotion:true}); accessible.sync();
    accessible.sync([digit],{moving:true,cursor:6});
    assert.deepEqual(accessible.display.paths.at(-1).at(-1),px(accessible,digit.points.at(-1)));
});


test('a granted pupil can write only with the pen; revocation discards drafts but keeps confirmed ink', async()=>{
    const f = fixture({staff:false}); f.access(true); f.sync();
    assert.equal(f.el('ink-tools').hidden,true);
    assert.equal(f.el('board-footer').hidden,true);
    f.draw('pointerdown',100,100); await f.flush();
    assert.deepEqual(f.calls,[]);
    f.sync([],{write:true});
    assert.equal(f.el('ink-tools').hidden,false);
    assert.equal(f.el('pen').disabled,false);
    assert.equal(f.el('eraser'),null);
    assert.equal(f.el('board-clear'),null);
    f.draw('pointerdown',100,100,{pointerType:'touch'}); f.draw('pointerup',181,202,{pointerType:'touch'});
    await f.flush();
    assert.equal(f.calls.length,1);
    assert.deepEqual(f.calls[0].strokes[0].points,[[1000,2000],[1810,4040]]);
    f.sync(f.calls[0].strokes,{write:true,cursor:1});
    f.draw('pointerdown',250,260);
    assert.equal(f.display.paths.length,2);
    f.sync([],{write:false,cursor:1});
    await f.flush();
    assert.equal(f.el('ink-tools').hidden,true);
    assert.equal(f.calls.length,1);
    assert.equal(f.display.paths.length,1);
    f.sync([],{write:true,cursor:1}); await f.flush();
    assert.equal(f.calls.length,1, 'previous drafts are not sent if the teacher grants again');
});
