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
    "INSERT INTO teachers VALUES (1,'دبیر ریاضی',NULL,1,0),(2,'دبیر دیگر','1404/1405',1,0),(3,'ناظم',NULL,1,1),(4,'دبیر سال پیش','1404/1405',1,0)",
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
$db->exec("UPDATE settings SET key_value='1404/1405' WHERE key_name='current_academic_year'");
must(cl_current_year($db) === '1404/1405', 'native current selection wins over the default flag');
$db->exec('UPDATE academic_years SET status=0 WHERE id=2');
denied(409, fn()=>cl_current_year($db));
$db->exec("UPDATE settings SET key_value='1402/1403' WHERE key_name='current_academic_year'");
must(cl_current_year($db) === $year, 'deleted year falls back to an unambiguous active default');
$db->exec('UPDATE academic_years SET status=1 WHERE id=2');
$db->exec("UPDATE settings SET key_value='1405/1406' WHERE key_name='current_academic_year'");
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
must(cl_class_label('هفتم1') === '7/1' && cl_class_label($first['name']) === '7/1'
    && cl_class_label('دوازدهم-٣') === '12/3' && cl_class_label('۹ / ۲') === '9/2' && cl_class_label('پژوهش الف') === 'پژوهش الف',
    'display labels normalize known grades and preserve custom class names');
