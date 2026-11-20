<?php
declare(strict_types=1);
require __DIR__ . '/../school-addon/upload/online-classroom/events.php';
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
    'notify_school_bale'=>true,'notify_school_telegram'=>true,'notify_school_rubika'=>true];
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

$runtime = null; $sfus = []; $snapshots = []; $outage = false;
$ctx = ['write_runtime'=>static function ($value) use (&$runtime) { $runtime = $value; }];
$rpc = static function ($method,$room,$payload) use (&$runtime,&$sfus,&$snapshots,&$outage,$s,$cfg): array {
    if ($method === 'CreateRoom') {
        must($runtime === 'scheduled-v2' && cl_active($s,cl_room_class($cfg,$room))['room'] === $room, 'room journal before request');
        must($payload['maxParticipants']===4, 'pilot cap retained');
        $sfus[$room]=[];
    } elseif ($method === 'DeleteRoom') { unset($sfus[$room]); }
    elseif ($method === 'ListParticipants') {
        if ($outage) throw new ClassroomError(502, 'simulated RPC failure');
        if (!isset($sfus[$room])) throw new ClassroomError(409, 'gone');
        return ['participants'=>array_values($snapshots[$room] ?? $sfus[$room])];
    } elseif ($method === 'UpdateParticipant') {
        return $sfus[$room][$payload['identity']] ?? throw new ClassroomError(409, 'gone');
    }
    return [];
};
$open = fn($actor,$slot,$n,$class=null) => cl_open_lesson($s,$ctx,$cfg,$class??$first,$actor,['slot'=>$slot],$n,$rpc);
$bind = fn($l,$sid) => ['lesson'=>$l['id'],'epoch'=>cl_epoch($l),'sid'=>$sid];
$pulse = function ($c,$actor,$sid,$n) use ($s,$bind) { $l=cl_active($s,$c); cl_touch($s,$l,$actor,$bind($l,$sid),$n); };
$wire = function (string $room, array $participants) use (&$sfus): void {
    $sfus[$room] = [];
    foreach ($participants as $id=>$sid) $sfus[$room][$id] = ['sid'=>$sid,'identity'=>$id,'state'=>'ACTIVE'];
};
$sample = fn($c,$n,$class)=> cl_reconcile($s,$cfg,$class,cl_active($s,$c),$n,$rpc);
$event = fn($kind,$room,$n,$who=null,$sid=null)=>cl_event_apply($s,$cfg,['event'=>$kind,'room'=>['name'=>$room], 'participant'=>['identity'=>$who,'sid'=>$sid]],$n,$rpc);
$sign = static function (string $raw, int $exp, string $iss) use ($cfg): string {
    $token = cl_b64url('{"alg":"HS256","typ":"JWT"}') . '.' . cl_b64url(json_encode(['iss'=>$iss,'exp'=>$exp,'sha256'=>base64_encode(hash('sha256',$raw,true))]));
    return $token . '.' . cl_b64url(hash_hmac('sha256',$token,$cfg['api_secret'],true));
};
denied(403,fn()=>$open($student,11,$t));
denied(403,fn()=>$open($teacher,14,$t));
denied(403,fn()=>$open($other,11,$t));
$a=$open($teacher,11,$t);
$b=$open($other,12,$t,$classes[20]);
$r=$a['room']; $r2=$b['room'];
must($a['phase']==='starting' && $r!==$r2 && !$a['started'], 'staff enters before pupils');
denied(409,fn()=>cl_ticket($cfg,$a,$student,$bind($a,'PA_Student'),$t));
denied(409,fn()=>cl_check_live($a,$teacher,$bind($a,'PA_Teacher'),$t));
$ticket = cl_ticket($cfg,$a,$teacher,$bind($a,'PA_Teacher'),$t);
must($ticket['epoch'] === cl_epoch($a), 'staff ticket before media is live; no independent watcher');
$raw = json_encode(['event'=>'room_started','room'=>['name'=>$r]]);
$jwt = $sign($raw,$t+300,$cfg['api_key']);
must(cl_event_verify($cfg,$raw,$jwt,$t)['room']['name']===$r, 'native signed LiveKit event');
denied(403,fn()=>cl_event_verify($cfg,$raw.' ',$jwt,$t));
denied(403,fn()=>cl_event_verify($cfg,$raw,$jwt,$t+301));
denied(403,fn()=>cl_event_verify($cfg,$raw,$sign($raw,$t+300,'unrelated'),$t));
$teacherWire=['teacher-1'=>'PA_Teacher'];
$studentWire=['student-1'=>'PA_Student'];
$managerWire=['admin-3'=>'PA_Manager'];
$otherWire=['teacher-2'=>'PA_Other'];
$wire($r,$teacherWire);
$pulse(10,$teacher,'PA_Teacher',$t+3);
$a = $sample(10,$t+3,$first);
must($a['phase']==='starting', 'no student tickets without the LiveKit callback');
denied(409,fn()=>cl_ticket($cfg,$a,$student,$bind($a,'PA_Student'),$t+3));
cl_event_apply($s,$cfg,cl_event_verify($cfg,$raw,$jwt,$t+4),$t+4,$rpc);
$event('room_started',$r2,$t+4);
$wire($r2,$otherWire); $pulse(20,$other,'PA_Other',$t+6);
$sample(20,$t+6,$classes[20]);
$a = $sample(10,$t+6,$first);
must($a['phase']==='live' && $a['active_seconds']===0, 'verified staff and no time counted in wait');
cl_ticket($cfg,$a,$student,$bind($a,'PA_Student'),$t+6);
denied(409,fn()=>cl_check_live($a,$student,$bind($a,'PA_Fake'),$t+6));
denied(409,fn()=>cl_check_live($a,$other,$bind($a,'PA_Teacher'),$t+6));
$pulse(10,$student,'PA_Student',$t+8);
$pulse(10,$manager,'PA_Manager',$t+8);
$wire($r,$teacherWire+$studentWire+$managerWire);
$sample(10,$t+9,$first);
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
$wire($r,$studentWire+$managerWire);
$event('participant_left',$r,$t+11,'teacher-1','PA_Teacher');
$a=$sample(10,$t+12,$first);
must($a['active_seconds']===6 && $a['seconds']['student-1']===3 && $a['phase']==='live', 'second authorized staff keeps room open');
$wire($r,$studentWire);
$event('participant_left',$r,$t+15,'admin-3','PA_Manager');
$a=cl_active($s,10);
must($a['room'] === null && !isset($sfus[$r]) && $a['phase']==='paused' && $a['active_seconds']===6
    && cl_active($s,20)['phase']==='live' && isset($sfus[$r2]), 'signed leave closes only this room without any browser request or watcher');
