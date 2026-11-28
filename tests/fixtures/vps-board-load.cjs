// Keep actual classroom.js and the PHP/SQL request path in the loop. Fake DOM,
// signaling, and a 15 ms lossy-notice path; do not infer real browser/WAN costs.
const assert = require('node:assert/strict');
const {readFileSync, writeFileSync} = require('node:fs');
const {setTimeout:delay} = require('node:timers/promises');
const {execFileSync} = require('node:child_process');
const {fixture} = require('./classroom-ui.cjs');
const [asset, out, serverPid] = process.argv.slice(2);
const endpoint = 'http://127.0.0.1:18392/board';
const rooms = [], failures = [], report = {};
let phase = '', lost = false, running = true;
const classroomScript = readFileSync(asset,'utf8');
const clk = +execFileSync('getconf',['CLK_TCK']).toString();
const parents = p => readFileSync(`/proc/${p}/task/${p}/children`,'utf8').trim().split(/\s+/).filter(Boolean).map(Number);
const phpPids = [+serverPid, ...parents(+serverPid)];
assert.ok(clk > 0 && phpPids.length >= 2);
const readProc = () => {
    let ticks = 0, rss = 0;
    for (const pid of phpPids) {
        const parts = readFileSync(`/proc/${pid}/stat`,'utf8').split(') ').at(-1).split(' ');
        ticks += +parts[11] + +parts[12];
        const info = readFileSync(`/proc/${pid}/status`,'utf8');
        rss += +info.match(/^VmRSS:\s+(\d+)/m)[1];
    }
    return {ticks,rssMiB:rss/1024};
};
const percent = (items, q) => items.length ? [...items].sort((a,b)=>a-b)[Math.ceil(q*items.length)-1] : null;
const round = n => n === null ? null : +n.toFixed(3);
const strokes = n => [{tool:'pen',id:n.toString(16).padStart(32,'0'),part:0,end:true,size:24,color:'#ed1164',
    points:Array.from({length:12},(_,i)=>[1600+430*i,2300+n%22*225+Math.round(190*Math.sin(i/1.7))])}];