must($first['name'] === 'هفتم ۱' && $first['key'] === cl_class_key('هفتم1'), 'room labels do not alter stored names or matching');
must(cl_school_name($db) === '', 'no fabricated school name if the setting is missing');
$db->exec("INSERT INTO settings VALUES ('school_name','دبیرستان بصیرت')");
must(cl_school_name($db) === 'دبیرستان بصیرت', 'read the school name from the existing settings table');
$db->exec('ALTER TABLE students ADD COLUMN photo_url VARCHAR(255)');
$db->beginTransaction();
try {
    $db->exec("UPDATE students SET first_name='حسن',last_name='احسانی',photo_url='uploads/photos/native.png' WHERE id=1;
        UPDATE students SET first_name='علی',last_name='احسانی' WHERE id=4;
        UPDATE students SET first_name='مریم',last_name='صادقی',photo_url='https://foreign.test/pupil.jpg' WHERE id=2;
        UPDATE students SET first_name='ح',last_name='صادقی' WHERE id=3 OR id=5;");
    $cards = cl_class_profiles($db,$first,['student-1'=>'PA_Sid','student-2'=>'PA_Other']);
    must($cards === ['student-1'=>['name'=>"ح\u{200C}احسانی",'photo'=>true]], 'a duplicate who has not joined counts; only live classmates get cards');
    $cards = cl_class_profiles($db,$first,['student-1'=>'PA_Sid','student-4'=>'PA_Peer']);
    must($cards['student-4'] === ['name'=>"ع\u{200C}احسانی",'photo'=>false], 'first letter plus Persian half-space, not first name');
    must(cl_class_profiles($db,$classes[20],['student-2'=>'PA_Other']) === ['student-2'=>['name'=>'صادقی','photo'=>false]],
        'another class and year do not collide, external photo URL is not used');
    must(cl_roster($db,$first)['student-1'] === 'حسن احسانی', 'reports retain full host names');
} finally { $db->rollBack(); }
$photoRoot = sys_get_temp_dir() . '/class-photo-' . bin2hex(random_bytes(6));
mkdir($photoRoot . '/uploads/photos', 0700, true);
$png = base64_decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGDkAAAAASUVORK5CYII=');
file_put_contents($photoRoot . '/uploads/photos/native.png',$png);
file_put_contents($photoRoot . '/uploads/photos/not-an-image.jpg','hello');
symlink($photoRoot . '/uploads/photos/native.png',$photoRoot . '/uploads/photos/alias.png');
try {
    must(cl_read_photo($photoRoot,'uploads/photos/native.png') === ['mime'=>'image/png','raw'=>$png], 'local image returned as binary');
    foreach (['https://foreign.test/pupil.png','/etc/passwd','uploads/photos/../native.png','uploads/photos/alias.png','uploads/photos/not-an-image.jpg',
        'uploads/photos/..%2Fnative.png','uploads/photos/a\\b.png'] as $badPhoto) must(cl_read_photo($photoRoot,$badPhoto)===null,'reject arbitrary paths, aliases and non-images');
} finally {
    foreach (['native.png','not-an-image.jpg','alias.png'] as $file) unlink($photoRoot . '/uploads/photos/' . $file);
    rmdir($photoRoot . '/uploads/photos'); rmdir($photoRoot . '/uploads'); rmdir($photoRoot);
}

denied(403,fn()=>cl_class_for($db,$classes,20,$student));
must(cl_access_class($db,$classes[10],$other) && !cl_access_class($db,$classes[20],$teacher), 'teacher class from assignment, not display name');
must(array_keys(cl_today_slots($db,$first,$teacher,$t)) === [11] && array_keys(cl_today_slots($db,$first,$deputy,$t)) === [11,13], 'Tehran weekday and scheduled teacher/deputy even with null or stale teacher year');

$db->exec("INSERT INTO class_schedules VALUES (91,2,'هفتم1','1405/1406','چهارشنبه','۰۱','علوم')");
must(!cl_get_slot($db,$first,11,$t) && !cl_get_slot($db,$first,91,$t) && array_keys(cl_today_slots($db,$first,$deputy,$t)) === [13], 'conflicting rows for the same bell are rejected');
$db->exec('DELETE FROM class_schedules WHERE id=91');
$db->exec("INSERT INTO class_schedules VALUES (91,1,'هفتم1','1405/1406','چهار شنبه','زنگ ۱','ریاضی'),
    (92,1,'هفتم1','1405/1406','چهارشنبه','زنگ ۵','نامعتبر'),(93,1,'هفتم1','1405/1406','چهارشنبه','کلاس ۳','نامعتبر')");
must(array_keys(cl_slots_for_date($db,$first,$t)) === [13], 'identical duplicates and malformed periods are not eligible even without clock hours');
$db->exec('DELETE FROM class_schedules WHERE id IN (91,92,93)');

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
denied(409,fn()=>cl_check_live($cfg,$a,$teacher,$bind($a,'PA_Teacher'),$t));
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
denied(409,fn()=>cl_check_live($cfg,$a,$student,$bind($a,'PA_Fake'),$t+6));
denied(409,fn()=>cl_check_live($cfg,$a,$other,$bind($a,'PA_Teacher'),$t+6));
$pulse(10,$student,'PA_Student',$t+8);
$pulse(10,$manager,'PA_Manager',$t+8);
$wire($r,$teacherWire+$studentWire+$managerWire);
$sample(10,$t+9,$first);
$active=cl_active($s,10);
cl_check_live($cfg,$active,$student,$bind($active,'PA_Student'),$t+9);
$wake = $bind($active,'PA_Teacher') + ['mode'=>'idle'];
must(cl_wake_mode($active) === 'live' && cl_wake_control($active,$teacher), 'wake defaults on and is configurable by the assigned teacher');
denied(403,fn()=>cl_set_wake($s,$active,$student,$wake));
denied(403,fn()=>cl_set_wake($s,$active,$manager,$wake));
denied(403,fn()=>cl_set_wake($s,$active,$other,$wake));
denied(400,fn()=>cl_set_wake($s,$active,$teacher,array_replace($wake,['mode'=>['idle']])));
denied(409,fn()=>cl_set_wake($s,$active,$teacher,array_replace($wake,['sid'=>'PA_Stale'])));
denied(409,fn()=>cl_set_wake($s,$active,$teacher,array_replace($wake,['epoch'=>str_repeat('0',24)])));
must(cl_set_wake($s,$active,$teacher,$wake) === ['wakeMode'=>'idle'], 'assigned teacher can choose one lesson-wide setting');
$active=cl_active($s,10);
must(cl_wake_mode($active) === 'idle' && cl_wake_mode(cl_active($s,20)) === 'live', 'setting remains in this lesson only');
cl_chat($s,$active,$student,'<b>متن است</b>',$t+9);
denied(429,fn()=>cl_chat($s,$active,$student,'spam',$t+10));
$stroke=['tool'=>'pen','color'=>'#0055aa','size'=>4,'points'=>[[420,200],[9000,9800]]];
$pupilInk = ['strokes'=>[$stroke+['id'=>str_repeat('b',32)]]];
denied(403,fn()=>cl_ink_input($active,$student,$pupilInk));
cl_set_access($s,$active,$teacher,['feature'=>'board','enabled'=>true,'identity'=>'student-1'],$t+9,$rpc);
$active = cl_active($s,10);
must(cl_activity_sync($s,$active,$student,[])['board']['write'] && !cl_may_write($active,['role'=>'student','identity'=>'student-4']),
    'individual pen grant is visible only to that pupil');
$pupilLine = cl_ink_input($active,$student,$pupilInk + ['author'=>'teacher-1']);
$staffLine = cl_ink_input($active,$teacher,$pupilInk);
$ownId = $pupilLine['strokes'][0]['id'];
$foreignId = $staffLine['strokes'][0]['id'];
must($ownId !== $foreignId && $pupilLine['author'] === 'student-1', 'PHP assigns the author and isolates identical gesture IDs');
$eraseOwn = static fn(array $targets): array => ['strokes'=>[['tool'=>'erase-stroke','targets'=>$targets]]];
must(cl_ink_input($active,$student,$eraseOwn([$ownId]))['strokes'][0]['targets'] === [$ownId], 'pupil may erase own whole lines');
denied(403,fn()=>cl_ink_input($active,$student,$eraseOwn([$ownId,$foreignId])));
denied(403,fn()=>cl_ink_input($active,$student,['strokes'=>[array_replace($stroke,['tool'=>'erase'])]]));
$cueLesson = array_replace($active,['id'=>str_repeat('d',32)]);
$inkUpdate=cl_ink_input($cueLesson,$student,$pupilInk + ['board'=>1]);
cl_board_action($s,$cueLesson,$inkUpdate,'board-ink');
$point = cl_board_read($s,$cueLesson,$student,['board'=>1,'ink'=>0])['ops'][0];
must($point['author'] === 'student-1' && $point['live'] === true && !isset($point['at']), 'recent ink has a trusted author cue');
must(!isset(cl_board_read($s,$cueLesson,$student,[])['ops'][0]['live']), 'history never carries a live cue');
$inkBucket='ink:' . $cueLesson['id'] . ':' . cl_layer($s,$cueLesson['id'],cl_activity($s,$cueLesson['id']))['key'];
$agedPoint = $s->get($inkBucket,cl_seq(2)); $agedPoint['at'] -= 2;
$s->put($inkBucket,cl_seq(2),$agedPoint);
must(!isset(cl_board_read($s,$cueLesson,$student,['board'=>1,'ink'=>0])['ops'][0]['live']), 'a late delta still paints ink, not a fresh name');
$requestAim=['board'=>1,'sid'=>'PA_Teacher','aim'=>[[3300,6200,600],[4500,6700,250],[7000,6800,10]]];
denied(403,fn()=>cl_board_read($s,$cueLesson,$student,$requestAim));
foreach ([[[3300,16000,50]], [[3300,6200,1200]], [[1,2,10],[3,4,50]], array_fill(0,CL_LASER_POINTS+1,[100,200,0])] as $invalid) {
    denied(400,fn()=>cl_board_read($s,$cueLesson,$teacher,array_replace($requestAim,['aim'=>$invalid])));
}
denied(409,fn()=>cl_board_read($s,$cueLesson,$teacher,array_replace($requestAim,['sid'=>'PA_Wrong'])));
cl_board_read($s,$cueLesson,$teacher,$requestAim);
cl_board_read($s,$cueLesson,$teacher,array_replace($requestAim,['aim'=>[[100,200,0]]]));
$withTrail=cl_board_read($s,$cueLesson,$student,['board'=>1,'ink'=>2]);
$coords=static fn($samples) => array_map(static fn($point)=>array_slice($point,0,2),$samples);
must($coords($withTrail['laser']['points']) === $coords($requestAim['aim']) && $withTrail['cursor'] === 2 && $withTrail['ops'] === [],
    'laser is one rate-limited snapshot of a sweep, not an ink operation');
$oldPointer = cl_activity($s,$cueLesson['id']); $oldPointer['laser']['at']-=.65;
$s->put('activity',$cueLesson['id'],$oldPointer);
$tail=cl_board_read($s,$cueLesson,$student,['board'=>1,'ink'=>2])['laser']['points'];
must($coords($tail) === [[4500,6700],[7000,6800]] && $tail[0][2] < $tail[1][2], 'the oldest segment expires before the tip');
$epochChanged = array_replace($cueLesson,['room'=>substr($cueLesson['room'],0,-24) . str_repeat('0',24)]);
must(cl_board_read($s,$epochChanged,$student,[])['laser'] === null, 'a resumed transport never gets the old laser');
$oldPointer['laser']['at']-=1;
$s->put('activity',$cueLesson['id'],$oldPointer);
must(cl_board_read($s,$cueLesson,$student,[])['laser'] === null, 'old laser disappears without storing a durable trail');
cl_board_read($s,$cueLesson,$teacher,array_replace($requestAim,['board'=>2]));
must(cl_board_read($s,$cueLesson,$student,[])['laser'] === null, 'a stale page cannot insert a new laser trail');
cl_board_read($s,$cueLesson,$teacher,$requestAim,microtime(true)-1.3);
must(cl_board_read($s,$cueLesson,$student,[])['laser'] === null, 'a request held up by the server lock is not replayed');
cl_clear_activity($s,$cueLesson['id']);
denied(403,fn()=>cl_ink_input($active,$student,['strokes'=>[['tool'=>'erase-stroke','targets'=>['old-00000001']]]]));
cl_set_access($s,$active,$manager,['feature'=>'board','enabled'=>false],$t+9,$rpc);
$active = cl_active($s,10);
must(!cl_activity_sync($s,$active,$student,[])['board']['write'] && cl_may_write($active,$teacher), 'group revoke resets individual exceptions and leaves staff tools');
denied(403,fn()=>cl_ink_input($active,$student,$pupilInk));
cl_set_access($s,$active,$teacher,['feature'=>'board','enabled'=>true],$t+9,$rpc);
$active = cl_active($s,10);

$ink = static fn(array $ops, int $rev = 1) => cl_board_action($s,$active,['board'=>$rev,'strokes'=>$ops],'board-ink');
$view = static fn(int $rev, int $cursor = 0) => cl_board_view($s,$active['id'],$student,['board'=>$rev,'ink'=>$cursor],cl_activity($s,$active['id']));
must($ink([$stroke])['ink']===1, 'existing ink format still accepted');
$legacy = $stroke+['id'=>'old-00000001'];
$sync=cl_activity_sync($s,$active,$student,[]);
must($sync['board']['ops']==[$legacy] && $sync['chat'][0]['text']==='<b>متن است</b>' && !$sync['board']['docs'], 'newcomer gets available page and chat as plain text');
must($view(1,1)['ops']===[], 'incremental legacy ink updates');
denied(400,fn()=>$ink([array_replace($stroke,['points'=>[[99000,1]]])]));

$long=[];
for ($i=0;$i<155;$i++) $long[]=[100+25*$i,3000+$i];
$pen = ['tool'=>'pen','id'=>str_repeat('a',32),'size'=>6,'color'=>'#00aaff'];
$early = $pen+['points'=>array_slice($long,0,24)];
$next = $pen+['points'=>array_slice($long,23,41)];
$lastPart = $pen+['points'=>array_slice($long,63)];
$startTick = $ink([$early])['ink'];
must($startTick===25 && $view(1,1)['ops']==[$early], 'reader sees first part immediately without a finished gesture');
$middleTick = $ink([$next])['ink'];
$layer = cl_layer($s,$active['id'],cl_activity($s,$active['id']));
must($middleTick===65 && $layer['seq']===2 && $layer['clock']===65,
    'streamed pen extends the tail row without spending another whole operation');
must($view(1,$startTick)['ops']==[$next] && $view(1,$middleTick)['ops']===[], 'cursor inside compacted row receives only new points and its anchor');
$endTick = $ink([$lastPart])['ink'];
must($endTick===157 && $view(1,$middleTick)['ops']==[$lastPart], 'long stroke spills into another row under the same id');
$pixel = ['tool'=>'erase','size'=>18,'color'=>'#ffffff','points'=>[[1200,2100],[1370,2140]]];
$erase = ['tool'=>'erase-stroke','targets'=>[$pen['id'],$legacy['id']]];
$eraseTick = $ink([$pixel,$erase])['ink'];
must($view(1,$endTick)['ops']==[$pixel,$erase], 'whole-stroke tombstone is independent of ordered legacy erase masks');
denied(400,fn()=>$ink([$early,['tool'=>'erase-stroke','targets'=>['other-page:bad']]]));
denied(400,fn()=>$ink([['tool'=>'erase-stroke','targets'=>array_fill(0,CL_INK_TARGETS+1,$pen['id'])]]));
must($view(1,$eraseTick)['ops']===[], 'invalid batch does not write any prefix');
$afterErase = $pen+['points'=>[$long[154],[4100,3180]]];
$ink([$afterErase]);
$blank = $view(1)['ops'];
must($blank==[$legacy,$pen+['points'=>array_slice($long,0,64)],$lastPart,$pixel,$erase,$afterErase],
    'new readers replay compacted geometry, legacy mask and tombstones even if a peer was still drawing');

$meta=cl_activity($s,$a['id']); $doc=str_repeat('1',32); $meta['docs'][$doc]=['id'=>$doc,'pages'=>2317,'name'=>'test.pdf','bytes'=>256,'mime'=>'application/pdf'];
$s->put('activity',$a['id'],$meta);
must(cl_board_action($s,$active,['board'=>1,'doc'=>$doc,'page'=>2317],'board-page')===['boardRev'=>2], 'page switch acknowledges a new revision');
$sync=$view(1,$eraseTick);
must($sync['page']===2317 && $sync['reset'] && $sync['cursor']===0, 'arbitrary PDF page and independent strokes');
denied(409,fn()=>$ink([$erase]));
$ink([$early], 2);
must($view(2)['ops']==[$early], 'the tombstone is confined to its original layer');
cl_board_action($s,$active,['board'=>2],'board-clear');
must($view(2)['cursor']===0 && $view(2)['doc']['id']===$doc && $view(2)['page']===2317, 'clear only ink and keep PDF and page');
cl_board_action($s,$active,['board'=>3,'doc'=>null,'page'=>1],'board-page');
must($view(4)['ops']===$blank, 'return to separately annotated page');
cl_board_action($s,$active,['board'=>4],'board-clear');
must($view(4,$eraseTick)['cursor']===0 && cl_activity($s,$active['id'])['ink_ops']===0, 'clear resets cursor and subtracts rows, not ticks');

// Board reads page by row count, not by cursor distance, including when chunks create gaps.
for ($i=0;$i<121;$i++) $ink([array_replace($early,['id'=>sprintf('%032x',$i)])],5);
$window = $view(5);
must(count($window['ops'])===120 && $window['more'] && $window['cursor']===120*24, 'bounded read of compact rows');
$remainder = $view(5,$window['cursor']);
must(count($remainder['ops'])===1 && !$remainder['more'] && $remainder['cursor']===121*24, 'gap-aware cursor drains the remainder');
// Force both caps near capacity, then check rollback after an in-row merge and a new row.
$budget = cl_activity($s,$active['id']); $layer = cl_layer($s,$active['id'],$budget);
$budgetKey = cl_layer_key($budget); $layerBucket = 'layers:' . $active['id'];
$s->put('activity',$active['id'],array_replace($budget,['ink_ops'=>11999]));
$s->put($layerBucket,$budgetKey,array_replace($layer,['seq'=>1599]));
$extension = array_replace($remainder['ops'][0],['points'=>[$long[23],$long[24]]]);
denied(409,fn()=>$ink([$extension,$early,$early],5));
$rolledBack = $view(5,$window['cursor']);
must($rolledBack['ops']===$remainder['ops'] && $rolledBack['cursor']===$remainder['cursor'],
    'overflow rolls back the replaced tail and entire transaction');
$s->put('activity',$active['id'],$budget);
$s->put($layerBucket,$budgetKey,$layer);
cl_board_action($s,$active,['board'=>5],'board-clear');

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
denied(409,fn()=>cl_check_live($cfg,$a,$student,$bind($a,'PA_Student'),$t+15));
denied(409,fn()=>cl_ticket($cfg,$a,$student,$bind($a,'PA_Student'),$t+15));
$a2=$open($teacher,11,$t+19);
$r3=$a2['room'];
must($a2['id']===$a['id'] && $r3!==$r && $a2['seconds']['student-1']===3, 'resume same lesson and counters with a new media room');
must(cl_may_write($a2,$student) && !cl_may_write(cl_active($s,20),$student), 'board permission stays with the same lesson, not another class');
must(cl_wake_mode($a2) === 'idle', 'wake policy survives transport replacement for this bell');
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
must(!isset($s->get('lesson',$cl['id'])['wake_mode']), 'wake policy is not retained in the finished lesson or report');
$rpt=cl_report_detail($s,$teacher,$cl['id']);
must($rpt['active_seconds']===9 && $rpt['seconds']['student-1']===6 && isset($rpt['roster']['student-4']) && !isset($rpt['seconds']['student-4']), 'report retains absence as zero and accurate denominator');
must(cl_report_detail($s,$deputy,$cl['id'])===$rpt && cl_report_detail($s,$manager,$cl['id'])===$rpt && cl_report_detail($s,$other,$cl['id'])===null, 'historical report scopes');
denied(403,fn()=>cl_report_detail($s,$student,$cl['id']));
denied(409,fn()=>$open($teacher,11,$t+42));
$db->exec('UPDATE class_schedules SET id=99 WHERE id=11');
denied(409,fn()=>$open($teacher,99,$t+42));
$different = $open($other,13,$t+42);
must($different['id'] !== $a['id'] && !$different['seconds'] && $different['active_seconds']===0, 'same room, different bell, independent attendance');
must(cl_wake_mode($different) === 'live', 'a fresh bell gets the default wake policy');

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
$runningBell = $sample(10,$bell+3,$first);
must($runningBell['phase'] === 'live' && $runningBell['started'] > $bell, 'the lesson starts only after confirmed staff admission');
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
must($liveBell['started'] === $runningBell['started'], 'resuming a lesson keeps its original server start');
$lateTicket = cl_ticket($revised,$liveBell,$student,$bind($liveBell,'PA_Student'),$end-1);
$claims = json_decode(base64_decode(strtr(explode('.',$lateTicket['token'])[1],'-_','+/')), true, 16, JSON_THROW_ON_ERROR);
must($claims['exp'] === $end, 'signed listener token expires no later than the bell');
denied(409,fn()=>cl_ticket($revised,$liveBell,$student,$bind($liveBell,'PA_Student'),$end));
denied(409,fn()=>cl_check_live($cfg,$liveBell,$student,$bind($liveBell,'PA_Student'),$end));
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
// V1.15 stores a second subject in the exact same cell. There is no persisted week parity.
$sharedAt = $bell + 7*86400;
$db->exec("INSERT INTO class_schedules VALUES (92,2,'هفتم1','1405/1406','چهار شنبه','زنگ ۱','آزمایشگاه')");
must(array_column($at($deputy,$sharedAt)['choices'],'id') === [92,99] && str_contains($at($deputy,$sharedAt-1)['next'],'انتخاب دستی'),
    'the two native alternatives occupy one timed bell');
must(array_column($at($teacher,$sharedAt)['choices'],'id') === [99] && $at($teacher,$sharedAt)['choices'][0]['manual'],
    'only the assigned alternative is exposed to the teacher, and never auto-started');
$db->exec('UPDATE teachers SET status=0 WHERE id=2');
must(array_column($at($deputy,$sharedAt)['choices'],'id') === [99] && $at($deputy,$sharedAt)['choices'][0]['manual'],
    'a disabled partner is not eligible and does not make the bell automatic');
denied(403,fn()=>$timedOpen($deputy,92,$sharedAt));
$db->exec('UPDATE teachers SET status=1 WHERE id=2');
$db->exec("INSERT INTO class_schedules VALUES (93,NULL,'هفتم1','1405/1406','چهار شنبه','زنگ ۱','سومین ردیف')");
must(!$at($deputy,$sharedAt)['choices'], 'three allocations in a cell stay blocked');
$db->exec('DELETE FROM class_schedules WHERE id=93');
denied(403,fn()=>$timedOpen($teacher,92,$sharedAt));
denied(409,fn()=>$timedOpen($deputy,92,$sharedAt-1));
$sharedLesson = $timedOpen($teacher,99,$sharedAt); $sharedRoom = $sharedLesson['room'];
$event('room_started',$sharedRoom,$sharedAt+1); $wire($sharedRoom,$teacherWire); $pulse(10,$teacher,'PA_Teacher',$sharedAt+2);
must($sample(10,$sharedAt+3,$first)['phase'] === 'live', 'ordinary year-independent teacher supervises the selected row');
denied(409,fn()=>$timedOpen($deputy,92,$sharedAt+4));
$event('participant_left',$sharedRoom,$sharedAt+5,'teacher-1','PA_Teacher');
must(cl_active($s,10)['phase'] === 'paused', 'chosen alternate can pause');
denied(409,fn()=>$timedOpen($deputy,92,$sharedAt+6));
$resumedAlternate = $timedOpen($deputy,99,$sharedAt+7);
must($resumedAlternate['id'] === $sharedLesson['id'] && $resumedAlternate['teacher'] === 1
    && $resumedAlternate['deadline'] === $sharedAt+60, 'only the chosen alternate can resume; the deadline is pinned');
cl_close_lesson($s,$timed,$resumedAlternate,$sharedAt+8,$rpc);
denied(409,fn()=>$timedOpen($deputy,92,$sharedAt+9));
must(!$at($deputy,$sharedAt+9)['choices'] && cl_report_detail($s,$teacher,$sharedLesson['id'])['slot'] === 99
    && cl_report_detail($s,$other,$sharedLesson['id']) === null, 'one report per bell, recorded under its selected teacher');
$db->exec('DELETE FROM class_schedules WHERE id=92');

// A one-day pilot is independent of bell hours, timetable assignments and official attendance.
$fri = strtotime('2026-11-19T20:30:00Z'); // Friday 00:00 in Tehran, still Thursday in UTC.
$sat = strtotime('2026-11-20T20:30:00Z');
$trial = $cfg + ['friday_test'=>['date'=>'2026-11-20','id'=>str_repeat('e',32)],
    'bell_hours'=>[1=>['start'=>'08:30','end'=>'09:15']]];
$priorYear = cl_school_user($db, ['teacher_id'=>4], $year);
$normalReportRows = $s->rows('reports', 100);
$pendingRows = $s->rows('host-pending', 100);
$beforeSchedule = $db->query('SELECT * FROM class_schedules ORDER BY id')->fetchAll(PDO::FETCH_ASSOC);
$trialOpen = fn($actor,$class,$instant)=>cl_open_lesson($s,$ctx,$trial,$class,$actor,['slot'=>CL_FRIDAY_SLOT],$instant,$rpc);
must(cl_friday_window($trial,$fri-1) === null && cl_friday_window($trial,$fri)['end'] === $sat && $sat-$fri === 86400,
    'Tehran midnight, not browser/UTC date, controls the full Friday');
must(cl_friday_window($trial,$sat) === null && cl_friday_window($trial,$fri+7*86400) === null, 'no Saturday spill or weekly repetition');
must(cl_friday_window(['friday_test'=>['date'=>'2026-11-21','id'=>str_repeat('e',32)]],$sat) === null, 'invalid day fails closed at runtime');
must(!cl_access_class($db,$classes[20],$priorYear) && cl_access_class($db,$classes[20],$priorYear,true)
    && !cl_access_class($db,$classes[20],$student,true), 'all active teachers are eligible during the pilot, not pupils in other classes');
must(array_column(cl_staff_schedule($s,$trial,$classes[20],$priorYear,$fri)['choices'],'id') === [CL_FRIDAY_SLOT], 'no bell assignment required');
denied(403,fn()=>$trialOpen($priorYear,$classes[20],$fri-1));
denied(403,fn()=>$trialOpen($student,$first,$fri));
$demo = $trialOpen($priorYear,$first,$fri);
$parallel = $trialOpen($other,$classes[20],$fri+1);
must($parallel['room'] !== $demo['room'] && $parallel['deadline'] === $sat && $demo['roster'] === [], 'one independent test per class and no pupil roster');
must($trialOpen($teacher,$first,$fri+2)['id'] === $demo['id'], 'another teacher joins the same classroom, not a second room');
denied(409,fn()=>cl_ticket($trial,$demo,$student,$bind($demo,'PA_Student'),$fri+2));
$demoToken = cl_ticket($trial,$demo,$teacher,$bind($demo,'PA_Teacher'),$fri+2);
must($demoToken['lesson'] === $demo['id'], 'ticket for another active teacher');
cl_event_apply($s,$trial,['event'=>'room_started','room'=>['name'=>$demo['room']]],$fri+1,$rpc);
$wire($demo['room'],['teacher-4'=>'PA_StaffOld','teacher-1'=>'PA_Teacher']);
$pulse(10,$priorYear,'PA_StaffOld',$fri+3); $pulse(10,$teacher,'PA_Teacher',$fri+3);
$liveDemo = cl_reconcile($s,$trial,$first,cl_active($s,10),$fri+3,$rpc);
must(cl_check_live($trial,$liveDemo,$teacher,$bind($liveDemo,'PA_Teacher'),$fri+3)['phase'] === 'live', 'both teachers become admitted after the verified event and presence');
$demoWake = $bind($liveDemo,'PA_StaffOld') + ['mode'=>'system'];
denied(403,fn()=>cl_set_wake($s,$liveDemo,$teacher,$bind($liveDemo,'PA_Teacher') + ['mode'=>'idle']));
must(cl_set_wake($s,$liveDemo,$priorYear,$demoWake)['wakeMode'] === 'system', 'only the teacher who started the Friday lesson may change wake');
$liveDemo = cl_active($s,10);
denied(409,fn()=>cl_check_live($cfg,$liveDemo,$teacher,$bind($liveDemo,'PA_Teacher'),$fri+3));
denied(409,fn()=>cl_ticket($cfg,$liveDemo,$teacher,$bind($liveDemo,'PA_Teacher'),$fri+3));
cl_close_lesson($s,$trial,$liveDemo,$fri+4,$rpc);
must(!$s->get('lesson',$demo['id']) && !cl_active($s,10),'manual close purges the practice instance');
$later = $trialOpen($teacher,$first,$sat-2);
must($later['id'] !== $demo['id'] && $later['deadline'] === $sat, 'reopens with a fresh identity well beyond the normal two-hour limit');
$lastToken = explode('.',cl_ticket($trial,$later,$teacher,$bind($later,'PA_Teacher'),$sat-1)['token']);
must(cl_event_json($lastToken[1])['exp'] === $sat, 'last ticket is clipped to the midnight cutoff');
denied(409,fn()=>cl_ticket($trial,$later,$teacher,$bind($later,'PA_Teacher'),$sat));
must(cl_reconcile($s,$trial,$first,$later,$sat,$rpc) === null && !isset($sfus[$later['room']]), 'midnight ends the demo');
cl_event_apply($s,$cfg,['event'=>'participant_left','room'=>['name'=>$parallel['room']]],$fri+5,$rpc);
must(!cl_active($s,20) && !isset($sfus[$parallel['room']]), 'revoking Friday also closes an in-flight practice via signed event reconciliation');
// Opt-in with pupils: same authenticated membership, real presence timing, ephemeral results.
$withPupils = array_replace($trial, ['friday_test'=>['date'=>'2026-11-20','id'=>str_repeat('f',32),'students'=>true]]);
$start = $fri+100;
$pupilDemo = cl_open_lesson($s,$ctx,$withPupils,$first,$priorYear,['slot'=>CL_FRIDAY_SLOT],$start,$rpc);
$room = $pupilDemo['room'];
$wrongClass = cl_school_user($db,['student_id'=>2],$year);
must($pupilDemo['roster'] === [] && cl_can_lesson($pupilDemo,$student) && !cl_can_lesson($pupilDemo,$wrongClass), 'opt-in only authorizes own class; do not copy the roster');
$staffWire = ['teacher-4'=>'PA_PilotStaff'];
$wire($room,$staffWire);
$pulse(10,$priorYear,'PA_PilotStaff',$start+1);
$unconfirmed = cl_reconcile($s,$withPupils,$first,cl_active($s,10),$start+1,$rpc);
denied(409,fn()=>cl_ticket($withPupils,$unconfirmed,$student,$bind($unconfirmed,'PA_Student'),$start+1));
cl_event_apply($s,$withPupils,['event'=>'participant_joined','room'=>['name'=>$room]],$start+2,$rpc);
$practicing = cl_reconcile($s,$withPupils,$first,cl_active($s,10),$start+2,$rpc);
denied(409,fn()=>cl_ticket($withPupils,$practicing,$wrongClass,$bind($practicing,'PA_OtherStudent'),$start+2));
must(cl_ticket($withPupils,$practicing,$student,$bind($practicing,'PA_Student'),$start+2)['lesson'] === $pupilDemo['id'], 'pupil gets a ticket after confirmed staff arrives');
$wire($room,$staffWire+$studentWire);
$pulse(10,$student,'PA_Student',$start+4);
$practicing = cl_reconcile($s,$withPupils,$first,cl_active($s,10),$start+5,$rpc);
must($practicing['seconds'] === [] && array_keys($practicing['roster']) === ['student-1'], 'only consecutive verified intervals count');
$practicing = cl_reconcile($s,$withPupils,$first,cl_active($s,10),$start+8,$rpc);
must($practicing['seconds'] === ['student-1'=>3], 'same server-clock accounting used for pupil practice');
cl_chat($s,cl_check_live($withPupils,$practicing,$student,$bind($practicing,'PA_Student'),$start+8),$student,'پیام تمرینی',$start+8);
cl_event_apply($s,$withPupils,['event'=>'participant_left','room'=>['name'=>$room],'participant'=>['identity'=>'teacher-4','sid'=>'PA_PilotStaff']],$start+9,$rpc);
$paused = cl_active($s,10);
must($paused['phase']==='paused' && $paused['room'] === null && !isset($sfus[$room]) && $paused['seconds'] === ['student-1'=>3], 'last teacher leaves: pupil time freezes and transport closes');
denied(409,fn()=>cl_ticket($withPupils,$paused,$student,$bind($practicing,'PA_Student'),$start+10));
$resumed = cl_open_lesson($s,$ctx,$withPupils,$first,$teacher,['slot'=>CL_FRIDAY_SLOT],$start+120,$rpc);
must($resumed['id'] === $paused['id'] && $resumed['room'] !== $room && $resumed['seconds'] === ['student-1'=>3], 'practice resumes with the same timer after the uncounted wait');
$withoutPupils = $withPupils;
unset($withoutPupils['friday_test']['students']);
must(cl_reconcile($s,$withoutPupils,$first,$resumed,$start+121,$rpc) === null && !$s->get('lesson',$resumed['id'])
    && $s->rows('chat:'.$resumed['id'],4) === [], 'revoking pupil opt-in closes and clears test activity even if the grant ID stayed the same');
must($s->rows('reports',100) === $normalReportRows && $s->rows('host-pending',100) === $pendingRows
    && $db->query('SELECT * FROM class_schedules ORDER BY id')->fetchAll(PDO::FETCH_ASSOC) === $beforeSchedule,
    'the demo never publishes reports, sends notices or modifies school scheduling');
// A real Friday lesson keeps its original assignment and cannot be preempted by the pilot.
$db->exec("INSERT INTO class_schedules (id,teacher_id,class_name,academic_year,day_of_week,period_num,subject_name) VALUES (990,1,'هفتم1','1405/1406','جمعه','زنگ 1','درس رسمی جمعه')");
$morning = $fri+8*3600+35*60;
$real = cl_open_lesson($s,$ctx,$trial,$first,$teacher,['slot'=>990],$morning,$rpc);
must(!cl_is_friday_test($real) && $real['deadline'] === $fri+9*3600+15*60, 'the weekly bell and its deadline remain ordinary even during a Friday pilot');
denied(409,fn()=>cl_ticket($trial,$real,$priorYear,$bind($real,'PA_StaffOld'),$morning));
denied(409,fn()=>$trialOpen($priorYear,$first,$morning));
cl_close_lesson($s,$trial,$real,$morning+1,$rpc);
if ($mysql) foreach ([$table,'academic_years','settings','classes','students','teachers','admins','class_schedules','bale_bot_users','telegram_bot_users'] as $name) $db->exec("DROP TABLE `$name`");
echo "PASS calendar, access roles, Friday practice, configured bell windows, native events, trusted presence, waiting/resume, reports, board/chat and conditional school bot delivery\n";
