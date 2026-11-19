<?php
declare(strict_types=1);
require __DIR__ . '/../school-addon/upload/online-classroom/core.php';

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
$pdo = new PDO('sqlite::memory:', null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
$pdo->exec("CREATE TABLE classes (id INT, name TEXT, academic_year TEXT);
CREATE TABLE students (id INT, first_name TEXT, last_name TEXT, class_name TEXT, academic_year TEXT, status TEXT);
CREATE TABLE teachers (id INT, full_name TEXT, status INT);
CREATE TABLE class_schedules (teacher_id INT, class_name TEXT, academic_year TEXT);
CREATE TABLE admins (id INT, name TEXT, role TEXT, permissions TEXT, status INT);
INSERT INTO classes VALUES (10,'پایه ۸ الف','1404/1405'),(11,'پایه ۸ الف','1403/1404'),(12,'پایه ۸ ب','1404/1405');
INSERT INTO students VALUES (1,'دانش‌آموز','آزمایشی','پایه ۸ الف','۱۴۰۴-۱۴۰۵','active'),(2,'ب','','پایه ۸ ب','1404/1405','active'),(3,'ج','','پایه ۸ الف','1403/1404','active'),(4,'د','','پایه ۸ الف','1404/1405','inactive'),(5,'ه','','پایه ۸ الف',NULL,'active');
INSERT INTO teachers VALUES (1,'دبیر الف',1),(2,'دبیر ب',1),(3,'دبیر ج',0);
INSERT INTO class_schedules VALUES (1,'پایه ۸ الف','1404/1405'),(2,'پایه ۸ الف','1403/1404'),(3,'پایه ۸ الف','1404/1405');
INSERT INTO admins VALUES (1,'مدیر','super_admin','[]',1),(2,'مدیر غیرفعال','super_admin','[]',0),(3,'معاون','admin','[]',1),(4,'مدیر کلاس‌ها','admin','[\"manage_classes\"]',1);");
$cfg = ['class_id' => 10, 'room_prefix' => 'test-school', 'api_key' => str_repeat('x',24), 'api_secret' => str_repeat('y',64)];
$class = cl_class($pdo, $cfg);
check($class['room'] === 'test-school-c10-1404-1405', 'room must be identified by class and year');
$student = cl_actor($pdo, ['student_id'=>'1'], $class);
check($student['identity'] === 'student-1' && !$student['moderator'], 'student identity and limited role');
check(cl_actor($pdo, ['teacher_id'=>1], $class)['moderator'], 'assigned teacher');
check(cl_actor($pdo, ['admin_id'=>1], $class)['moderator'], 'active super admin');
check(cl_actor($pdo, ['admin_id'=>4], $class)['moderator'], 'manager with explicit permission');
foreach ([2,3,4,5] as $id) blocked(fn()=>cl_actor($pdo,['student_id'=>$id],$class),403);
foreach ([2,3] as $id) blocked(fn()=>cl_actor($pdo,['teacher_id'=>$id],$class),403);
foreach ([2,3] as $id) blocked(fn()=>cl_actor($pdo,['admin_id'=>$id],$class),403);
blocked(fn()=>cl_actor($pdo,[],$class),401);
blocked(fn()=>cl_actor($pdo,['teacher_id'=>1,'student_id'=>1],$class),403);
$pdo->exec("INSERT INTO classes VALUES (13,' پایه ۸ الف','1404-1405')");
blocked(fn()=>cl_class($pdo,$cfg),503);

[$header,$payload,$sig]=explode('.',cl_join_ticket($cfg,$class['room'],$student));
$claims=json_decode(base64_decode(strtr($payload,'-_','+/'),true),true,512,JSON_THROW_ON_ERROR);
check($claims['sub']==='student-1' && $claims['exp']-time()<=120, 'entry scope and expiry');
check($claims['video']['canPublish']===false && $claims['video']['canPublishData']===false && !isset($claims['video']['roomAdmin']), 'listener-only token');
check($sig===cl_b64url(hash_hmac('sha256', "$header.$payload", $cfg['api_secret'], true)), 'HS256 signature');

$room = $class['room'];
$listener = ['canSubscribe' => true, 'canPublish' => false, 'canPublishData' => false, 'canPublishSources' => []];
$audioOnly = array_replace($listener, ['canPublish' => true, 'canPublishSources' => ['MICROPHONE']]);
$permissions = ['student-1' => $listener, 'student-6' => $listener, 'teacher-1' => $listener];
$tracks = [];
$updates = [];
$broken = [];
$rpc = static function (string $method, array $data) use (&$permissions, &$tracks, &$updates, &$broken, $room): array {
    check($data['room'] === $room, 'moderation cannot escape the active room');
    if ($method === 'ListParticipants') {
        // Real Twirp field name. List permissions can also be stale; use the live participant to read them.
        return ['participants' => array_map(static fn($id) => ['identity' => $id, 'permission' => ['can_publish' => false]], array_keys($permissions))];
    }
    $id = $data['identity'] ?? '';
    check($method === 'UpdateParticipant', 'supported RoomService call');
    if (!isset($permissions[$id])) throw new ClassroomError(409, 'not found');
    $fail = $broken[$id] ?? '';
    if (isset($data['permission'])) {
        $updates[] = $data;
        if ($fail !== 'ignored') $permissions[$id] = $data['permission'];
        if ($fail !== 'track') $tracks[$id] = array_values(array_intersect($tracks[$id] ?? [], $permissions[$id]['canPublishSources']));
    }
    $wire = [];
    foreach ($permissions[$id] as $key => $value) {
        // Exercise both proto JSON names and the optional camelCase JSON spelling.
        $name = $id === 'student-6' ? $key : strtolower(preg_replace('/[A-Z]/', '_$0', $key));
        $wire[$name] = $value;
    }
    if (isset($data['permission']) && $fail === 'missing-publish') unset($wire['can_publish'], $wire['canPublish']);
    $reply = ['identity' => $id, 'permission' => $wire,
        'tracks' => array_map(static fn($source) => ['source' => $source], $tracks[$id] ?? [])];
    if ($fail === 'missing-tracks') unset($reply['tracks']);
    return $reply;
};

cl_control_media($room, 'give-mic', 'student-1', $rpc);
$tracks['student-1'] = ['MICROPHONE']; // Published by the student, not by the grant.
cl_control_media($room, 'give-mic', 'student-6', $rpc);
$tracks['student-6'] = ['MICROPHONE'];
check(array_column($updates, 'identity') === ['student-1', 'student-6'], 'additive grants only mutate the chosen participant');
foreach (['student-1', 'student-6'] as $id) check($permissions[$id]['canPublishSources'] === ['MICROPHONE'], 'both students can publish audio only');
check(!$permissions['teacher-1']['canPublish'], 'other listeners unaffected');
blocked(fn()=>cl_control_media($room, 'give-mic', 'student-999', $rpc),409);
blocked(fn()=>cl_media_target('cut-mic', 'teacher-1'),400);
blocked(fn()=>cl_media_target('cut-video', ['student-1']),400);
blocked(fn()=>cl_media_target('give-mic', null),400);
blocked(fn()=>cl_media_target('cut-all-mics', 'student-1'),400);

// Extra sources exist only in this fixture, not in the audio-only pilot.
$permissions['student-1']['canPublishData'] = true;
$permissions['student-1']['canUpdateMetadata'] = true;
$permissions['student-1']['canManageAgentSession'] = true;
$permissions['student-1']['canPublishSources'] = [1, 2, 3, 4]; // Numeric proto enums are valid too.
$tracks['student-1'] = ['CAMERA', 'MICROPHONE', 'SCREEN_SHARE', 'SCREEN_SHARE_AUDIO'];
cl_control_media($room, 'cut-video', 'student-1', $rpc);
check($tracks['student-1'] === ['MICROPHONE', 'SCREEN_SHARE_AUDIO'], 'cut camera and screen video without touching audio');
cl_control_media($room, 'cut-mic', 'student-1', $rpc);
check($tracks['student-1'] === ['SCREEN_SHARE_AUDIO'] && $permissions['student-1']['canPublish'], 'cut mic preserves independent source');
check($permissions['student-1']['canSubscribe'] && $permissions['student-1']['canPublishData'] && $permissions['student-1']['canUpdateMetadata']
    && $permissions['student-1']['canManageAgentSession'], 'selective cuts preserve other permission flags and listening');
check($tracks['student-6'] === ['MICROPHONE'], 'other student still audible');

$permissions['teacher-1'] = array_replace($audioOnly, ['canPublishSources' => []]); // Empty list means unrestricted.
$tracks['teacher-1'] = ['MICROPHONE', 'CAMERA'];
$updates = [];
cl_control_media($room, 'cut-all-mics', null, $rpc);
check(array_column($updates, 'identity') === ['student-6', 'teacher-1'], 'bulk cut reaches both camel and real snake-case publishers');
check(!$permissions['student-6']['canPublish'] && $permissions['student-6']['canPublishSources'] === [] && $tracks['student-6'] === [], 'last source is revoked, never wildcard');
check($permissions['teacher-1']['canPublishSources'] === ['CAMERA', 'SCREEN_SHARE', 'SCREEN_SHARE_AUDIO'] && $tracks['teacher-1'] === ['CAMERA'], 'unrestricted publisher keeps only non-mic sources');
cl_control_media($room, 'cut-all-mics', null, $rpc);
check(count($updates) === 2, 'repeated cut is idempotent');

foreach (['ignored', 'missing-publish', 'track', 'missing-tracks'] as $failure) {
    $broken['student-1'] = $failure;
    $permissions['student-1'] = $audioOnly;
    $tracks['student-1'] = ['MICROPHONE'];
    $permissions['student-6'] = $audioOnly;
    $tracks['student-6'] = ['MICROPHONE'];
    blocked(fn()=>cl_control_media($room, 'cut-all-mics', null, $rpc),502);
    check(!$permissions['student-6']['canPublish'] && $tracks['student-6'] === [], 'bulk cut continues others but reports incomplete verification');
}
blocked(fn()=>cl_control_media($room, 'cut-all-mics', null, static fn() => ['participants' => null]),502);
blocked(fn()=>cl_media_permission(['identity'=>'student-6','permission'=>['canPublish'=>false,'can_publish'=>true]],'student-6'),502);
check(cl_service_grant('UpdateParticipant', $room) === ['roomAdmin'=>true,'room'=>$room], 'admin RPC cannot escape current room');
echo "PASS school roles, listener JWT, additive mics, selective/bulk SFU cuts, strict result verification\n";

// A removed/closed room name must not be reused, and cleanup must not target another install.
$state = null;
$history = [];
$rooms = [];
$ctx = [
    'read_runtime' => static function () use (&$state): ?string { return $state; },
    'write_runtime' => static function (?string $value) use (&$state): void { $state = $value; },
];
$mock = static function (string $method, string $room, array $data) use (&$rooms, &$history, &$state): array {
    $history[] = [$method, $room];
    if ($method === 'CreateRoom') {
        check($state === $room && $data['maxParticipants'] === 4, 'journal room before network send');
        $rooms[$room] = ['name' => $room];
    } elseif ($method === 'DeleteRoom') {
        if (!isset($rooms[$room])) throw new ClassroomError(409, 'not found');
        unset($rooms[$room]);
    } elseif ($method === 'ListRooms') {
        return ['rooms' => isset($rooms[$room]) ? [$rooms[$room]] : []];
    }
    return [];
};
blocked(fn()=>cl_managed_action($cfg, $ctx, 'join', null, $mock),409);
$first = cl_managed_action($cfg, $ctx, 'open', null, $mock);
check(cl_managed_action($cfg, $ctx, 'open', null, $mock) === $first, 'reuse only a still-active lesson');
check(cl_managed_action($cfg, $ctx, 'join', null, $mock) === $first, 'join current lesson only');
$rooms['another-install'] = ['name' => 'another-install'];
$fail = static fn() => throw new ClassroomError(502, 'VPS offline');
blocked(fn()=>cl_managed_action($cfg, $ctx, 'close', null, $fail),502);
check($state === $first, 'retain runtime for a cleanup retry');
cl_managed_action($cfg, $ctx, 'close', null, $mock);
check($state === null && !isset($rooms[$first]) && isset($rooms['another-install']), 'close only own room');
check(cl_managed_action($cfg, $ctx, 'open', null, $mock) !== $first, 'new lesson gets a new room, not stale listener tickets');
$state = 'another-install'; $calls = count($history);
blocked(fn()=>cl_managed_action($cfg, $ctx, 'close', null, $mock),503);
check(count($history) === $calls, 'bad runtime never sent to VPS');
echo "PASS managed room journal, exact cleanup, outage retry, fresh room after close\n";
