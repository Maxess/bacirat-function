const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');
const script = fs.readFileSync(path.join(__dirname, '../school-addon/upload/online-classroom/assets/board.js'), 'utf8');

// The real board, with DOM/canvas and the network acknowledgement stubbed.
function fixture({staff=true,navigate=staff,reducedMotion=false}={}) {
    const elements = new Map(), timers = new Map(), frames = new Map(), calls = [], notices = [];
    let time = 0, seq = 0, access = staff, response, points, failDownload = false;
    const downloads = [];
    const display = { strokes:[], paths:[] };
    const context = {
        save(){}, restore(){}, beginPath(){ points=[]; },
        moveTo(x,y){ points.push([x,y]); }, lineTo(x,y){ points.push([x,y]); }, arc(x,y){ points.push([x,y]); }, fillRect(){},
        clearRect(){ display.strokes=[]; display.paths=[]; }, drawImage(){ display.images = (display.images || 0)+1; },
        stroke(){ display.strokes.push([this.globalCompositeOperation,this.strokeStyle]); display.paths.push(points); },
        fill(){ this.stroke(); },
    };
    const rect = { left:0,top:0,width:1000,height:500 };
    function el(id) {
        if (!staff && ['pan','board-zoom','board-zoom-in','board-zoom-out','board-laser','board-file','board-doc','board-upload','board-clear','page-prev','page-next','page-go','page-number'].includes(id)) return null;
        if (!elements.has(id)) {
            const classes = new Set(), events = {};
            elements.set(id, {
                id, dataset:{}, style:{}, children:[], scrollLeft:0, scrollTop:0, width:1600, height:1000, clientWidth:1000, clientHeight:500, scrollWidth:1000, scrollHeight:500,
                value: id==='ink-color' ? '#0099ff' : id==='ink-size' ? '4' : '',
                classList:{contains: k=>classes.has(k), add:k=>classes.add(k), toggle(k,v){ if (v) classes.add(k); else classes.delete(k); }, remove:k=>classes.delete(k)},
                addEventListener(k,fn){ events[k]=fn; }, setAttribute(k,v){ this[k]=v; }, getContext:()=>context,
                querySelector: k=>el(k), querySelectorAll:()=>[], replaceChildren(){this.children=[];}, getBoundingClientRect:()=>rect,
                append(node){node.parent=this; this.children.push(node);}, remove(){if(this.parent) this.parent.children=this.parent.children.filter(x=>x!==this);}, focus(){},
                setPointerCapture(id){this.capture=id;}, hasPointerCapture(id){return id===this.capture;}, releasePointerCapture(){this.capture=null;},
                fire(type, more={}) { return events[type]?.({type,button:0,clientX:0,clientY:0,pointerId:1,pointerType:'mouse',preventDefault(){},...more}); },
            });
        }
        return elements.get(id);
    }
    const window = { createImageBitmap:async()=>({width:600,height:800,close(){}}), addEventListener(){}, confirm:()=>true, matchMedia:()=>({matches:reducedMotion}) };
    vm.runInNewContext(script, {
        document:{baseURI:'https://school.fixture.test/',getElementById:el,createElement:tag=>el(`${tag}-${++seq}`),createElementNS:(_,tag)=>el(`${tag}-${++seq}`),addEventListener(){}},
        AbortController, URL, window, crypto:webcrypto, performance:{now:()=>time},
        async fetch(url,options) { downloads.push({url,options}); return {ok:!failDownload,async blob(){return {};}}; },
        requestAnimationFrame(fn){const id=++seq;frames.set(id,fn);return id;}, cancelAnimationFrame:id=>frames.delete(id),
        setTimeout(fn,ms){const id=++seq;timers.set(id,{at:time+ms,fn});return id;}, clearTimeout:id=>timers.delete(id),
    });
    const root = el('classroom'); root.dataset.moderator=staff ? 'yes' : 'no'; root.dataset.content='/content?class=22';
    const board = window.ClassroomBoard(root, {
        canEdit:()=>access, notice:text=>notices.push(text), identity:()=>staff ? 'teacher-1':'student-1',
        authorName:()=> 'نام نویسنده', onExpand(){}, aimSoon(){},
        send(action,data){
            const call={action,...structuredClone(data)};
            calls.push(call);
            return response ? response(call) : Promise.resolve(action==='board-ink' ? {ink:calls.length} : {boardRev:2});
        },
        explain: e=>e.message,
    });
    function sync(ops=[], extras={}, scope={lesson:'L',epoch:'E',sid:'PA_staff'}, requestedAt) {
        board.sync({rev:1,reset:false,doc:null,page:1,docs:[],cursor:0,ops,navigate,...extras}, scope, requestedAt);
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
    return {el,board,sync,display,calls,notices,downloads,flush,frame,delay:s=>response=s, access:v=>access=v, failDownload:v=>failDownload=v, draw(type,x,y,other){el('board-scroll').fire(type,{target:el('board-ink'),clientX:x,clientY:y,...other});}};
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


test('a pupil needs a grant for their pen and eraser; revocation drops drafts but keeps confirmed ink', async()=>{
    const f = fixture({staff:false}); f.access(true); f.sync();
    assert.equal(f.el('ink-tools').hidden,true);
    assert.equal(f.el('board-footer').hidden,true);
    f.draw('pointerdown',100,100); await f.flush();
    assert.deepEqual(f.calls,[]);
    f.sync([],{write:true});
    assert.equal(f.el('ink-tools').hidden,false);
    assert.equal(f.el('pen').disabled,false);
    assert.equal(f.el('eraser').hidden,false);
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


test('two fingers can pan and pinch without creating a pen dot; a remaining finger cannot restart the stroke', async()=>{
    const f=fixture(); f.sync();
    const t=(id)=>({pointerId:id,pointerType:'touch'});
    const initial=f.el('board-stage').style.width;
    f.draw('pointerdown',290,235,t(1));
    f.frame(125); f.draw('pointermove',287,222,t(1));
    assert.ok(f.display.paths.length, 'a single intended stroke can have an unsent preview');
    f.draw('pointerdown',570,230,t(2));
    f.draw('pointermove',180,190,t(1)); f.draw('pointermove',720,190,t(2)); f.frame(16);
    assert.ok(parseInt(f.el('board-stage').style.width)>parseInt(initial));
    assert.ok(f.el('board-scroll').scrollLeft || f.el('board-scroll').scrollTop);
    f.draw('pointerup',720,190,t(2)); f.draw('pointermove',349,240,t(1)); f.draw('pointerup',349,240,t(1));
    assert.deepEqual(f.display.paths,[]); await f.flush(); assert.deepEqual(f.calls,[]);
});

test('a two-finger view gesture restores an unshared eraser preview',async()=>{
    const f=fixture(); f.sync([drawOp('a'.repeat(32),[[900,3000],[4500,3000]])],{cursor:4});
    const t=(id)=>({pointerId:id,pointerType:'touch'});
    f.el('eraser').fire('click'); f.draw('pointerdown',220,150,t(1));
    f.frame(125); f.draw('pointermove',231,151,t(1)); assert.deepEqual(f.display.strokes,[]);
    f.draw('pointerdown',540,210,t(2));
    f.draw('pointerup',540,210,t(2)); f.draw('pointerup',231,151,t(1));
    await f.flush(); assert.deepEqual(f.calls,[]); assert.equal(f.display.strokes.length,1);
});

test('a contacting pen keeps its path and ignores simultaneous palm touches',async()=>{
    const f=fixture(); f.sync();
    const stylus={pointerType:'pen',pointerId:1}, palm={pointerType:'touch',pointerId:2};
    f.draw('pointerdown',560,200,palm); f.frame(125); f.draw('pointermove',590,220,palm);
    f.draw('pointerdown',130,150,stylus);
    f.draw('pointermove',300,280,palm); f.draw('pointermove',155,187,stylus);
    f.draw('pointerup',155,187,stylus); f.draw('pointerup',300,280,palm);
    await f.flush(); assert.deepEqual(f.calls[0].strokes[0].points,[[1300,3000],[1550,3740]]);
});

test('an admitted pupil erases fragments of their own line, never other authors or a legacy mask',async()=>{
    const f=fixture({staff:false}); f.access(true);
    const prefix='b'.repeat(16), mine=prefix+'c'.repeat(16), other='a'.repeat(32);
    f.sync([drawOp(mine,[[900,3000],[4500,3000]]),drawOp(other,[[900,3100],[4500,3100]],'#004400')],{write:true,owner:prefix,cursor:4});
    f.delay(() => Promise.resolve({ink:5}));
    f.el('eraser').fire('click'); f.draw('pointerdown',220,153); f.draw('pointerup',220,153); await f.flush();
    assert.deepEqual(f.calls[0].strokes,[{tool:'erase-stroke',targets:[mine]}]);
    assert.deepEqual(f.display.strokes,[['source-over','#004400']]);
    f.sync([],{write:false,cursor:4}); assert.equal(f.el('eraser').hidden,true);
});

test('fresh remote author label follows the revealed tip, fades quickly, and cannot survive a reset',async()=>{
    const f=fixture({staff:false}); const root=f.el('board-writers'); f.sync();
    const op={...digit,author:'student-2',live:true};
    f.sync([op],{moving:true,cursor:6}); assert.equal(root.children.length,0);
    f.frame(60); const label=root.children[0]; assert.equal(label.textContent,'نام نویسنده');
    assert.notEqual(label.style.left,'48%');
    f.frame(210); assert.equal(label.style.left,'48%'); assert.equal(label.style.top,'43%');
    await f.flush(); assert.equal(label.classList.contains('fading'),true);
    await f.flush(); assert.deepEqual(root.children,[]);
    f.sync([{...op,author:'student-1'}],{moving:true,cursor:12}); f.frame(250);
    assert.deepEqual(root.children,[],'own ink stays unlabelled');
    f.sync([op],{moving:true,cursor:6,rev:2,reset:true}); f.frame(250);
    assert.deepEqual(root.children,[],'history stays unlabelled');
});

test('a laser sweep coalesces into one existing reader request and is never written as ink',async()=>{
    const f=fixture(); f.sync();
    f.el('board-laser').fire('click'); f.draw('pointerdown',200,200);
    f.frame(50); f.draw('pointermove',400,170);
    f.frame(50); f.draw('pointermove',600,300); f.draw('pointerup',600,300);
    assert.deepEqual(structuredClone(f.board.takeAim()),{aim:[[2000,4000,100],[4000,3400,50],[6000,6000,0]]});
    assert.deepEqual(structuredClone(f.board.takeAim()),{});
    f.frame(16); assert.equal(f.el('board-laser-local-trail').children.length,2);
    f.draw('pointerdown',250,100); f.draw('pointerup',250,100); f.frame(16);
    assert.equal(f.el('board-laser-local-trail').children.length,0,'do not connect independent gestures');
    assert.deepEqual(structuredClone(f.board.takeAim()),{aim:[[2500,2000,16]]});
    f.frame(1200); assert.equal(f.el('board-laser-local').hidden,false);
    f.frame(700); assert.equal(f.el('board-laser-local').hidden,true);
    await f.flush(); assert.deepEqual(f.calls,[]); assert.deepEqual(f.display.paths,[]);
});

test('remote trail fades oldest-first and never survives a board revision or transport',()=>{
    const pupil=fixture({staff:false}); pupil.sync();
    const dot=pupil.el('board-laser-remote'), trail=pupil.el('board-laser-remote-trail');
    const cue={by:'teacher-1',points:[[1100,2400,150],[3300,5800,450],[6000,6000,800]]};
    pupil.sync([],{laser:cue}); pupil.frame(12);
    assert.equal(dot.hidden,false); assert.equal(dot.style.left,'60%');
    assert.deepEqual(trail.children.map(l=>[l.x1,l.y1,l.x2,l.y2]),[[1100,2400,3300,5800],[3300,5800,6000,6000]]);
    const [a,b]=trail.children;
    assert.ok(Number(a.style.opacity)<Number(b.style.opacity));
    pupil.frame(150); assert.equal(trail.children.length,1); assert.equal(trail.children[0].x1,3300);
    pupil.frame(645); assert.equal(dot.hidden,true); assert.deepEqual(trail.children,[]);
    pupil.sync([],{laser:cue},undefined,0); pupil.frame(30);
    assert.equal(dot.hidden,true,'a slow response does not revive a past trail');
    pupil.sync([],{laser:cue}); pupil.frame(30);
    pupil.sync([],{rev:2,reset:true}); assert.equal(dot.hidden,true); assert.deepEqual(trail.children,[]);
    pupil.sync([],{rev:2,laser:cue}); pupil.frame(30);
    pupil.sync([],{rev:2},{lesson:'L2',epoch:'E',sid:'PA_new'}); assert.equal(dot.hidden,true); assert.deepEqual(trail.children,[]);
});


test('fast movement is bounded and unsent laser samples expire without refreshing the reader',()=>{
    const f=fixture(); f.sync(); f.el('board-laser').fire('click'); f.draw('pointerdown',200,200);
    for (let n=1;n<=125;n++) { f.frame(10); f.draw('pointermove',200+n*2,200+n); }
    const {aim}=f.board.takeAim();
    assert.equal(aim.length,20); assert.deepEqual(Array.from(aim.at(-1)),[4500,6500,0]);
    assert.ok(aim.every(([, , age],i)=>age>=0 && age<1900 && (i===0 || age<=aim[i-1][2])));
    f.draw('pointermove',500,270); f.frame(860); assert.deepEqual(structuredClone(f.board.takeAim()),{});
    f.draw('pointermove',600,300); f.access(false);
    assert.deepEqual(structuredClone(f.board.takeAim()),{});
    f.board.reset(); assert.equal(f.el('board-laser-local').hidden,true);
});


test('the selected file loads automatically; idle deltas do not re-fetch, and a failed file can be retried', async()=>{
    const f=fixture({staff:false}); f.access(true);
    const scope={lesson:'L',epoch:'E',sid:'PA_student'};
    const doc={id:'large',width:600,height:800,bytes:400000,mime:'image/jpeg',pages:1};
    f.sync([],{doc,write:true},scope);
    assert.equal(f.el('board-file-receive').hidden,true);
    await f.flush();
    assert.equal(f.downloads.length,1);
    assert.equal(f.display.images,1);
    assert.equal(f.downloads[0].options.priority,'low');
    assert.deepEqual(Object.fromEntries(new URL(f.downloads[0].url).searchParams),{class:'22',lesson:'L',epoch:'E',sid:'PA_student',doc:'large'});
    f.board.sync({rev:1,cursor:1,write:false,ops:[drawOp('one',[[100,100],[200,400]])]},scope);
    assert.equal(f.el('ink-tools').hidden,true);
    assert.equal(f.el('board-stage').style.aspectRatio,'1500 / 2000');
    assert.equal(f.display.strokes.length,1);
    assert.equal(f.downloads.length,1,'an ink/permission delta does not restart the document');
    f.failDownload(true);
    f.sync([],{doc:{...doc,id:'another'},rev:2},scope); await f.flush();
    assert.equal(f.downloads[0].options.signal.aborted,true);
    assert.equal(f.el('board-file-receive').hidden,false);
    f.failDownload(false); f.el('board-load-file').fire('click'); await f.flush();
    assert.equal(f.downloads.length,3);
    assert.equal(f.el('board-file-receive').hidden,true);
    f.board.reset();
    assert.equal(f.downloads[2].options.signal.aborted,true);
});

test('the shared crop is independent of ink and resets only with the authoritative view or scope',async()=>{
    const f=fixture({staff:false}); f.access(true); f.sync([],{write:true});
    const scope={lesson:'L',epoch:'E',sid:'PA_staff'}, view={zoom:200,crop:[5000,1000,10000,7000]};
    f.board.sync({rev:1,view,cursor:0,write:true},scope);
    assert.deepEqual(structuredClone(f.board.framing()),view);
    const width=f.el('board-stage').style.width, anchor=f.el('board-stage').style.left;
    assert.ok(parseFloat(anchor)<0);
    f.el('board-scroll').fire('wheel',{ctrlKey:true,deltaY:-90});
    f.el('board-scroll').fire('keydown',{key:'+'});
    f.draw('pointerdown',140,150,{button:1}); f.draw('pointermove',200,200,{button:1});
    const touch=id=>({pointerId:id,pointerType:'touch'});
    f.draw('pointerdown',200,200,touch(2)); f.draw('pointerdown',300,300,touch(3));
    f.draw('pointermove',500,400,touch(3)); f.frame(130);
    f.draw('pointerup',500,400,touch(3)); f.draw('pointerup',200,200,touch(2));
    await f.flush();
    assert.deepEqual(f.calls,[]);
    assert.equal(f.el('board-stage').style.width,width);
    assert.equal(f.el('board-stage').style.left,anchor);
    assert.deepEqual(structuredClone(f.board.takeView()),{});
    f.board.sync({rev:1,view:{zoom:300,crop:[0,0,6000,6000]},cursor:0},{...scope,epoch:'next'});
    assert.deepEqual(structuredClone(f.board.framing()),view,'ignore a compact response without a matching document scope');
    f.sync([],{rev:1},{...scope,epoch:'next'});
    assert.equal(f.board.framing().zoom,100);
});

test('only the assigned teacher publishes viewport deltas, retrying the newest geometry until echoed',async()=>{
    const f=fixture(); f.sync();
    f.el('board-zoom-in').fire('click'); f.el('board-zoom-in').fire('click');
    const latest=structuredClone(f.board.takeView());
    assert.equal(latest.view.zoom,150);
    assert.deepEqual(structuredClone(f.board.takeView()),latest);
    f.sync([],{view:{zoom:125,crop:[0,0,10000,8000]}});
    assert.deepEqual(structuredClone(f.board.takeView()),latest,'a late response never undoes the local zoom');
    f.sync([],{view:latest.view});
    assert.deepEqual(structuredClone(f.board.takeView()),{});
    f.sync([],{rev:2,doc:{id:'new',mime:'image/jpeg',bytes:12,pages:1}});
    assert.equal(f.el('board-zoom').textContent,'۱۰۰٪');
    const manager=fixture({staff:true,navigate:false}); manager.sync();
    manager.el('page-next').fire('click'); manager.el('board-zoom-in').fire('click');
    assert.equal(manager.el('board-pages').hidden,true);
    assert.equal(manager.el('board-zoom-in').hidden,true);
    assert.deepEqual(structuredClone(manager.board.takeView()),{});
    await manager.flush(); assert.deepEqual(manager.calls,[]);
});
