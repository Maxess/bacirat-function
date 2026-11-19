<?php
declare(strict_types=1);
require __DIR__ . '/../school-addon/upload/online-classroom/activity.php';
require __DIR__ . '/../school-addon/upload/online-classroom/reports.php';

function must(bool $ok, string $why): void { if (!$ok) throw new RuntimeException($why); }
function denied(int $status, callable $call): void {
    try { $call(); } catch (ClassroomError $error) { must($error->status === $status, 'incorrect denial: ' . $error->status . ' ' . $error->getMessage()); return; }
    throw new RuntimeException('expected refusal');
}
$dsn = getenv('MPM_TEST_DSN') ?: 'sqlite::memory:';
$mysql = $dsn !== 'sqlite::memory:';
if ($mysql && !preg_match('/^mysql:host=127\.0\.0\.1;port=[0-9]+;dbname=mpm_fixture_[a-z0-9_]+;charset=utf8mb4$/D', $dsn)) throw new RuntimeException('Disposable database required');
$db = new PDO($dsn, getenv('MPM_TEST_USER') ?: null, getenv('MPM_TEST_PASSWORD') ?: null, [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,PDO::ATTR_EMULATE_PREPARES=>false]);
if ($mysql && $db->query('SHOW TABLES')->fetchColumn()) throw new RuntimeException('Use an empty disposable database');
$table = 'mtg_plug_store_' . str_repeat('a', 32);
$db->exec("CREATE TABLE `$table` (bucket VARCHAR(80), item VARCHAR(120), payload MEDIUMTEXT, PRIMARY KEY(bucket,item))");
foreach ([
    "CREATE TABLE academic_years (id INT PRIMARY KEY,year_name VARCHAR(32),is_default INT,status INT)",
    "CREATE TABLE settings (key_name VARCHAR(100),key_value VARCHAR(100))",
    "CREATE TABLE classes (id INT PRIMARY KEY,name VARCHAR(100),academic_year VARCHAR(32))",
    "CREATE TABLE students (id INT PRIMARY KEY,first_name VARCHAR(100),last_name VARCHAR(100),class_name VARCHAR(100),academic_year VARCHAR(32),status VARCHAR(15))",
    "CREATE TABLE teachers (id INT PRIMARY KEY,full_name VARCHAR(100),academic_year VARCHAR(32),status INT,is_deputy INT)",
    "CREATE TABLE admins (id INT PRIMARY KEY,name VARCHAR(100),role VARCHAR(100),permissions VARCHAR(100),status INT)",
    "CREATE TABLE class_schedules (id INT PRIMARY KEY,teacher_id INT,class_name VARCHAR(100),academic_year VARCHAR(32),day_of_week VARCHAR(32),period_num VARCHAR(32),subject_name VARCHAR(100))",
    "CREATE TABLE bale_bot_users (bale_chat_id VARCHAR(80),student_id INT)",
    "CREATE TABLE telegram_bot_users (telegram_chat_id VARCHAR(80),student_id INT)",
    "INSERT INTO academic_years VALUES (1,'۱۴۰۵-۱۴۰۶',1,1),(2,'1404/1405',0,1)",
    "INSERT INTO settings VALUES ('current_academic_year','1405/1406'),('bale_bot_token','987:TestbaleXXXX'),('telegram_bot_token','456:TesttgXXXX')",
    "INSERT INTO classes VALUES (10,'هفتم ۱','1405/1406'),(20,'هفتم۲','1405/1406'),(30,'هفتم۱','1404/1405')",
    "INSERT INTO students VALUES (1,'اول','مجاز','هفتم1','1405/1406','active'),(2,'دوم','مجاز','هفتم2','1405/1406','active'),
       (3,'قدیمی','','هفتم1','1404/1405','active'),(4,'غایب','','هفتم ۱','1405/1406','active'),(5,'غیرفعال','','هفتم1','1405/1406','inactive')",
    "INSERT INTO teachers VALUES (1,'دبیر ریاضی','1405/1406',1,0),(2,'دبیر دیگر','1405/1406',1,0),(3,'ناظم','1405/1406',1,1),(4,'دبیر سال پیش','1404/1405',1,0)",
    "INSERT INTO admins VALUES (1,'مدیر','super_admin','[]',1),(2,'ناظر عمومی','observer','[]',1),(3,'مدیر آموزشی','edu_admin','[\"manage_classes\"]',1)",
    "INSERT INTO class_schedules VALUES (11,1,'هفتم1','1405/1406','چهار شنبه','زنگ ۱','ریاضی'),(12,2,'هفتم2','1405/1406','چهارشنبه','زنگ ۱','علوم'),
       (13,2,'هفتم1','1405/1406','چهارشنبه','زنگ ۲','علوم'),(14,1,'هفتم1','1405/1406','شنبه','زنگ ۱','ریاضی'),(15,4,'هفتم1','1404/1405','چهارشنبه','زنگ ۱','ورزش')",
    "INSERT INTO bale_bot_users VALUES ('111',1),('111',1),('-222',1),('333',4)",
    "INSERT INTO telegram_bot_users VALUES ('444',1),('555',1),('666',2)",
] as $statement) $db->exec($statement);
$s = new ClassroomStore($db, $table);
$cfg = ['room_prefix'=>'test-' . str_repeat('a', 20),'site_origin'=>'https://school.example.test','signal_url'=>'wss://media.example.test',
    'api_key'=>str_repeat('a',24),'api_secret'=>str_repeat('b',64),'max_participants'=>4,
    'notify_bale'=>true,'notify_telegram'=>true,'notify_rubika'=>true,'rubika_token'=>str_repeat('c',24)];
