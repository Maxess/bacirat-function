<?php
declare(strict_types=1);
require __DIR__ . '/../school-addon/upload/online-classroom/events.php';

function ok(bool $condition, string $why): void { if (!$condition) throw new RuntimeException($why); }
function deny(int $code, callable $fn): void {
    try { $fn(); } catch (ClassroomError $error) { ok($error->status === $code, 'incorrect denial: ' . $error->status); return; }
    throw new RuntimeException('expected refusal');
}
$db = new PDO('sqlite::memory:', null, null, [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
$table = 'mtg_plug_store_' . str_repeat('a', 32);
$db->exec("CREATE TABLE `$table` (bucket TEXT,item TEXT,payload TEXT,PRIMARY KEY(bucket,item));
    CREATE TABLE academic_years (year_name TEXT,status INT,is_default INT);
    INSERT INTO academic_years VALUES ('1405/1406',1,1),('1404/1405',1,0);
    CREATE TABLE settings (key_name TEXT PRIMARY KEY,key_value TEXT);
    INSERT INTO settings VALUES ('current_academic_year','1405/1406'), ('school_name','مدرسهٔ پژوهش');
    CREATE TABLE classes (id INT PRIMARY KEY,name TEXT,academic_year TEXT);
    INSERT INTO classes VALUES (15,'هفتم ۱','1405/1406'),(16,'هفتم ۲','1405/1406'),(17,'هفتم ۳','1404/1405'),(18,'هفتم ۴','1405/1406'),(19,'هفتم ۵','1405/1406');
    CREATE TABLE admins (id INT PRIMARY KEY,name TEXT,role TEXT,permissions TEXT,status INT);
    INSERT INTO admins VALUES (1,'مدیر کل','super_admin','[]',1),(2,'مدیر کلاس','admin','[\"manage_classes\"]',1),
      (3,'مدیر بدون مجوز','admin','[]',1),(4,'مدیر غیرفعال','super_admin','[]',0);
    CREATE TABLE bot_chats (platform TEXT,chat_id TEXT,title TEXT,chat_type TEXT,status TEXT);
    INSERT INTO bot_chats VALUES ('bale','-201','گروه اول','group','member'),('bale','-202','گروه دوم','group','member'),
      ('bale','-206','گروه خارج‌شده','group','left'),('bale','111','خصوصی','private','member'),('bale','-208','کانال','channel','member'),
      ('rubika','g0KnownGroup','گروه روبیکا','private','member'),('telegram','-100321','کلاس تلگرام','supergroup','member')");
$s = new ClassroomStore($db, $table);
$cfg = ['record_lessons'=>true,'screen_share'=>true,'student_screen_share'=>true,'recording_secret'=>str_repeat('a',48),'api_key'=>str_repeat('k',16),'api_secret'=>str_repeat('s',40),'room_prefix'=>'test-' . str_repeat('0',20)];
$teacher = ['role'=>'teacher','id'=>7,'identity'=>'teacher-7','office'=>null];
$student = ['role'=>'student','id'=>12,'identity'=>'student-12'];
$admin = cl_school_user($db, ['admin_id'=>2], '1405/1406');
$super = cl_school_user($db, ['admin_id'=>1], '1405/1406');
$deputy = ['role'=>'teacher','id'=>8,'office'=>'deputy','identity'=>'teacher-8'];
foreach ([3,4] as $unauthorized) deny(403, fn()=>cl_school_user($db, ['admin_id'=>$unauthorized], '1405/1406'));
$t = (new DateTimeImmutable('2026-11-18 09:30:00', new DateTimeZone('Asia/Tehran')))->getTimestamp();
$class = cl_classes($db, '1405/1406')[15];
$prefixes = ['bale'=>'https://bale.test/bot-fixture/','rubika'=>'https://rubika.test/v3/fixture/','telegram'=>'https://tg.test/bot-fixture/'];
$GLOBALS['MTAG_PLUGIN_CONTEXT'] = ['recording_bot'=>static function (string $provider) use (&$prefixes): ?array {
    return isset($prefixes[$provider]) ? ['revision'=>hash('sha256', $prefixes[$provider]), 'prefix'=>$prefixes[$provider]] : null;
}];
$checks = [];
$member = 'member';
$request = static function (string $url, array $params) use (&$checks, &$member): array {
    $checks[] = [basename($url), $params];
    if (str_ends_with($url, '/getMe')) return ['ok'=>true,'result'=>['id'=>3]];
    if (str_ends_with($url, '/getChatMember')) return ['ok'=>true,'result'=>['status'=>$member,'user'=>['id'=>3]]];
    $id = $params['chat_id'];
    if ($id === 'g0KnownGroup') return ['status'=>'OK','data'=>['chat'=>['chat_id'=>$id,'chat_type'=>'Group','title'=>'گروه معتبر']]];
    return ['ok'=>true,'result'=>['id'=>(int)$id,'type'=>'group','title'=>'گروه تأییدشده']];
};
$rpc = static function (string $method): array { ok($method === 'DeleteRoom', 'worker can only finalize known expired/closing rooms'); return []; };
$worker = static fn(string $action, ?string $id, int $at, array $extra = []) => cl_rec_worker($s, $cfg,
    ['action'=>$action,'protocol'=>2,'worker'=>str_repeat('a',32),'id'=>$id,'available'=>true] + $extra, $at, $rpc, $request);
$lesson = static function (int $stamp, int $number = 1, int $classId = 15) use ($s, $cfg, $class): array {
    $id = str_pad(dechex($number), 32, '0', STR_PAD_LEFT);
    $room = $cfg['room_prefix'] . '-v4-c' . $classId . '-' . str_pad(dechex($number), 24, '0', STR_PAD_LEFT);
    $live = ['id'=>$id,'class_id'=>$classId,'class_name'=>$class['name'],'year'=>$class['year'],'teacher'=>7,'slot'=>1,'subject'=>'ریاضی',
        'period'=>'زنگ ۱','date'=>cl_day($stamp)->format('Y-m-d'),'phase'=>'live','sample'=>$stamp,'room'=>$room,'started'=>$stamp-10,
        'created'=>$stamp-11,'deadline'=>$stamp+200,'staff'=>['teacher-7'=>'PA_staff'],'active_seconds'=>10,
        'pulses'=>['teacher-7'=>['sid'=>'PA_staff','at'=>$stamp]],'seen'=>['teacher-7'=>'PA_staff']];
    $s->put('lesson', $id, $live);
    $s->put('active', (string)$classId, ['id'=>$id]);
    return $live;
};
$close = static function (array $live, array $actor, bool $yes, int $at, ?callable $end = null) use ($s, $cfg, $rpc): ?string {
    $decision = cl_rec_close_choice($s, $cfg, $live, $actor, ['recording'=>$live['id'],'sendRecording'=>$yes], $at);
    $live['recording_consent'] = $decision === 'queued';
    cl_close_lesson($s, $cfg, $live, $at, $end ?? $rpc);
    return $decision;
};
foreach (['', 'Bearer ' . $cfg['api_secret'], null] as $auth) deny(403, fn()=>cl_rec_worker_auth($cfg, $auth));
cl_rec_worker_auth($cfg, 'Bearer ' . $cfg['recording_secret']);
ok(count(cl_rec_inventory($db, 'bale')) === 2, 'only member groups, excludes channel and private');
foreach (['bale:111','bale:-206','bale:-208','bale:-204','unrecognized:-201'] as $bad) deny(409, fn()=>cl_rec_set_link($s,$cfg,$class,$bad,$request));
deny(409, fn()=>cl_rec_set_link($s,$cfg,$class,'bale:-201',fn()=>['ok'=>true,'result'=>['id'=>-202,'type'=>'group']]));
deny(409, fn()=>cl_rec_set_link($s,$cfg,$class,'bale:-201',static function ($url,$params) use ($db,$request) {
    $db->exec("UPDATE bot_chats SET status='left' WHERE chat_id='-201'");
    return $request($url,$params);
}));
$db->exec("UPDATE bot_chats SET status='member' WHERE chat_id='-201'");
cl_rec_set_link($s,$cfg,$class,'bale:-201',$request);
ok(array_filter($checks, fn($call)=>$call[0] === 'getChatMember') === [], 'Bale assignment does not require admin rights');
$other = cl_classes($db,'1405/1406')[16];
cl_rec_set_link($s,$cfg,$other,'telegram:-100321',$request);
ok(cl_rec_link($s,$other)['provider'] === 'telegram', 'ordinary Telegram group member assignable');
$member = 'left';
deny(409, fn()=>cl_rec_set_link($s,$cfg,$other,'telegram:-100321',$request));
cl_rec_set_link($s,$cfg,$other,'rubika:g0KnownGroup',$request);
ok(cl_rec_link($s,$other)['title'] === 'گروه معتبر', 'Rubika getChat verifies inventory candidate even with missing update type');
ok(cl_rec_link($s,$class)['chat'] === '-201' && cl_rec_link($s, array_replace($class,['year'=>'1404/1405'])) === null, 'scoped by real class and year');

$status = $worker('maintenance',null,$t);
ok($status === ['release'=>CL_REC_RELEASE,'idle'=>true,'worker_ok'=>true,'native_ready'=>false]
    && $s->get('record-worker','heartbeat') === null, 'maintenance probe is read-only');
$s->put('record-pending', str_repeat('3',32), ['id'=>str_repeat('3',32)]);
ok($worker('maintenance',null,$t)['idle'] === false, 'maintenance is blocked even by a pending index without local media');
$s->delete('record-pending', str_repeat('3',32));

$live = $lesson($t);
$id = $live['id'];
foreach ([$teacher,$super,$admin] as $operator) {
    ok(cl_rec_status($s,$cfg,$live,$operator) === ['recording'=>['start'=>true]], 'assigned teacher and authorized admins see the start control');
    ok(cl_rec_status($s,$cfg,$live + ['test'=>'friday'],$operator) === ['recording'=>null], 'Friday test never shows recording controls');
    deny(403, fn()=>cl_rec_start($s,$cfg,$class,$live + ['test'=>'friday'],$operator,$t));
}
$disabled = array_replace($cfg, ['record_lessons'=>false]);
ok(cl_rec_status($s,$disabled,$live,$admin) === ['recording'=>null], 'an admin does not bypass the opt-in');
deny(403, fn()=>cl_rec_start($s,$disabled,$class,$live,$admin,$t));
deny(503, fn()=>cl_rec_start($s,$cfg,$class,$live,$admin,$t));
deny(400, fn()=>cl_rec_worker($s,$cfg,['action'=>'poll','protocol'=>1,'worker'=>str_repeat('a',32)],$t,$rpc));
deny(503, fn()=>cl_rec_start($s,$cfg,$class,$live,$admin,$t));
ok(!$worker('maintenance',null,$t)['idle'], 'an open bell blocks maintenance even without a recording');
$worker('plan',null,$t,['native_release'=>CL_REC_RELEASE]);
ok($worker('maintenance',null,$t)['native_ready'] && !$worker('maintenance',null,$t+19)['native_ready'], 'only the fresh native heartbeat confirms site readiness');
ok($worker('plan',null,$t)['capture'] === [], 'signed deferred worker heartbeat');
deny(409, fn()=>cl_rec_worker($s,$cfg,['action'=>'plan','protocol'=>2,'worker'=>str_repeat('b',32)],$t,$rpc));
foreach ([$student,$deputy] as $unauthorized) {
    ok(cl_rec_status($s,$cfg,$live,$unauthorized) === ['recording'=>null], 'students and unassigned deputies cannot see the start control');
    deny(403, fn()=>cl_rec_start($s,$cfg,$class,$live,$unauthorized,$t));
}
ok(cl_rec_start($s,$cfg,$class,$live,$admin,$t)['recording']['notice'] === 'preparing', 'authorized admin starts a recording explicitly');
$live = cl_active($s,15);
$identity = $live['recorder'];
$peer = ['identity'=>$identity,'state'=>'ACTIVE','permission'=>['can_subscribe'=>true,'can_publish'=>false,'can_publish_data'=>false,'recorder'=>true,'hidden'=>true]];
ok(cl_live_media_peers(['participants'=>[$peer]],$identity) === [], 'hidden recorder is not attendance or staff');
deny(502, fn()=>cl_live_media_peers(['participants'=>[$peer]]));
$peer['permission']['can_publish'] = true;
deny(502, fn()=>cl_live_media_peers(['participants'=>[$peer]],$identity));
$hold = $worker('poll',$id,$t+4);
ok($hold['command'] === 'hold' && !isset($hold['title']), 'notice before capturing A/V');
$job = $worker('poll',$id,$t+5);
$title = ['school'=>'مدرسهٔ پژوهش','subject'=>'ریاضی'];
ok($job['title'] === $title, 'video title uses school settings and the subject of this bell, not the class name');
$db->exec("UPDATE settings SET key_value='نام تازه' WHERE key_name='school_name'");
ok($worker('poll',$id,$t+5)['title'] === $title, 'video title is pinned from recording start for later parts or room reopens');
$legacy = $s->get('recording',$id);
unset($legacy['title']);
$s->put('recording',$id,$legacy);
ok($worker('poll',$id,$t+5)['title'] === array_replace($title,['school'=>'نام تازه']), 'job without cached title has a bounded title fallback');
$s->put('recording',$id,$legacy + ['title'=>$title]);
ok(cl_rec_title($s, ['subject'=>"\u{202E}فیزیک\n" . str_repeat('X',150)])['subject'] === 'فیزیک ' . str_repeat('X',94),
    'worker receives bounded, single-line subject text with no directional overrides');
$claims = json_decode(base64_decode(strtr(explode('.',$job['token'])[1],'-_','+/')),true);
ok($job['room'] === $live['room'] && $claims['sub'] === $identity && $claims['video']['canPublish'] === false
    && $claims['video']['recorder'] === true && $claims['video']['hidden'] === true, 'only a hidden/receiving JWT for this room');
$renewal = $worker('poll',$id,$t+6,['running'=>$live['room']]);
ok($renewal['command'] === 'record' && !isset($renewal['title']) && !isset($renewal['token']), 'token and title are only for capture startup');
ok(cl_rec_status($s,$cfg,$live,$student) === ['recording'=>['notice'=>'live']], 'students only see recording indicator, not destination or control');
foreach ([$teacher,$admin] as $operator) {
    $controls = cl_rec_status($s,$cfg,$live,$operator)['recording'];
    ok($controls['confirm'] && $controls['canSend'] && !isset($controls['start']) && $controls['delayMinutes'] === 180, 'both authorized closers can approve sending, but cannot start a second capture');
}
ok(!cl_rec_status($s,$cfg,$live,$deputy)['recording']['canSend'], 'unassigned deputy cannot approve sending');
$board = $worker('board',$id,$t+6,['room'=>$live['room'],'board'=>0,'ink'=>0]);
ok(isset($board['board']) && !isset($board['chat']) && $board['board']['write'] === false, 'recorder reads only current board');
$view = ['zoom'=>200,'crop'=>[5000,0,10000,10000]];
cl_board_read($s,$live,$teacher,['lesson'=>$id,'epoch'=>cl_epoch($live),'sid'=>'PA_staff','board'=>1,'view'=>$view],null,$t+6);
$delta = $worker('board',$id,$t+6,['room'=>$live['room'],'board'=>1,'ink'=>0,'compact'=>true,'view'=>CL_VIEW_FULL])['board'];
ok($delta['view'] === $view && $delta['rev'] === 1 && !$delta['write'] && !isset($delta['navigate']) && !isset($delta['doc']),
    'worker receives the cropped viewport in the compact delta but cannot change it');
deny(409, fn()=> $worker('board',$id,$t+6,['room'=>'other']));
$scope = ['lesson'=>$id,'epoch'=>cl_epoch($live),'sid'=>'PA_staff'];
$setBoard = ['source'=>'board','enabled'=>true] + $scope;
$capable = $worker('poll',$id,$t+6,['archiveLayout'=>true]);
$defaultLayout = ['board'=>false,'screen'=>null];
ok($capable['archive'] === $defaultLayout + ['screenOwner'=>null] && $capable['teacher'] === 'teacher-7'
    && $capable['mediaMode'] === 'standard', 'archive starts with only the primary source and the actual scheduled teacher');
ok(cl_rec_status($s,$cfg,$live,$student)['recording'] === ['notice'=>'preparing'], 'layout metadata is not sent to pupils');
deny(403, fn()=>cl_rec_set_source($s,$cfg,$live,$student,$setBoard,$t+6));
deny(403, fn()=>cl_rec_set_source($s,$cfg,$live,$admin,$setBoard,$t+6));
deny(409, fn()=>cl_rec_set_source($s,$cfg,$live,$teacher,array_replace($setBoard,['sid'=>'PA_wrong']),$t+6));
deny(400, fn()=>cl_rec_set_source($s,$cfg,$live,$teacher,array_replace($setBoard,['enabled'=>1]),$t+6));
ok(cl_rec_set_source($s,$cfg,$live,$teacher,$setBoard,$t+6)['archive'] === ['board'=>true,'screen'=>null], 'active presenter can include the board');
$share = ['source'=>'screen','track'=>'TR_original','publisherSid'=>'PA_Pupil','enabled'=>true] + $scope;
deny(409, fn()=>cl_rec_set_source($s,$cfg,$live,$teacher,$share,$t+6));
$live['camera_codec'] = 'vp8';
$live['seen']['student-12'] = 'PA_Pupil';
$live['screen'] = ['identity'=>'student-12','sid'=>'PA_Pupil','until'=>$t+120];
$s->put('lesson',$id,$live);
deny(409, fn()=>cl_rec_set_source($s,$cfg,$live,$teacher,array_replace($share,['publisherSid'=>'PA_previous']),$t+6));
deny(409, fn()=>cl_rec_set_source($s,$cfg,$live,$teacher,array_replace($share,['track'=>'PA_Pupil']),$t+6));
ok(cl_rec_set_source($s,$cfg,$live,$teacher,$share,$t+6)['archive'] === ['board'=>true,'screen'=>'TR_original'], 'screen selection binds to the current publication');
ok(cl_rec_set_source($s,$cfg,$live,$teacher,array_replace($share,['track'=>'TR_old','enabled'=>false]),$t+6)['archive']['screen'] === 'TR_original',
    'a delayed unselect for a different track does not clear the current choice');
$pointer = [[5400,1900,70],[6800,6000,0]];
cl_board_read($s,$live,$teacher,$scope+['board'=>1,'aim'=>$pointer],null,$t+6);
$recordRead = ['room'=>$live['room'],'board'=>1,'ink'=>0,'compact'=>true];
$captured = $worker('board',$id,$t+6,$recordRead);
$cue = $captured['board']['laser'];
ok($captured['archive'] === ['board'=>true,'screen'=>'TR_original','screenOwner'=>['identity'=>'student-12','sid'=>'PA_Pupil']] && array_keys($cue) === ['points'] && count($cue['points']) === 2,
    'recorder reads an anonymous trail and current source flags in the existing compact delta');
ok($cue['points'][1][2] <= CL_LASER_TTL_MS && !isset($captured['board']['ops']), 'laser points expire and do not become ink');
$activity = cl_activity($s,$id);
$activity['laser']['rev']++;
$s->put('activity',$id,$activity);
ok(!isset($worker('board',$id,$t+6,$recordRead)['board']['laser']), 'recorder does not replay a trail from another board revision');
$activity['laser']['rev']--;
$activity['laser']['at'] -= 2;
$s->put('activity',$id,$activity);
ok(!isset($worker('board',$id,$t+6,$recordRead)['board']['laser']), 'expired laser is not replayed');
ok(cl_rec_layout($cfg,$s->get('recording',$id),array_replace($live,['room'=>substr($live['room'],0,-24) . str_repeat('f',24)])) === $defaultLayout,
    'both toggles reset on a new room transport');
unset($live['screen']);
$s->put('lesson',$id,$live);
ok($worker('board',$id,$t+6,$recordRead)['archive']['screen'] === null, 'a revoked screen lease cannot keep priority');
cl_board_read($s,$live,$teacher,$scope+['board'=>1,'aim'=>$pointer],null,$t+6);
$worker('poll',$id,$t+6,['running'=>$live['room']]);
ok(isset($worker('board',$id,$t+6,$recordRead)['archive']), 'deferred worker always carries board and layout state');
// The recorder is not a roster entry and doesn't need the teacher's tab.
$live['pulses'][$admin['identity']] = ['sid'=>'PA_office','at'=>$t+6];
$live['seen'][$admin['identity']] = $live['staff'][$admin['identity']] = 'PA_office';
$s->put('lesson',$id,$live);
cl_event_apply($s,$cfg,['event'=>'participant_left','room'=>['name'=>$live['room']],
    'participant'=>['identity'=>$identity,'sid'=>'PA_recorder']],$t+6,$rpc);
ok(cl_active($s,15) === $live, 'recorder leave does not change the class roster');
cl_event_apply($s,$cfg,['event'=>'participant_left','room'=>['name'=>$live['room']],
    'participant'=>['identity'=>'teacher-7','sid'=>'PA_staff']],$t+7,$rpc);
$live = cl_active($s,15);
ok($live['phase'] === 'live' && !isset($live['staff']['teacher-7'])
    && $worker('poll',$id,$t+7)['command'] === 'record', 'capture continues after teacher leaves if another authorized staff member keeps the class live');
$stored = cl_activity($s,$id);
$stored['view']['at'] -= 1; // Separate the two viewport writes without waiting in real time.
$s->put('activity',$id,$stored);
$adminView = ['zoom'=>150,'crop'=>[0,1000,6500,7900]];
cl_board_read($s,$live,$admin,['lesson'=>$id,'epoch'=>cl_epoch($live),'sid'=>'PA_office','board'=>1,'view'=>$adminView],null,$t+7);
$recorded = $worker('board',$id,$t+7,['room'=>$live['room'],'board'=>1,'ink'=>0,'compact'=>true])['board'];
ok($recorded['view'] === $adminView && $recorded['cursor'] === 0 && !isset($recorded['navigate']),
    'the same recorder protocol receives the manager’s crop without recording view changes as ink');
$staleRoom = $live['room'];
$paused = array_replace($live, ['phase'=>'paused','room'=>null]);
$s->put('lesson',$id,$paused);
ok($worker('poll',$id,$t+6)['command'] === 'hold', 'do not capture while the school waits for staff');
$live['room'] = substr($staleRoom,0,-24) . str_repeat('c',24);
$s->put('lesson',$id,$live);
$resume = $worker('poll',$id,$t+6,['archiveLayout'=>true]);
ok($resume['room'] === $live['room'] && $resume['teacher'] === 'teacher-7' && $resume['archive'] === $defaultLayout + ['screenOwner'=>null],
    'resume uses the same teacher and fresh defaults on the new transport');
deny(409, fn()=> $worker('board',$id,$t+6,['room'=>$staleRoom]));
deny(409, fn()=> $worker('claim',$id,$t+7,['part'=>1,'total'=>2]));
deny(409, fn()=>cl_rec_close_choice($s,$cfg,$live,$teacher,['recording'=>$id,'sendRecording'=>'true'],$t+7));
deny(409, fn()=>cl_rec_close_choice($s,$cfg,$live,$teacher,['recording'=>str_repeat('2',32),'sendRecording'=>true],$t+7));
deny(403, fn()=>cl_rec_close_choice($s,$cfg,$live,$deputy,['recording'=>$id,'sendRecording'=>true],$t+7));
deny(409, fn()=>cl_rec_close_choice($s,$cfg,$live,$admin,[],$t+7));
deny(502, fn()=>$close($live,$admin,true,$t+7,fn()=>throw new ClassroomError(502,'simulated close timeout')));
deny(409, fn()=> $worker('claim',$id,$t+8,['part'=>1,'total'=>2]));
ok($worker('poll',$id,$t+11)['command'] === 'hold' && $s->get('lesson',$id)['phase'] === 'finished', 'worker retries close and queues only after commit');
$eligible = $t+11+10800;
$closed = $s->get('recording',$id);
ok($closed['closed_at'] === $t+11 && $closed['not_before'] === $eligible && $closed['not_before'] < $live['deadline']+10800,
    'three full hours start at actual successful closure, not consent, the schedule or capture start');
deny(409, fn()=> $worker('rendered',$id,$eligible-1,['total'=>2]));
ok($worker('plan',null,$eligible-1)['work'] === null && $worker('poll',$id,$eligible-1)['command'] === 'hold', 'no render at 2h59m59s');
$unrecorded = $lesson($eligible-10, 80, 16);
ok($worker('plan',null,$eligible)['work'] === null && $worker('poll',$id,$eligible)['command'] === 'hold', 'an open unrecorded bell has priority');
deny(409, fn()=> $worker('rendered',$id,$eligible,['total'=>2]));
cl_close_lesson($s,$cfg,$unrecorded,$eligible+1,$rpc);
ok($worker('plan',null,$eligible+1)['work'] === $id && $worker('poll',$id,$eligible+1)['command'] === 'render', 'eligible after the next bell ends');
deny(409, fn()=> $worker('claim',$id,$eligible+1,['part'=>1,'total'=>2]));
ok($worker('rendered',$id,$eligible+1,['total'=>2])['command'] === 'deliver', 'worker commits finalized film before upload');
$permit = $worker('claim',$id,$eligible+2,['part'=>1,'total'=>2]);
ok($permit['command'] === 'send' && $permit['chat'] === '-201', 'exact approved group and first part');
deny(409, fn()=> $worker('claim',$id,$eligible+3,['part'=>1,'total'=>2]));
// Date-dependent lock bounds use the worker clock; rebind at current wall time only for this lock assertion.
$stored = $s->get('recording',$id);
$locked = $stored;
$locked['flight']['until'] = time()+90;
$s->put('recording',$id,$locked);
deny(409, fn()=>cl_rec_set_link($s,$cfg,$class,'bale:-202',$request));
$s->put('recording',$id,$stored);
$receipt = ['part'=>1,'attempt'=>$permit['attempt'],'result'=>'accepted','message'=>'123'];
$worker('result',$id,$eligible+4,$receipt);
$worker('result',$id,$eligible+5,$receipt);
ok($worker('poll',$id,$eligible+6)['sent'] === 1, 'receipt replay is idempotent');
$part2 = $worker('claim',$id,$eligible+7,['part'=>2,'total'=>2]);
$worker('result',$id,$eligible+8,['part'=>2,'attempt'=>$part2['attempt'],'result'=>'accepted','message'=>'124']);
ok($worker('poll',$id,$eligible+9)['command'] === 'purge' && !$s->get('record-pending',$id), 'all parts confirmed then purge and release queue item');
$worker('cleaned',$id,$eligible+10);
ok($s->get('recording',$id)['cleaned'] === $eligible+10 && $s->get('recording',$id)['receipts'] === [1=>'123',2=>'124'], 'keep both delivery receipts after cleanup');

foreach (['no','expiry','changed','left','uncertain'] as $i=>$scenario) {
    $now = $t + 30 * ($i + 1);
    $live = $lesson($now, $i+2);
    $id = $live['id'];
    $worker('plan',null,$now);
    cl_rec_start($s,$cfg,$class,$live,$teacher,$now);
    $live = cl_active($s,15);
    ok($worker('poll',$id,$now+5)['command'] === 'record', 'new bell has its own recording');
    if ($scenario === 'no') {
        ok($close($live,$admin,false,$now+6) === 'discarded', 'no by authorized closer');
        ok($worker('poll',$id,$now+10806)['command'] === 'purge' && !$s->get('record-pending',$id), 'no means never render, even after the delay');
    }
    elseif ($scenario === 'expiry') ok($worker('poll',$id,$live['deadline']+1)['command'] === 'purge', 'expired without a decision');
    else {
        ok($close($live,$teacher,true,$now+6) === 'queued', 'positive decision binds to original link');
        if ($scenario === 'changed') cl_rec_set_link($s,$cfg,$class,'bale:-202',$request);
        if ($scenario === 'left') $db->exec("UPDATE bot_chats SET status='left' WHERE chat_id='-201'");
        $ready = $now+6+10800;
        if ($scenario === 'uncertain') {
            $worker('rendered',$id,$ready,['total'=>1]);
            $claim = $worker('claim',$id,$ready+1,['part'=>1,'total'=>1]);
            ok($claim['command'] === 'send', 'claim valid');
            ok($worker('poll',$id,$ready+250)['command'] === 'retain', 'lost response never re-sent');
            deny(409, fn()=>$worker('claim',$id,$ready+251,['part'=>1,'total'=>1]));
            ok($worker('poll',$id,$live['deadline']+CL_REC_RETENTION+1)['command'] === 'purge', 'bounded retention');
        } else ok($worker('poll',$id,$ready)['command'] === 'purge', 'changed mapping or bot removal prevents making and rerouting');
    }
    $db->exec("UPDATE bot_chats SET status='member' WHERE chat_id='-201'");
    cl_rec_set_link($s,$cfg,$class,'bale:-201',$request);
    deny(409, fn()=> $worker('claim',$id,$now+260,['part'=>1,'total'=>1]));
}
// Simultaneous rooms and a new period can record while older jobs await their delay.
$now = $t + 900;
$classes = cl_classes($db, '1405/1406');
foreach ([18,19] as $cid) cl_rec_set_link($s,$cfg,$classes[$cid],'bale:-201',$request);
$rooms = [];
$worker('plan',null,$now);
foreach ([15,16,18,19] as $i=>$cid) $rooms[$cid] = $lesson($now,100+$i,$cid);
foreach ([15,16,18] as $cid) cl_rec_start($s,$cfg,$classes[$cid],$rooms[$cid],$teacher,$now);
deny(409, fn()=>cl_rec_start($s,$cfg,$classes[19],$rooms[19],$teacher,$now));
$ids = array_map(static fn($cid) => $rooms[$cid]['id'], [15,16,18]);
ok($worker('plan',null,$now+5)['capture'] === $ids, 'three independent capture leases');
foreach ($ids as $index=>$item) ok($worker('poll',$item,$now+5)['room'] === $rooms[[15,16,18][$index]]['room'], 'room-scoped capture');
$custom = array_replace($cfg,['recording_delay_minutes'=>90]);
$first = cl_active($s,15);
$first['recording_consent'] = cl_rec_close_choice($s,$custom,$first,$teacher,['recording'=>$first['id'],'sendRecording'=>true],$now+9) === 'queued';
cl_close_lesson($s,$custom,$first,$now+9,$rpc);
cl_rec_start($s,$cfg,$classes[19],$rooms[19],$teacher,$now+9);
ok(count($worker('plan',null,$now+10)['capture']) === 3 && $s->get('recording',$first['id'])['not_before'] === $now+9+5400,
    'approved bell frees source slot immediately, custom delay is pinned at close');
$second = cl_active($s,19);
$second['recording_consent'] = true;
cl_close_lesson($s,$custom,$second,$now+15,$rpc);
foreach ([16,18] as $cid) $close(cl_active($s,$cid),$teacher,false,$now+17);
$queue = $worker('plan',null,$now+5500);
ok($queue['work'] === $first['id'] && $queue['capture'] === [], 'oldest eligible closure renders first');
ok($worker('poll',$second['id'],$now+5414)['command'] === 'hold', 'delay is evaluated per individual end time');
$next = $lesson($now+5520, 123, 15);
ok($worker('plan',null,$now+5520)['work'] === null, 'a following period preempts an eligible job');
cl_rec_start($s,$cfg,$class,$next,$teacher,$now+5520);
ok($worker('plan',null,$now+5525)['capture'] === [$next['id']], 'back-to-back same class has its own recording');
ok(cl_rec_delay(['recording_delay_minutes'=>0]) === 180 && cl_rec_delay(['recording_delay_minutes'=>720]) === 720, 'safe default and configured delay bounds');
echo "classroom recording checks passed\n";
