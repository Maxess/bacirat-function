<?php
declare(strict_types=1);
require __DIR__ . '/../school-addon/upload/online-classroom/activity.php';

function check(bool $condition, string $what): void {
    if (!$condition) throw new RuntimeException($what);
}
function blocked(callable $call, int $status): void {
    try { $call(); } catch (ClassroomError $error) {
        check($error->status === $status, 'incorrect deny status');
        return;
    }
    throw new RuntimeException('expected denial');
}
$room = 'test-example-v3-c10-cccccccccccccccccccccccc';
$cfg = ['api_key'=>str_repeat('x',24),'api_secret'=>str_repeat('y',64),'camera_share'=>true];
$student = ['identity'=>'student-1','name'=>'دانش‌آموز'];
[$header,$payload,$sig]=explode('.',cl_join_ticket($cfg,$room,$student));
$claims=json_decode(base64_decode(strtr($payload,'-_','+/'),true),true,512,JSON_THROW_ON_ERROR);
check($claims['sub']==='student-1' && $claims['exp']-time()<=35 && $claims['video']['room']===$room, 'entry scope and expiry');
check($claims['video']['canPublish']===false && $claims['video']['canPublishData']===false && !isset($claims['video']['roomAdmin']), 'listener-only token');
check($sig===cl_b64url(hash_hmac('sha256', "$header.$payload", $cfg['api_secret'], true)), 'HS256 signature');
$listener = ['canSubscribe'=>true,'canPublish'=>false,'canPublishData'=>false,'canPublishSources'=>[]];
$limit = static fn(array $sources) => array_replace($listener, ['canPublish'=>!!$sources,'canPublishSources'=>$sources]);
$mic = $limit(['MICROPHONE']);
$cam = $limit(['CAMERA']);
$both = $limit(['CAMERA','MICROPHONE']);
$student['role'] = 'student';
$teacher = ['identity'=>'teacher-1','role'=>'teacher'];
$admin = ['identity'=>'admin-1','role'=>'admin'];
$now = time();
$db = new PDO('sqlite::memory:');
$table = 'mtg_plug_store_' . str_repeat('a',32);
$db->exec("CREATE TABLE `$table` (bucket TEXT,item TEXT,payload TEXT,PRIMARY KEY(bucket,item))");
$s = new ClassroomStore($db,$table);
$seen = ['teacher-1'=>'PA_Staff','student-1'=>'PA_Pupil','student-6'=>'PA_Peer','admin-1'=>'PA_Admin'];
$lesson = ['id'=>str_repeat('b',32),'room'=>$room,'camera_codec'=>'vp8','seen'=>$seen,
    'pulses'=>array_map(fn($sid)=>['sid'=>$sid,'at'=>$now],$seen)];
$s->put('lesson',$lesson['id'],$lesson);
$sfuSids = $seen + ['student-999'=>'PA_Wait'];
$permissions = array_fill_keys(array_keys($sfuSids),$listener);
$tracks = $updates = $broken = [];
$codec = fn($value) => cl_camera_codec(['enabled_codecs'=>$value]);
check(!$codec([['mime'=>'audio/opus']]) && $codec([['mime'=>'video/VP8']]) === 'vp8' && $codec([['mime'=>'video/h264']]) === 'h264','actual room capability');