async function call(input, seat, signal) {
    const window = report[phase];
    try {
        const response = await fetch(endpoint,{method:'POST',body:JSON.stringify(input),signal,
            headers:{'Content-Type':'application/json','X-QA-Seat':String(seat)}});
        const text = await response.text();
        assert.equal(response.status,200,`${input.action} class ${input.class}, seat ${seat}: ${text}`);
        if (window) {
            window.requests[input.action] = (window.requests[input.action] ?? 0)+1;
            window.sql += +response.headers.get('X-QA-SQL');
            window.bodyBytes += Buffer.byteLength(text);
            window.lock.push(+response.headers.get('X-QA-Lock-Ms'));
            window.app.push(+response.headers.get('X-QA-App-Ms'));
        }
        const body = JSON.parse(text);
        return {ok:true,status:200,json:async()=>body};
    } catch (error) {
        if (running) failures.push(error.message);
        throw error;
    }
}
async function until(predicate,time,reason) {
    const end = performance.now()+time;
    while (!predicate()) {
        if (performance.now() > end) throw Error(reason);
        await delay(15);
    }
}
function room(c) {
    const r = {seq:0, peers:[], events:[], receivers:new Set()};
    const owner = `teacher-${c}`;
    for (let i=0;i<=20;i++) {
        const f = fixture(!i,0,{realTime:true, classroomScript, classId:c, identity:i ? `student-${(c-1)*20+i}` : owner,
            peers:i ? [owner] : [], permissions:id=>({canPublishData:id===owner,canPublish:false}), http:(input,signal)=>call(input,i,signal),
            onBoard(view) {
                if (i && view?.rev === 1) for (const e of r.events) if (view.cursor >= e.ink && !e.observed.has(i)) {
                    e.observed.add(i); e.wait.push(performance.now()-e.at);
                }
            },
            onPublish(packet,opts) {
                if (lost || opts.topic !== 'baci.board.head.v1') return;
                if (report[phase]) report[phase].notices++;
                const deliver = () => {
                    for (const tab of r.peers.slice(1)) {
                        const peer=tab.room?.remoteParticipants.get(owner);
                        if (peer?.identity === owner && peer.permissions?.canPublishData === true) {
                            tab.room.emit('DataReceived',packet,peer,1,opts.topic);
                            r.receivers.add(peer.identity + ':' + tab.room.localParticipant.identity);
                        }
                    }
                };
                setTimeout(deliver,15);
            }});
        r.peers.push(f);
    }
    return r;
}
async function write(r) {
    const seq = ++r.seq;
    const e = {ink:seq*12, at:performance.now(), observed:new Set(), wait:[]};
    r.events.push(e);
    const ack=await r.peers[0].boardSend('board-ink',{board:1,seq,strokes:strokes(seq)});
    assert.equal(ack.seq,seq); assert.equal(ack.ink,e.ink);
    return e;
}
async function measure(name, ms, activity) {
    const w = report[name] = {requests:{},sql:0,bodyBytes:0,notices:0,lock:[],app:[]};
    const before=readProc();
    const start=performance.now(); phase=name;
    const events = await activity?.(start) ?? [];
    await delay(Math.max(0,ms-(performance.now()-start)));
    const elapsed=(performance.now()-start)/1000, after=readProc();
    phase='';
    assert.ok(!failures.length, failures.slice(0,4).join(' | '));
    const expected=events.length*20, waits=events.flatMap(e=>e.wait);
    assert.equal(waits.length,expected,`${name}: all committed cursors observed by all 140 pupils`);
    const requests=Object.values(w.requests).reduce((a,b)=>a+b,0);
    return {tabs:147,seconds:round(elapsed),requests:w.requests,requests_per_s:round(requests/elapsed),
        sql_per_s:round(w.sql/elapsed),body_bytes_per_s:round(w.bodyBytes/elapsed),head_publishes:w.notices,
        batch_ink_commits:events.length,cursor_observations:expected,ink_p50_ms:round(percent(waits,.5)),
        ink_p95_ms:round(percent(waits,.95)),ink_p99_ms:round(percent(waits,.99)),ink_max_ms:waits.length?round(Math.max(...waits)):null,
        lock_entry_p95_ms:round(percent(w.lock,.95)),php_app_p95_ms:round(percent(w.app,.95)),
        php_cpu_cores:round((after.ticks-before.ticks)/clk/elapsed),php_rss_sum_mib_at_end:round(after.rssMiB)};
}
async function run() {
    for (let c=1;c<=7;c++) { rooms.push(room(c)); await delay(125); }
    await until(()=>rooms.every(r=>r.peers.every(p=>p.position?.rev===1)),9000,'not all tabs admitted');
    await delay(5500);
    const notices = rooms.map(r=>r.peers[0].position?.notice === true);
    assert.ok(notices.every(n=>n) || notices.every(n=>!n),'head capability must be negotiated for all seven test rooms or none');
    if (notices[0]) assert.ok(rooms.every(r=>r.receivers.size===20),'all seven fanout groups must actually receive the supported notices');
    const idle=await measure('idle',18_000);
    const writing=await measure('writing',28_000,async start=>{
        const events=[];
        for(let i=0;i<24;i++) {
            // Two writes 200 ms apart in each 2 s period. Fairly stagger rooms.
            const time=2000*Math.floor(i/2)+(i%2)*200;
            await delay(Math.max(0,start+time-performance.now()));
            events.push(...await Promise.all(rooms.map(async (r,k)=>{ await delay(k*31); return write(r); })));
        }
        return events;
    });
    await delay(2400);
    const missed=await measure('no_hint',6500,async()=>{
        lost=true; // First commit starts with a warm notice path, then all datagrams stop.
        return Promise.all(rooms.map(write));
    });
    assert.ok((writing.ink_p99_ms ?? Infinity)<1200 && (missed.ink_max_ms ?? Infinity)<3200,'ink recovery deadline');
    return {idle,writing,missed};
}
run().then(result=>{running=false; writeFileSync(out,JSON.stringify(result,null,2)+'\n'); process.exit(0);},error=>{
    running=false; console.error(error); process.exit(1);
});
