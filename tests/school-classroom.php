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

$updates=[];
$rpc=static function(string $method, array $data) use (&$updates): array {
    if ($method==='ListParticipants') return ['participants'=>[
        ['identity'=>'teacher-1','permission'=>['canPublish'=>true]], ['identity'=>'student-1','permission'=>['canSubscribe'=>true]],
    ]];
    $updates[]=$data;
    return [];
};
cl_transfer_floor($class['room'],'student-1',$rpc);
check(count($updates)===2 && $updates[0]['identity']==='teacher-1' && !$updates[0]['permission']['canPublish'], 'withdraw old mic before granting');
check($updates[1]['identity']==='student-1' && $updates[1]['permission']['canPublishSources']===['MICROPHONE'], 'new permission microphone only');
blocked(fn()=>cl_transfer_floor($class['room'],'student-999',$rpc),409);
$updates=[];
cl_transfer_floor($class['room'],null,$rpc);
check(count($updates)===1 && !$updates[0]['permission']['canPublish'], 'cut floor');
check(cl_service_grant('UpdateParticipant', $class['room']) === ['roomAdmin'=>true,'room'=>$class['room']], 'admin RPC cannot escape current room');
echo "PASS school membership, no invalid/ambiguous roles, JWT grants, microphone handoff\n";

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