$peer = static function ($id) use (&$permissions,&$sfuSids,&$tracks,&$broken): array {
    $wire = [];
    foreach ($permissions[$id] as $key=>$value) {
        $name = $id === 'student-6' ? $key : strtolower(preg_replace('/[A-Z]/','_$0',$key));
        $wire[$name] = $value;
    }
    $reply = ['identity'=>$id,'sid'=>$sfuSids[$id],'state'=>'ACTIVE','permission'=>$wire,
        'tracks'=>array_map(static fn($source)=>['source'=>$source], $tracks[$id] ?? [])];
    if (($broken[$id] ?? '') === 'missing-publish') unset($reply['permission']['can_publish'],$reply['permission']['canPublish']);
    if (($broken[$id] ?? '') === 'missing-tracks') unset($reply['tracks']);
    return $reply;
};
$rpc = static function (string $method, string $target, array $data) use (&$permissions,&$sfuSids,&$tracks,&$updates,&$broken,$peer,$room): array {
    check($target === $room && $data['room'] === $room,'permission RPC scoped to this room');
    if ($method === 'ListParticipants') return ['participants'=>array_map($peer,array_keys($sfuSids))];
    check($method === 'UpdateParticipant', 'only the named RoomService read/write');
    $id = $data['identity'];
    if (!isset($sfuSids[$id])) throw new ClassroomError(409, 'gone');
    if (isset($data['permission'])) {
        $fail = $broken[$id] ?? '';
        $updates[] = [$id,$data['permission']];
        if ($fail === 'reconnect') $sfuSids[$id] = 'PA_New';
        if ($fail !== 'ignored') $permissions[$id] = $data['permission'];
        if ($fail !== 'track') $tracks[$id] = array_values(array_intersect($tracks[$id] ?? [], $permissions[$id]['canPublishSources']));
    }
    return $peer($id);
};
$get = fn()=> $s->get('lesson',$lesson['id']);
$set = static fn($actor,$feature,$enabled,$identity=null)=>cl_set_access($s,$cfg,$get(),$actor,compact('feature','enabled','identity'),$now,$rpc);
blocked(fn()=>$set($student,'board',true,'student-1'),403);
blocked(fn()=>$set($teacher,'screen',true),400);
blocked(fn()=>$set($teacher,'mic','true'),400);
blocked(fn()=>$set($teacher,'board',true,'admin-1'),400);
blocked(fn()=>$set($teacher,'mic',true,'student-999'),409);
blocked(fn()=>$set($teacher,'mic',true,['student-1']),400);
blocked(fn()=>cl_set_access($s,$cfg,array_replace($lesson,['camera_codec'=>null]),$teacher,['feature'=>'camera','enabled'=>true],$now,$rpc),409);
$disabled = array_replace($cfg, ['camera_share'=>false, 'screen_share'=>true]);
blocked(fn()=>cl_set_access($s,$disabled,$lesson,$teacher,['feature'=>'camera','enabled'=>true],$now,$rpc),409);
check(cl_webcam_codec($disabled,$lesson)===null && cl_screen_enabled($disabled,$lesson), 'screen works independently when webcam is turned off');
check($get() === $lesson && !$updates,'invalid writes do not modify policy or permissions');

$set($admin,'mic',true,'student-1');
check($updates === [['student-1',$mic]],'individual grant changes only the specified peer');
$tracks['student-1'] = ['MICROPHONE']; // Publishing is not part of any permission grant.
$set($teacher,'camera',true);
check($permissions['student-1'] === $both && $permissions['student-6'] === $cam && $permissions['teacher-1'] === $cam
    && $permissions['admin-1'] === $cam && $permissions['student-999'] === $listener,'group cameras for admitted people, never screen share or data');
check($tracks['student-1'] === ['MICROPHONE'] && !$tracks['student-6'],'grant does not publish, and video does not replace audio');
$tracks['student-1'][] = 'CAMERA';
check(cl_sync_media($disabled,$get(),cl_live_media_peers($rpc('ListParticipants',$room,['room'=>$room])),$rpc)
    && $permissions['student-1'] === $mic && $tracks['student-1'] === ['MICROPHONE'], 'a global webcam denial removes stale sources without touching mic');
$set($teacher,'camera',true);
$tracks['student-1'][] = 'CAMERA';
$set($teacher,'mic',false,'student-1');
check($permissions['student-1'] === $cam && $tracks['student-1'] === ['CAMERA'],'revoking mic preserves independent camera');
$set($teacher,'mic',true);
$set($teacher,'camera',false,'student-6');
check($permissions['student-6'] === $mic && $permissions['student-1'] === $both,'individual exception can override a group default');
$set($teacher,'camera',false);
check($get()['access']['camera'] === ['all'=>false,'members'=>[]] && $permissions['student-1'] === $mic && !$tracks['student-1'],'group revoke clears exceptions and stops video only');

// A newly sampled SID starts with a listener ticket and is granted only after PHP sees admission.
$pending = $get();
$pending['seen']['student-999'] = $sfuSids['student-999'];
$refresh = fn($l) => cl_sync_media($cfg,$l,cl_live_media_peers($rpc('ListParticipants',$room,['room'=>$room])),$rpc);
check($permissions['student-999'] === $listener && $refresh($pending) && $permissions['student-999'] === $mic,'late admission gets the lesson default');
$broken['student-6'] = 'reconnect';
blocked(fn()=>$set($teacher,'camera',true,'student-6'),502);
check($permissions['student-6'] === $listener && $sfuSids['student-6'] === 'PA_New','racing SID is returned to listener');
unset($broken['student-6']);
$set($teacher,'camera',false,'student-6');