$t = (new DateTimeImmutable('2026-11-18 08:30:00', new DateTimeZone('Asia/Tehran')))->getTimestamp();
$year = cl_current_year($db);
must($year === '1405/1406', 'active master year normalized');
$db->exec('UPDATE academic_years SET is_default=0');
must(cl_current_year($db) === $year, 'explicit imported current-year setting matches exactly one active master year');
$db->exec('UPDATE academic_years SET is_default=1');
denied(409, fn()=>cl_current_year($db));
$db->exec('UPDATE academic_years SET is_default=0 WHERE id=2');
$classes = cl_classes($db, $year);
must(array_keys($classes) === [10,20], 'current default only');
$db->exec("INSERT INTO classes VALUES (31,'هفتم۱','1405/1406')");
must(array_keys(cl_classes($db,$year)) === [20], 'ambiguous class name fails closed');
$db->exec('DELETE FROM classes WHERE id=31');
$student = cl_school_user($db, ['student_id'=>1], $year);
$teacher = cl_school_user($db, ['teacher_id'=>1], $year);
$other = cl_school_user($db, ['teacher_id'=>2], $year);
$deputy = cl_school_user($db, ['teacher_id'=>3], $year);
$manager = cl_school_user($db, ['admin_id'=>3], $year);
foreach ([['student_id'=>3],['student_id'=>5],['admin_id'=>2],['teacher_id'=>1,'admin_id'=>1]] as $bad) denied(403, fn()=>cl_school_user($db,$bad,$year));
$first = cl_class_for($db,$classes,10,$student);
denied(403,fn()=>cl_class_for($db,$classes,20,$student));
must(cl_access_class($db,$classes[10],$other) && !cl_access_class($db,$classes[20],$teacher), 'teacher class from assignment, not display name');
must(array_keys(cl_today_slots($db,$first,$teacher,$t)) === [11] && array_keys(cl_today_slots($db,$first,$deputy,$t)) === [11,13], 'Tehran weekday, real schedule id and deputy');

$db->exec("INSERT INTO class_schedules VALUES (91,2,'هفتم1','1405/1406','چهارشنبه','۰۱','علوم')");
must(!cl_get_slot($db,$first,11,$t) && !cl_get_slot($db,$first,91,$t) && array_keys(cl_today_slots($db,$first,$deputy,$t)) === [13], 'conflicting rows for the same bell are rejected');
$db->exec('DELETE FROM class_schedules WHERE id=91');