denied(409,fn()=>cl_check_live($a,$student,$bind($a,'PA_Student'),$t+15));
denied(409,fn()=>cl_ticket($cfg,$a,$student,$bind($a,'PA_Student'),$t+15));
$a2=$open($teacher,11,$t+19);
$r3=$a2['room'];
must($a2['id']===$a['id'] && $r3!==$r && $a2['seconds']['student-1']===3, 'resume same lesson and counters with a new media room');
denied(409,fn()=>cl_ticket($cfg,$a2,$teacher,$bind($a,'PA_Teacher'),$t+19));
$event('room_finished',$r,$t+20);
$event('participant_joined',$r3,$t+20);
$pulse(10,$teacher,'PA_NewTeacher',$t+20);
$pulse(10,$student,'PA_NewStudent',$t+20);
$wire($r3,['teacher-1'=>'PA_NewTeacher','student-1'=>'PA_NewStudent']);
$sample(10,$t+21,$first);
$sample(10,$t+24,$first);
must(cl_active($s,10)['seconds']['student-1']===6, 'resumed interval counted once');
$pulse(10,$teacher,'PA_NewTeacher',$t+40);
$a3=$sample(10,$t+41,$first);
must($a3['phase']==='paused' && !isset($sfus[$r3]) && $a3['seconds']['student-1']===6, 'unsampled gap not counted, expired transport revoked');
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
$host = static function (string $provider) use (&$send,$s): ?array {
    if ($provider === 'rubika') return null; // No school bridge in the reviewed host.
    return [
        'revision'=>'synthetic-' . $provider,
        'active'=>static fn()=>true,
        'links'=>static fn($student)=>cl_query($s->db, "SELECT {$provider}_chat_id FROM {$provider}_bot_users WHERE student_id=?", [$student])->fetchAll(PDO::FETCH_COLUMN),
        'private'=>static fn($chat)=>!str_starts_with($chat,'-') && $chat !== '555',
        'send'=>static function ($student,$chat,$text) use ($provider,&$send): bool { $send[]=[$provider,$chat,$text]; return true; },
    ];
};
cl_expand_outbox($s,$cfg,$host,$cl['id'],$s->get('host-pending',$cl['id']),$t+46);
$activation = [];
foreach ($s->rows('host-outbox',20) as $key=>$item) cl_deliver_one($s,$cfg,$host,$key,$item,$t+46,$activation);
must(count($send)===3 && !$s->rows('host-outbox',20), 'dedup, normal school accounts and absence, skip group/unavailable provider');
$targets=array_column($send,1); sort($targets);
must($targets===['111','333','444'] && $s->get('delivery',$cl['id'])['sent']===3, 'only private linked contacts');
foreach ($send as [$provider,$target,$text]) must(str_contains($text,'00:00:09') && str_contains($text,$target==='333'?'00:00:00':'00:00:06'), 'correct attendance message');

