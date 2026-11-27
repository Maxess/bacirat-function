// Optional packaged PHP/WASM HTTP test. MPM_TEST_PHP_WASM points to a directory
// containing node_modules/@php-wasm/{node,universal}; no external site or SFU is used.
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {createHash,createHmac} = require('node:crypto');
const {readFileSync} = require('node:fs');
const {createRequire} = require('node:module');
const {join, resolve} = require('node:path');
const test = require('node:test');
const {twoPageBoardPDF} = require('./fixtures/classroom-board-docs.cjs');

const {MPM_TEST_PHP_WASM:runtime, MPM_TEST_CLASSROOM_ZIP:archive=join(__dirname,'../artifacts/school-classroom-plugin-v0.4.25.zip')} = process.env;

test('packaged API restores board controls for the admitted manager in normal and Friday lessons', {skip:!runtime,timeout:25000}, async t=>{
    const load = createRequire(resolve(runtime,'package.json'));
    const {PHP} = load('@php-wasm/universal'), {loadNodeRuntime} = load('@php-wasm/node');
    const php = new PHP(await loadNodeRuntime('8.5',{emscriptenOptions:{processId:1}}));
    const realNow = Date.now;
    t.after(()=>{ Date.now=realNow; php.exit(); });
    const entry = name => execFileSync('unzip',['-p',archive,name]);
    const {files} = JSON.parse(entry('plugin.json'));
    php.mkdir('/classroom');
    for (const file of Object.keys(files).filter(f=>f.endsWith('.php') || f==='video-presets.json')) php.writeFile(`/classroom/${file}`,entry(file));
    php.writeFile('/fixture.php',readFileSync(join(__dirname,'fixtures/classroom-board-api.php')));
    const roles={manager:['admin',1,'PA_Manager'], unauthorized:['admin',2,'PA_Blocked'],
        pupil:['student',1,'PA_Pupil'], teacher:['teacher',1,'PA_Teacher'], deputy:['teacher',2,'PA_Deputy']};
    const origin='https://school.example.test', $_SERVER={HTTPS:'on',HTTP_HOST:'school.example.test'};
    const headers={'X-Classroom-CSRF':'fixture-token','Content-Type':'application/json',Origin:origin};
    const crop={zoom:200,crop:[5000,1200,10000,7200]};
    const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGDkAAAAASUVORK5CYII=','base64');
    const pdf=twoPageBoardPDF();
    const strokes=[{tool:'pen',id:'c'.repeat(32),color:'#0099bb',size:4,points:[[6400,4000]]}];

    for (const demo of [false,true]) await t.test(demo?'admin-opened Friday with no teacher assignment':'scheduled lesson with teacher handoff',async()=>{
        const base=`<?php $demo=${demo}; `;
        let now=Date.parse(demo?'2026-11-20T08:30:00Z':'2026-11-18T08:30:00Z'), board=1;
        Date.now=()=>now;
        const seed=async mode=>{
            const response=await php.run({code:`${base}$seed='${mode}'; require '/fixture.php';`,$_SERVER});
            assert.equal(response.httpStatusCode,200,response.errors);
            return response.json;
        };
        const {lesson,epoch,teacher}=await seed('open');
        assert.equal(teacher,demo?0:1);
        const bind = (role,extra) => ({class:22,lesson,epoch,sid:roles[role][2],...extra});
        async function reply(role,file,options,expected,action) {
            const [type,id]=roles[role];
            const code=`${base}require '/fixture.php'; $_SESSION=['${type}_id'=>${id}]; require '/classroom/${file}.php';`;
            const response=await php.run({code,$_SERVER,...options});
            assert.equal(response.httpStatusCode,expected,`${role} ${action}: ${response.errors}${response.text}`);
            return response;
        }
        async function api(role,action,extra={},expect=200,token=headers['X-Classroom-CSRF']) {
            const body=Buffer.from(JSON.stringify(bind(role,{action,board,compact:true,...extra})));
            return (await reply(role,'api',{method:'POST',headers:{...headers,'X-Classroom-CSRF':token},body},expect,action)).json;
        }
        async function upload(role,bytes,type,name,pages,expect=200) {
            const form=new FormData();
            for (const [k,v] of Object.entries(bind(role,{board,pages}))) form.set(k,String(v));
            form.set('file',new Blob([bytes],{type}),name);
            const request=new Request(origin,{method:'POST',body:form});
            return (await reply(role,'api',{method:'POST',headers:{...headers,'Content-Type':request.headers.get('content-type')},
                body:new Uint8Array(await request.arrayBuffer())},expect,'upload')).json;
        }
        const manager=await api('manager','status',{board:0});
        assert.equal(manager.admitted,true);
        assert.equal(manager.board.navigate,true,'the admitted manager sees the restored controls');
        assert.equal(manager.wakeControl,false,'presenting does not confer assigned-teacher privileges');
        assert.equal(manager.recording.start,true);
        await api('manager','set-wake',{mode:'system'},403);
        if (demo) await api('manager','start-recording',{},409); // No recording group until configured.
        await api('unauthorized','board-sync',{},403);
        await api('deputy','board-sync',{view:crop},403);
        await api('manager','board-sync',{sid:'PA_Stale',view:crop},409);
        await api('manager','board-sync',{epoch:'0'.repeat(24),view:crop},409);
        await api('manager','board-sync',{view:crop},403,'invalid');
        assert.deepEqual((await api('manager','board-sync',{view:crop})).board.view,crop);

        const imageUpload=await upload('manager',png,'image/png','diagram.png',1);
        assert.equal(imageUpload.boardRev,++board);
        const image=(await api('pupil','board-sync',{board:0})).board.doc;
        assert.equal(image.mime,'image/png');
        const imageGet=await reply('pupil','content',{method:'GET',relativeUri:'/content.php?'+new URLSearchParams(bind('pupil',{doc:image.id}))},200,'content');
        assert.deepEqual(Buffer.from(imageGet.bytes),png);
        const pdfUpload=await upload('manager',pdf,'application/pdf','lesson.pdf',2);
        assert.equal(pdfUpload.boardRev,++board);
        const doc=(await api('pupil','board-sync',{board:0})).board.doc.id;
        assert.equal((await api('manager','board-page',{doc:image.id,page:1})).boardRev,++board);
        assert.equal((await api('pupil','board-sync',{board:0})).board.doc.id,image.id,'manager can select an existing file');
        const page=await api('manager','board-page',{doc,page:2});
        assert.equal(page.boardRev,++board);
        const current=(await api('pupil','board-sync',{board:0})).board;
        assert.equal(current.doc.id,doc); assert.equal(current.page,2); assert.equal(current.view,undefined);

        const grant={feature:'board',enabled:true,identity:'student-1'};
        await api('pupil','board-ink',{strokes},403);
        await api('manager','set-access',grant);
        assert.equal((await api('pupil','board-ink',{strokes})).ink,1);
        await api('pupil','board-page',{doc: image.id,page:1},403);
        await api('pupil','board-sync',{view:crop},403);
        await upload('pupil',png,'image/png','diagram.png',1,403);
        await api('manager','set-access',{...grant,enabled:false});
        await api('pupil','board-ink',{strokes},403);
        await api('manager','set-access',{feature:'board',enabled:true});
        assert.equal((await api('pupil','board-sync')).board.write,true);

        await api('manager','board-sync',{view:crop});
        now+=1000;
        await seed('teacher');
        if (demo) {
            assert.equal((await api('manager','board-sync')).board.navigate,true);
            assert.equal((await api('teacher','board-sync')).board.navigate,undefined);
            const room = `test-${'a'.repeat(20)}-v4-c22-${epoch}`;
            const event = Buffer.from(JSON.stringify({event:'participant_joined', room:{name:room}}));
            const part = value=>Buffer.from(JSON.stringify(value)).toString('base64url');
            const payload = `${part({alg:'HS256',typ:'JWT'})}.${part({iss:'k'.repeat(16),exp:now/1000+300,sha256:createHash('sha256').update(event).digest('base64')})}`;
            const signed = `${payload}.${createHmac('sha256','s'.repeat(48)).update(payload).digest('base64url')}`;
            assert.match((await reply('pupil','events',{method:'GET'},405,'webhook GET')).json.message,/POST/);
            const callback = {method:'POST',headers:{'Content-Type':'application/webhook+json',Authorization:signed},body:event};
            await reply('pupil','events',{...callback,body:Buffer.concat([event,Buffer.from(' ')])},403,'modified native event');
            await reply('pupil','events',callback,200,'signed POST');

            await seed('record-ready');
            const worker = '9'.repeat(32);
            const recorder = async (action,data,expected=200) => (await reply('pupil','recording-api',{
                method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${'r'.repeat(48)}`},
                body:Buffer.from(JSON.stringify({action,protocol:2,worker,...data})),
            },expected,action)).json;
            await api('manager','start-recording',{},503); // Link alone doesn't prove worker readiness.
            await recorder('plan',{available:true});
            await api('pupil','start-recording',{},403);
            await api('deputy','start-recording',{},403);
            const started = (await api('manager','start-recording')).recording;
            assert.equal(started.notice,'preparing');
            assert.deepEqual((await api('pupil','status')).recording,{notice:'preparing'});
            assert.equal((await recorder('poll',{id:lesson})).command,'hold');
            now += 5000;
            await seed('teacher'); // Fresh fixture presence; the worker still enforces the real lease/scope.
            const permit = await recorder('poll',{id:lesson});
            assert.equal(permit.command,'record');
            assert.equal(permit.room,room);
            assert.ok(permit.expires <= now/1000 + 26*3600);
            await recorder('poll',{id:lesson,running:room});
            assert.deepEqual((await api('pupil','status')).recording,{notice:'live'});
            await api('manager','record-source',{source:'board',enabled:true});
            await recorder('board',{id:lesson,room:'stale'},409);
            const view = await recorder('board',{id:lesson,room});
            assert.equal(view.board.write,false);
            assert.equal(view.archive.board,true);
        } else {
            const handoff=(await api('teacher','board-sync')).board;
            assert.equal(handoff.navigate,true);
            assert.deepEqual(handoff.view,crop,'teacher inherits the existing shared framing');
            assert.equal(handoff.doc,undefined,'no document reload during a compact handoff');
            assert.equal((await api('manager','board-sync')).board.navigate,undefined);
            await api('manager','board-page',{doc,page:1},403);
            await api('manager','board-sync',{view:{zoom:100,crop:[0,0,10000,10000]}},403);
            await upload('manager',png,'image/png','diagram.png',1,403);
            await api('manager','set-access',grant,403);
            await api('manager','set-access',{feature:'board',enabled:false});
            assert.equal((await api('pupil','board-sync')).board.write,false,'the following manager can still revoke');
            await seed('record');
            const filter = {source:'board',enabled:true};
            assert.deepEqual((await api('teacher','status')).recording.archive,{board:false,screen:null});
            for (const role of ['manager','pupil']) assert.equal((await api(role,'status')).recording.archive,undefined);
            assert.equal((await api('teacher','status',{sid:''})).recording.archive,undefined);
            await api('pupil','record-source',filter,403);
            await api('manager','record-source',filter,403);
            await api('teacher','record-source',filter,403,'invalid');
            await api('teacher','record-source',{...filter,epoch:'0'.repeat(24)},409);
            await api('teacher','record-source',{...filter,sid:'PA_Old'},409);
            assert.deepEqual((await api('teacher','record-source',filter)).archive,{board:true,screen:null});
            assert.deepEqual((await api('teacher','record-source',{source:'screen',enabled:true,publisherSid:'PA_Pupil',track:'TR_Class'})).archive,
                {board:true,screen:'TR_Class'});
            await api('teacher','board-sync',{aim:[[6345,3255,0]]});
            const request = body=>({method:'POST', headers:{...headers,Authorization:`Bearer ${'r'.repeat(48)}`},body:Buffer.from(JSON.stringify(body))});
            const room=`test-${'a'.repeat(20)}-v4-c22-${epoch}`;
            const command={action:'board',protocol:2,id:lesson,room,worker:'9'.repeat(32),board,ink:1,compact:true};
            await reply('pupil','recording-api',request({...command,protocol:1}),400,'old live encoding protocol cannot be enabled');
            const recording=await reply('pupil','recording-api',request(command),200,'worker board');
            assert.deepEqual(recording.json.archive,{board:true,screen:'TR_Class',screenOwner:{identity:'student-1',sid:'PA_Pupil'}});
            assert.deepEqual(Object.keys(recording.json.board.laser),['points']);
            assert.deepEqual(recording.json.board.laser.points[0].slice(0,2),[6345,3255]);
            assert.equal(recording.json.board.navigate,undefined);
            await reply('pupil','recording-api',request({...command,room:'old-room'}),409,'stale worker room');
            await reply('pupil','recording-api',request({...command,action:'record-source',...filter}),400,'worker cannot select layout');

            await api('teacher','leave');
            assert.equal((await api('manager','board-sync')).board.navigate,true,'a verified teacher departure immediately restores control');
            assert.deepEqual((await api('manager','record-source',{...filter,enabled:false})).archive,{board:false,screen:'TR_Class'});
        }
        if (entry('activity.php').includes(Buffer.from('function cl_board_writer('))) {
            const v1={wire:1};
            const start=(await api('manager','board-sync',{...v1,board:0})).board;
            assert.equal(start.checkpoint,true); assert.equal(start.writeSeq,0);
            const batch={...v1,seq:1,strokes:[{...strokes[0],part:0,end:true}]};
            const ack=await api('manager','board-ink',batch);
            assert.equal(ack.ink,start.head+1);
            assert.deepEqual(await api('manager','board-ink',batch),ack);
            await api('manager','board-ink',{...v1,seq:3,strokes:[{tool:'undo'}]},409);
            await api('manager','board-ink',{...v1,seq:2,strokes:[{tool:'undo'}]});
            const seek=(await api('pupil','board-sync',{...v1,ink:ack.ink})).board;
            assert.equal(seek.rev,board); assert.equal(seek.ops[0].tool,'undo'); assert.equal(seek.ops[0].from,ack.ink);
            const legacy=(await api('pupil','board-sync')).board;
            assert.equal(legacy.rev,board+1); assert.ok(legacy.reset);
            assert.ok(legacy.ops.every(op=>op.tool!=='undo'));
        }
        await api('manager','set-access',{feature:'board',enabled:false});
        assert.equal((await api('pupil','board-sync')).board.write,false);
    });
});