$runtime = null; $sfus = [];
$ctx = ['write_runtime'=>static function ($value) use (&$runtime) { $runtime = $value; }];
$rpc = static function ($method,$room,$payload) use (&$runtime,&$sfus,$s,$cfg): array {
    if ($method === 'CreateRoom') {
        must($runtime === 'scheduled-v1' && cl_active($s,cl_room_class($cfg,$room))['room'] === $room, 'room journal before request');
        must($payload['maxParticipants']===4, 'pilot cap retained');
        $sfus[$room]=true;
    } elseif ($method === 'DeleteRoom') { unset($sfus[$room]); }
    return [];
};
$open = fn($actor,$slot,$n,$class=null) => cl_open_lesson($s,$ctx,$cfg,$class??$first,$actor,['slot'=>$slot],$n,$rpc);
$tick = fn($rooms,$n) => cl_watch_tick($s,$cfg,$rooms,$n,$n)['drop'];
$bind = fn($l,$sid) => ['lesson'=>$l['id'],'epoch'=>cl_epoch($l),'sid'=>$sid];
$pulse = function ($c,$actor,$sid,$n) use ($s,$bind) { $l=cl_active($s,$c); cl_touch($s,$l,$actor,$bind($l,$sid),$n); };
denied(503,fn()=>$open($teacher,11,$t));
$raw = json_encode(['watch_version'=>2,'at'=>$t,'prefix'=>$cfg['room_prefix'],'rooms'=>[]]);
$mac = hash_hmac('sha256', "school-classroom-watch-v1\n{$cfg['site_origin']}\n" . $raw, $cfg['api_secret']);
must(cl_watch_auth($cfg,$raw,$mac,json_decode($raw,true),$t) === [], 'signed machine body and exact site');
denied(403,fn()=>cl_watch_auth($cfg,$raw.' ',$mac,json_decode($raw,true),$t));
denied(403,fn()=>cl_watch_auth($cfg,$raw,$mac,json_decode($raw,true),$t+11));
$tick([],$t);
denied(409,fn()=>$tick([],$t));
denied(403,fn()=>$open($student,11,$t));
denied(403,fn()=>$open($teacher,14,$t));
denied(403,fn()=>$open($other,11,$t));
$a=$open($teacher,11,$t);
$b=$open($other,12,$t,$classes[20]);
$r=$a['room']; $r2=$b['room'];
must($a['phase']==='starting' && $r!==$r2 && !$a['started'], 'independent transports arrive in waiting');
denied(409,fn()=>cl_ticket($cfg,$a,$student,$bind($a,'PA_Student'),$t));
denied(409,fn()=>cl_check_live($a,$teacher,$bind($a,'PA_Teacher'),$t));
denied(409,fn()=>cl_ticket($cfg,$a,$teacher,$bind($a,'PA_Teacher'),$t));
$teacherWire=['teacher-1'=>'PA_Teacher'];
$studentWire=['student-1'=>'PA_Student'];
$managerWire=['admin-3'=>'PA_Manager'];
$otherWire=['teacher-2'=>'PA_Other'];
$rooms=fn($aa,$bb=null) => [$r=>$aa,$r2=>$bb??[]];
$pulse(10,$student,'PA_Student',$t+3);
$policy = cl_watch_tick($s,$cfg,$rooms($studentWire),$t+3,$t+3);
$a = cl_active($s,10);
must($a['phase']==='starting' && $policy['permit']->$r === 'starting', 'only staff admission before confirmed supervision');
$ticket = cl_ticket($cfg,$a,$teacher,$bind($a,'PA_Teacher'),$t+3);
must($ticket['epoch'] === cl_epoch($a), 'staff ticket after observer confirms starting generation');
denied(409,fn()=>cl_ticket($cfg,$a,$student,$bind($a,'PA_Student'),$t+3));
$pulse(10,$teacher,'PA_Teacher',$t+6);
$pulse(20,$other,'PA_Other',$t+6);
$tick($rooms($teacherWire,$otherWire),$t+6);
$a=cl_active($s,10); must($a['phase']==='live' && $a['active_seconds']===0, 'real moderator, no time counted in wait');
cl_ticket($cfg,$a,$student,$bind($a,'PA_Student'),$t+6);
denied(409,fn()=>cl_check_live($a,$student,$bind($a,'PA_Fake'),$t+6));
denied(409,fn()=>cl_check_live($a,$other,$bind($a,'PA_Teacher'),$t+6));
$pulse(10,$student,'PA_Student',$t+8);
$pulse(10,$manager,'PA_Manager',$t+8);
$tick($rooms($teacherWire+$studentWire+$managerWire,$otherWire),$t+9);
$active=cl_active($s,10);
cl_check_live($active,$student,$bind($active,'PA_Student'),$t+9);
cl_chat($s,$active,$student,'<b>متن است</b>',$t+9);
denied(429,fn()=>cl_chat($s,$active,$student,'spam',$t+10));
$stroke=['tool'=>'pen','color'=>'#0055aa','size'=>4,'points'=>[[420,200],[9000,9800]]];
cl_board_action($s,$active,['board'=>1,'strokes'=>[$stroke]],'board-ink');
$sync=cl_activity_sync($s,$active,$student,[]);
must($sync['board']['ops']==[$stroke] && $sync['chat'][0]['text']==='<b>متن است</b>' && !$sync['board']['docs'], 'newcomer gets available page and chat as plain text');
must(cl_activity_sync($s,$active,$student,['board'=>1,'ink'=>1,'chat'=>1])['board']['ops']===[], 'incremental ink updates');
denied(400,fn()=>cl_board_action($s,$active,['board'=>1,'strokes'=>[array_replace($stroke,['points'=>[[99000,1]]])]],'board-ink'));
$meta=cl_activity($s,$a['id']); $doc=str_repeat('1',32); $meta['docs'][$doc]=['id'=>$doc,'pages'=>2317,'name'=>'test.pdf','bytes'=>256,'mime'=>'application/pdf'];
$s->put('activity',$a['id'],$meta);
cl_board_action($s,$active,['board'=>1,'doc'=>$doc,'page'=>2317],'board-page');
$sync=cl_activity_sync($s,$active,$student,['board'=>1,'ink'=>1]);
must($sync['board']['page']===2317 && $sync['board']['reset'] && $sync['board']['cursor']===0, 'arbitrary PDF page and independent strokes');
cl_board_action($s,$active,['board'=>2,'doc'=>null,'page'=>1],'board-page');
must(cl_activity_sync($s,$active,$student,[])['board']['ops']==[$stroke], 'return to separately annotated page');
cl_board_action($s,$active,['board'=>3],'board-clear');
must(cl_activity_sync($s,$active,$student,['board'=>3,'ink'=>1])['board']['cursor']===0, 'erase only drawing layer');