// Simulated unlink, inactive provider, changed bot, and ambiguous send after the packet was queued.
$makeNotice = static function (string $provider, string $chat, int $student) use ($s,$cl,$t) {
    $item=['provider'=>$provider,'revision'=>'synthetic-' . $provider,'chat'=>$chat,'student'=>$student,'lesson'=>$cl['id'],'attempt'=>0];
    $key=cl_outbox_key($item,$t+46);
    $s->put('host-outbox',$key,$item);
    return [$key,$item];
};
[$key,$item] = $makeNotice('bale','111',1);
$db->exec("DELETE FROM bale_bot_users WHERE bale_chat_id='111'");
cl_deliver_one($s,$cfg,$host,$key,$item,$t+46,$activation);
must(count($send)===3, 'rechecks existing linked account after queueing');
[$key,$item] = $makeNotice('telegram','444',1);
$inactive = static fn($p)=>array_replace($host($p),['active'=>static fn()=>false]);
$freshActivation = [];
cl_deliver_one($s,$cfg,$inactive,$key,$item,$t+46,$freshActivation);
must(count($send)===3, 'remote webhook not active for the site');
[$key,$item] = $makeNotice('telegram','444',1);
$changed = static fn($p)=>array_replace($host($p),['revision'=>'other-test-bot']);
cl_deliver_one($s,$cfg,$changed,$key,$item,$t+46,$freshActivation);
must(count($send)===3, 'queued notices cannot be redirected to another bot');
[$key,$item] = $makeNotice('telegram','444',1);
$ambiguous = static fn($p)=>array_replace($host($p),['send'=>static fn()=>throw new RuntimeException('simulated lost reply')]);
$freshActivation = [];
cl_deliver_one($s,$cfg,$ambiguous,$key,$item,$t+46,$freshActivation);
must(!$s->get('host-outbox',$key) && $s->get('delivery',$cl['id'])['uncertain']===1, 'provider timeout does not duplicate a potentially delivered notice');