foreach (['ignored', 'missing-publish', 'track', 'missing-tracks'] as $failure) {
    $broken['student-1'] = $failure;
    $permissions['student-1'] = $permissions['teacher-1'] = $mic;
    $tracks['student-1'] = $tracks['teacher-1'] = ['MICROPHONE'];
    blocked(fn()=>$set($teacher,'mic',false),502);
    check(!$permissions['teacher-1']['canPublish'] && !$tracks['teacher-1'],'one failed update does not stop the remaining revocations');
    check(!cl_access_enabled($get(),'mic') && $get()['media_pending'],'intent to revoke saved for retry');
}
$broken = [];
$tracks['student-1'] = [];
check($refresh($get()), 'reconciliation succeeds after a transient mismatch');
$permissions['teacher-1'] = array_replace($both, ['canPublishSources'=>[], 'canPublishData'=>true]);
$tracks['teacher-1'] = [1,2,3,4];
check($refresh($get()) && $permissions['teacher-1'] === $listener && !$tracks['teacher-1'],'wildcard SDK state never passes permission checks');
blocked(fn()=>cl_live_media_peers(['participants'=>null]),502);
blocked(fn()=>cl_media_permission(['identity'=>'student-6','permission'=>['canPublish'=>false,'can_publish'=>true]],'student-6'),502);
check(cl_service_grant('UpdateParticipant', $room) === ['roomAdmin'=>true,'room'=>$room], 'admin RPC cannot escape current room');

$pilot = $cfg + ['screen_share'=>true];
$s->put('lesson',$lesson['id'],$get() + ['staff'=>['teacher-1'=>'PA_Staff','admin-1'=>'PA_Admin']]);
$screen = static fn($actor,$sid,$enabled,$opts=null)=>cl_set_screen($s,$opts??$pilot,$get(),$actor,compact('sid','enabled'),$now,$rpc);
blocked(fn()=>$screen($teacher,'PA_Staff',true,$cfg),409);
blocked(fn()=>cl_set_screen($s,$pilot,array_replace($get(),['camera_codec'=>'h264']),$teacher,['sid'=>'PA_Staff','enabled'=>true],$now,$rpc),409);
blocked(fn()=>$screen($student,'PA_Pupil',true),403);
blocked(fn()=>$screen($teacher,'PA_Wrong',true),409);
$set($teacher,'mic',true,'teacher-1');
$set($teacher,'camera',true,'teacher-1');
$tracks['teacher-1'] = ['MICROPHONE','CAMERA'];
$owned = $screen($teacher,'PA_Staff',true);
check($owned['screen'] === ['identity'=>'teacher-1','sid'=>'PA_Staff'] && $permissions['teacher-1'] === $limit(['CAMERA','MICROPHONE','SCREEN_SHARE']),
    'pilot grants exactly one extra video source to a confirmed moderator');
check($tracks['teacher-1'] === ['MICROPHONE','CAMERA'] && $permissions['admin-1'] === $listener, 'no automatic screen or general publish grant');
blocked(fn()=>$screen($admin,'PA_Admin',true),409);
$tracks['teacher-1'][] = 'SCREEN_SHARE';
$current = $get();
cl_refresh_screen($current,$pilot,['teacher-1'=>$peer('teacher-1')],$now+CL_SCREEN_LEASE+1);
check($current['screen']['until'] > $now+CL_SCREEN_LEASE+1,'active presentation holds the lease');
$replaced = $current; $replaced['staff']['teacher-1'] = 'PA_Replaced';
cl_refresh_screen($replaced,$pilot,['teacher-1'=>$peer('teacher-1')],$now+1);
check(cl_screen_state($replaced) === null, 'a stale staff SID never keeps a presentation lease');
$broken['teacher-1'] = 'ignored';
blocked(fn()=>$screen($teacher,'PA_Staff',false),502);
check(cl_screen_state($get()) === null && $get()['media_pending'], 'intent to revoke persists on SFU error');
blocked(fn()=>$screen($admin,'PA_Admin',true),502);
check($permissions['admin-1'] === $listener, 'never grant a second stream until old source is confirmed removed');
unset($broken['teacher-1']);
$screen($admin,'PA_Admin',true);
check($permissions['teacher-1'] === $both && $tracks['teacher-1'] === ['MICROPHONE','CAMERA'] && $permissions['admin-1'] === $limit(['SCREEN_SHARE']),
    'sharing moves without stopping previous independent media');
$current=$get();
cl_refresh_screen($current,$pilot,['admin-1'=>$peer('admin-1')],$now+CL_SCREEN_LEASE+1);
check(cl_screen_state($current) === null && $refresh($current) && $permissions['admin-1'] === $listener,'unused presentation expires and its permit is removed');

echo "PASS listener JWT, media isolation, SFU reconcile, and one opt-in screen-share video source\n";