$pulse(10,$student,'PA_Student',$t+11);
$tick($rooms($studentWire+$managerWire,$otherWire),$t+12);
$a=cl_active($s,10);
must($a['active_seconds']===6 && $a['seconds']['student-1']===3 && $a['phase']==='live', 'second authorized moderator keeps room open');
$pulse(20,$other,'PA_Other',$t+14);
must($tick($rooms($studentWire,$otherWire),$t+15) === [$r], 'no browser event needed to order server-side disconnection after last staff drops');
$a=cl_active($s,10);
must($a['phase']==='paused' && $a['active_seconds']===6 && cl_active($s,20)['phase']==='live', 'pause stops class and attendance and leaves the other classroom alone');
denied(409,fn()=>cl_check_live($a,$student,$bind($a,'PA_Student'),$t+15));
denied(409,fn()=>cl_ticket($cfg,$a,$student,$bind($a,'PA_Student'),$t+15));
denied(409,fn()=>$open($teacher,11,$t+15)); // Must confirm old transport is gone first.
unset($sfus[$r]);
$tick([$r2=>$otherWire],$t+18);
$a2=$open($teacher,11,$t+19);
$r3=$a2['room'];
must($a2['id']===$a['id'] && $r3!==$r && $a2['seconds']['student-1']===3, 'resume same lesson and counters with a new media room');
denied(409,fn()=>cl_ticket($cfg,$a2,$teacher,$bind($a,'PA_Teacher'),$t+19));
$pulse(10,$teacher,'PA_NewTeacher',$t+20);
$pulse(10,$student,'PA_NewStudent',$t+20);
$newW=['teacher-1'=>'PA_NewTeacher','student-1'=>'PA_NewStudent'];
$tick([$r3=>$newW,$r2=>$otherWire],$t+21);
$tick([$r3=>$newW],$t+24);
must(cl_active($s,10)['seconds']['student-1']===6, 'resumed interval counted once');
$pulse(10,$teacher,'PA_NewTeacher',$t+40);
$pulse(10,$student,'PA_NewStudent',$t+40);
$policy = cl_watch_tick($s,$cfg,[$r3=>$newW],$t+41,$t+41);
must($policy['drop']===[$r3] && !(array)$policy['permit']
    && cl_active($s,10)['seconds']['student-1']===6, 'unsampled gap not attributed, expired transport revoked');