// If webhooks are delayed, server-side samples may see a stale list but still check the current staff SID.
$pulse(10,$other,'PA_Other2',$t+43); $r4 = $different['room'];
$event('room_started',$r4,$t+43); $wire($r4,['teacher-2'=>'PA_Other2']);
$sample(10,$t+45,$first);
$snapshots[$r4] = $sfus[$r4]; $wire($r4,[]);
$a4 = $sample(10,$t+48,$first);
must($a4['phase']==='paused' && !isset($sfus[$r4]), 'lost last adult even if ListParticipants is stale and no callback has arrived');
$different = $open($deputy,13,$t+49); $r5 = $different['room'];
$event('room_started',$r5,$t+49);
$pulse(10,$deputy,'PA_Deputy',$t+50); $wire($r5,['teacher-3'=>'PA_Deputy']);
must($sample(10,$t+51,$first)['phase']==='live', 'existing school deputy starts the room');
$outage = true;
denied(502,fn()=>$sample(10,$t+54,$first));
$a5 = cl_active($s,10);
denied(409,fn()=>cl_ticket($cfg,$a5,$student,$bind($a5,'PA_Student'),$t+54));
must($a5['phase']==='paused', 'failed RPC closes application access before attempting remote cleanup');
$outage = false;
$sample(10,$t+57,$first);
must(!isset($sfus[$r5]), 'server retries the owned room cleanup');
$db->exec('UPDATE academic_years SET is_default=CASE WHEN id=2 THEN 1 ELSE 0 END');
$sample(20,$t+60,null);
must(!cl_active($s,20) && cl_report_detail($s,$teacher,$cl['id'])===$rpt, 'changing school year closes prior room and preserves report');
// The administrator's time windows use Tehran even when the hosting server uses a different zone.
$db->exec('UPDATE academic_years SET is_default=CASE WHEN id=1 THEN 1 ELSE 0 END');
cl_close_lesson($s,$cfg,cl_active($s,10),$t+61,$rpc);
$db->exec("INSERT INTO class_schedules VALUES (73,1,'هفتم1','1405/1406','چهارشنبه','زنگ ۳','ورزش'),(74,1,'هفتم1','1405/1406','چهارشنبه','۰۴','هنر')");
$bell = strtotime('2026-11-25T05:00:00Z'); // Wednesday 08:30 Asia/Tehran.
$end = $bell + 60;
$hours = [1=>['start'=>'08:30','end'=>'08:31'], 2=>['start'=>'08:31','end'=>'09:15'], 4=>['start'=>'10:20','end'=>'11:05']];
$timed = $cfg + ['bell_hours'=>$hours];
$at = fn($actor,$n)=>cl_staff_schedule($s,$timed,$first,$actor,$n);
$timedOpen = fn($actor,$slot,$n)=>cl_open_lesson($s,$ctx,$timed,$first,$actor,['slot'=>$slot],$n,$rpc);
$upcoming = $at($deputy,$bell-1);
must($upcoming['timed'] && $upcoming['date'] === '2026-11-25' && !$upcoming['choices'] && str_contains($upcoming['next'],'08:30 تا 08:31'), 'waiting before the right bell by server clock');
must(array_column($at($deputy,$bell)['choices'],'id') === [99] && !$at($other,$bell)['choices'], 'exact start is inclusive and does not change teacher assignment');
must(!cl_staff_schedule($s,$timed,$first,$teacher,$bell-86400)['choices'], 'hours never manufacture a missing weekly slot');
denied(409,fn()=>$timedOpen($teacher,99,$bell-1));
denied(409,fn()=>$timedOpen($deputy,13,$bell));
denied(403,fn()=>$timedOpen($other,99,$bell));
denied(409,fn()=>$timedOpen($teacher,73,$bell+4800)); // Blank third row does not open even though the timetable has a third bell.
must(!$sfus && !cl_active($s,10), 'attempts outside a configured period do not create an SFU room or lesson');
$firstBell = $timedOpen($teacher,99,$bell); $ring = $firstBell['room'];
must($firstBell['deadline'] === $end && $firstBell['bell'] === '08:30 تا 08:31', 'configured cutoff instead of a fixed two-hour deadline');
$event('room_started',$ring,$bell+1); $wire($ring,$teacherWire); $pulse(10,$teacher,'PA_Teacher',$bell+3);
must($sample(10,$bell+3,$first)['phase'] === 'live', 'live authorization remains required in the scheduled window');
$event('participant_left',$ring,$bell+7,'teacher-1','PA_Teacher');
must(!isset($sfus[$ring]) && cl_active($s,10)['phase'] === 'paused', 'scheduled bell still pauses after the last supervisor');
$revised = array_replace($timed,['bell_hours'=>[1=>['start'=>'09:00','end'=>'09:30']]]);
$continued = cl_open_lesson($s,$ctx,$revised,$first,$teacher,['slot'=>99],$end-10,$rpc); $back = $continued['room'];
must($continued['id'] === $firstBell['id'] && $continued['deadline'] === $end && $back !== $ring, 'existing bell retains the original time even on resume after an edit');
$event('room_started',$back,$end-9); $wire($back,$teacherWire+$studentWire);
$pulse(10,$teacher,'PA_Teacher',$end-7); $pulse(10,$student,'PA_Student',$end-7);
cl_reconcile($s,$revised,$first,cl_active($s,10),$end-7,$rpc);
$pulse(10,$teacher,'PA_Teacher',$end-1); $pulse(10,$student,'PA_Student',$end-1);
$liveBell = cl_reconcile($s,$revised,$first,cl_active($s,10),$end-1,$rpc);
$lateTicket = cl_ticket($revised,$liveBell,$student,$bind($liveBell,'PA_Student'),$end-1);
$claims = json_decode(base64_decode(strtr(explode('.',$lateTicket['token'])[1],'-_','+/')), true, 16, JSON_THROW_ON_ERROR);
must($claims['exp'] === $end, 'signed listener token expires no later than the bell');
denied(409,fn()=>cl_ticket($revised,$liveBell,$student,$bind($liveBell,'PA_Student'),$end));
denied(409,fn()=>cl_check_live($liveBell,$student,$bind($liveBell,'PA_Student'),$end));
must(cl_reconcile($s,$revised,$first,$liveBell,$end,$rpc) === null && !isset($sfus[$back]), 'at the exclusive end, retire the room and finalize the report');
must(cl_report_detail($s,$teacher,$firstBell['id'])['ended'] === $end, 'report uses the stored end time');
denied(409,fn()=>$timedOpen($teacher,99,$end));
must(array_column($at($deputy,$end)['choices'],'id') === [13], 'an adjacent bell is now available, not an extension of the first');
$secondBell = $timedOpen($other,13,$end); $secondRoom = $secondBell['room'];
$event('participant_joined',$secondRoom,$end+1); $wire($secondRoom,$otherWire); $pulse(10,$other,'PA_Other',$end+3);
$sample(10,$end+3,$first);
$event('participant_left',$secondRoom,$secondBell['deadline'],'teacher-2','PA_Other');
must(!cl_active($s,10) && !isset($sfus[$secondRoom]) && cl_report_detail($s,$other,$secondBell['id']), 'a signed event also finalizes an expired bell without a browser request');
$fourth = strtotime('2026-11-25T06:50:00Z');
must(array_column($at($teacher,$fourth)['choices'],'id') === [74], 'fourth bell maps to the normalized school period number');
must(!$at($teacher,$fourth+2700)['choices'], 'bell end is exclusive, even if it was never opened');
if ($mysql) foreach ([$table,'academic_years','settings','classes','students','teachers','admins','class_schedules','bale_bot_users','telegram_bot_users'] as $name) $db->exec("DROP TABLE `$name`");
echo "PASS calendar, access roles, configured bell windows, native events, trusted presence, waiting/resume, reports, board/chat and conditional school bot delivery\n";
