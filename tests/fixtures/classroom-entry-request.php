<?php
declare(strict_types=1);

// Run the installed page/content PHP against an in-memory school and private files.
$plugin = getenv('MPM_TEST_CLASSROOM_ROOT');
require $plugin . '/activity.php';
$manifest = json_decode(file_get_contents($plugin . '/plugin.json'), true);
$request = json_decode(getenv('MPM_TEST_ENTRY_REQUEST') ?: '{}', true, 12, JSON_THROW_ON_ERROR);
$db = new PDO('sqlite::memory:', null, null, [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
$table = 'mtg_plug_store_' . str_repeat('a',32);
$db->exec(<<<SQL
CREATE TABLE $table (bucket TEXT, item TEXT, payload TEXT, PRIMARY KEY(bucket,item));
CREATE TABLE students (id INT, first_name TEXT, last_name TEXT, class_name TEXT, academic_year TEXT, status TEXT);
INSERT INTO students VALUES (1,'دانش‌آموز','آزمایشی','هفتم۱','1405/1406','active'), (2,'دیگر','','هفتم۲','1405/1406','active');
CREATE TABLE classes (id INT, name TEXT, academic_year TEXT);
INSERT INTO classes VALUES (22,'هفتم۱','1405/1406'), (23,'هفتم۲','1405/1406');
CREATE TABLE academic_years (year_name TEXT, status INT, is_default INT);
INSERT INTO academic_years VALUES ('1405/1406',1,1);
CREATE TABLE settings (key_name TEXT, key_value TEXT);
INSERT INTO settings VALUES ('current_academic_year','1405/1406'), ('school_name','مدرسهٔ آزمایشی');
SQL);
$cfg = ['enabled'=>true,'site_origin'=>'https://school.example.test','signal_url'=>'wss://media.example.test',
    'room_prefix'=>'test-' . str_repeat('a',20),'api_key'=>str_repeat('b',24),'api_secret'=>str_repeat('c',64)];
$dir = sys_get_temp_dir() . '/classroom-entry-' . bin2hex(random_bytes(8));
mkdir($dir, 0700);
$GLOBALS['MTAG_PLUGIN_CONTEXT'] = ['base'=>'/plugins/school-plugins.php?plugin=school-classroom', 'site_base'=>'/', 'csrf'=>'fixture',
    'pdo'=>$db, 'store'=>$table, 'config'=>$cfg,'plugin_dir'=>realpath($dir), 'asset_hashes'=>$manifest['files'],
    'pdf_hash'=>$manifest['files'][$manifest['pdf_resources']]];
$_SERVER['HTTPS'] = 'on';
$_SERVER['HTTP_HOST'] = 'school.example.test';
$_SERVER['REQUEST_METHOD'] = $request['method'] ?? 'GET';
if (isset($request['range'])) $_SERVER['HTTP_RANGE'] = $request['range'];
$actor = $request['actor'] ?? 1;
$_SESSION = $actor ? ['student_id'=>$actor] : [];
$_GET = ($request['query'] ?? []) + ['class'=>'22'];
$now = time();
$id = str_repeat('a',32); $doc = str_repeat('b',32); $old = str_repeat('c',32);
$s = cl_store();
$phase = $request['phase'] ?? 'live';
$lesson = ['id'=>$id,'class_id'=>22,'class_name'=>'هفتم۱','year'=>'1405/1406', 'teacher'=>1,'room'=>$cfg['room_prefix'] . '-22-' . str_repeat('e',24),
    'subject'=>'ریاضی', 'date'=>cl_day($now)->format('Y-m-d'), 'deadline'=>$now+120, 'sample'=>$phase === 'stale' ? $now-14 : $now,
    'phase'=>$phase === 'stale' ? 'live' : $phase, 'staff'=>['teacher-1'=>'PA_Teacher'], 'seen'=>['teacher-1'=>'PA_Teacher'],
    'pulses'=>['teacher-1'=>['sid'=>'PA_Teacher','at'=>$now]],'first_seen'=>[], 'seconds'=>[]];
$s->put('active','22',['id'=>$id]);
$s->put('lesson',$id,$lesson);
$bytes = "%PDF-1.4\nSynthetic lesson attachment\n";
$file = cl_lesson_dir($id,true) . '/' . $doc;
file_put_contents($file,$bytes);
$oldFile = dirname($file) . '/' . $old;
file_put_contents($oldFile,$bytes);
$row = ['id'=>$doc,'name'=>'worksheet.pdf','mime'=>'application/pdf','pages'=>2,'bytes'=>strlen($bytes)];
$activity = cl_activity($s,$id);
$activity['doc'] = $doc; $activity['docs'] = [$doc=>$row, $old=>array_replace($row,['id'=>$old])]; $activity['page'] = 2;
$s->put('activity',$id,$activity);
for ($i=1; $i<=19; $i++) cl_chat($s,$lesson,['role'=>'student','identity'=>'student-1','name'=>'همکلاسی'] ,
    $i===19 ? '<b>پیام تازهٔ آزمون</b>' : "پیام آزمون $i",$now-80+$i*3);
$stroke = ['tool'=>'pen','color'=>'#0055aa','size'=>4,'points'=>[[100,200],[5000,4500]]];
cl_board_action($s,$lesson,['board'=>1,'strokes'=>[$stroke,$stroke+['id'=>str_repeat('f',32)],
    ['tool'=>'erase-stroke','targets'=>[str_repeat('f',32)]]]],'board-ink');
$before = $db->query("SELECT * FROM $table ORDER BY bucket,item")->fetchAll(PDO::FETCH_ASSOC);
ob_start();
(static function (string $target): void { require $target; })($plugin . (($_GET['route'] ?? '') === 'content' ? '/content.php' : '/index.php'));
$body = ob_get_clean();
$status = http_response_code() ?: 200;
if ($before !== $db->query("SELECT * FROM $table ORDER BY bucket,item")->fetchAll(PDO::FETCH_ASSOC)) throw new RuntimeException('A read changed store state');
unlink($file); unlink($oldFile); rmdir(dirname($file)); rmdir($dir . '/documents'); rmdir($dir);
echo json_encode(['status'=>$status,'body'=>$body], JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