$cl=cl_active($s,10);
cl_close_lesson($s,$cfg,$cl,$t+42,$rpc);
must(!cl_active($s,10) && !isset($sfus[$r3]) && $s->get('activity',$cl['id'])===null, 'close and purge activity');
$rpt=cl_report_detail($s,$teacher,$cl['id']);
must($rpt['active_seconds']===9 && $rpt['seconds']['student-1']===6 && isset($rpt['roster']['student-4']) && !isset($rpt['seconds']['student-4']), 'report retains absence as zero and accurate denominator');
must(cl_report_detail($s,$deputy,$cl['id'])===$rpt && cl_report_detail($s,$manager,$cl['id'])===$rpt && cl_report_detail($s,$other,$cl['id'])===null, 'historical report scopes');
denied(403,fn()=>cl_report_detail($s,$student,$cl['id']));
denied(409,fn()=>$open($teacher,11,$t+42));
$db->exec('UPDATE class_schedules SET id=99 WHERE id=11');
denied(409,fn()=>$open($teacher,99,$t+42));
$different = $open($other,13,$t+42);
must($different['id'] !== $a['id'] && !$different['seconds'] && $different['active_seconds']===0, 'same room, different bell, independent attendance');

$send=[];
$mock = static function($p,$key,$method,$body) use (&$send): array {
    if ($method==='getChat' && $p==='rubika') return ['chat'=>['chat_type'=>str_starts_with($body['chat_id'],'g')?'Group':'User','chat_id'=>$body['chat_id'],'user_id'=>'u1']];
    if ($method==='getChat') return ['id'=>$body['chat_id'],'type'=>$body['chat_id']==='555'?'group':'private'];
    if ($method==='sendMessage') { $send[]=[$p,$body['chat_id'],$body['text']]; return ['message_id'=>'m1']; }
    return ['updates'=>[]];
};
$cmd=cl_own_rubika($s,$cfg,$student,'rubika-pair',$t+43)['command'];
$update=['type'=>'NewMessage','chat_id'=>'gGroup','new_message'=>['sender_type'=>'User','sender_id'=>'u1','text'=>$cmd]];
cl_rubika_update($s,$cfg,$cfg['rubika_token'],$update,$t+43,$mock);
must(!$s->get('rubika-link','1'),'group cannot pair');
$update['chat_id']='bPrivate';
cl_rubika_update($s,$cfg,$cfg['rubika_token'],$update,$t+44,$mock);
$update['chat_id']='bOther';
cl_rubika_update($s,$cfg,$cfg['rubika_token'],$update,$t+45,$mock);
must($s->get('rubika-link','1')['chat']==='bPrivate', 'short-lived private pairing consumed once');
$tokens=['bale'=>'987:TestbaleXXXX','telegram'=>'456:TesttgXXXX','rubika'=>$cfg['rubika_token']];
cl_expand_outbox($s,$tokens,$cl['id'],$s->get('notify-pending',$cl['id']),$t+46);
foreach ($s->rows('outbox',20) as $key=>$item) cl_deliver_one($s,$key,$item,$tokens,$t+46,$mock);
must(count($send)===4 && !$s->rows('outbox',20), 'dedup, send three platforms and absence, skip group and unrelated contacts');
$targets=array_column($send,1); sort($targets);
must($targets===['111','333','444','bPrivate'] && $s->get('delivery',$cl['id'])['sent']===4, 'only private linked contacts');
foreach ($send as [$provider,$target,$text]) must(str_contains($text,'00:00:09') && str_contains($text,$target==='333'?'00:00:00':'00:00:06'), 'correct attendance message');
cl_own_rubika($s,$cfg,$student,'rubika-unlink',$t+47);
must(!$s->get('rubika-link','1'), 'student can unlink');
$db->exec('UPDATE academic_years SET is_default=CASE WHEN id=2 THEN 1 ELSE 0 END');
$tick([],$t+48);
must(!cl_active($s,20) && cl_report_detail($s,$teacher,$cl['id'])===$rpt, 'changing school year closes prior live rooms and preserves historical report');
if ($mysql) foreach ([$table,'academic_years','settings','classes','students','teachers','admins','class_schedules','bale_bot_users','telegram_bot_users'] as $name) $db->exec("DROP TABLE `$name`");
echo "PASS calendar, access roles, staff lease, waiting/resume, pause-aware per-student accounting, board/chat and three opted-in bot transports\n";
