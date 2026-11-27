<?php
declare(strict_types=1);

// A disposable school and synthetic SFU presence. The HTTP entrypoints, uploads,
// storage and admission checks run unchanged from the archive under test.
require_once '/classroom/activity.php';
$table = 'mtg_plug_store_' . str_repeat('1', 32);
$db = new PDO('sqlite:/tmp/board-' . ($demo ? 'practice' : 'bell') . '.sqlite');
$ctx = ['pdo'=>$db,'store'=>$table,'csrf'=>'fixture-token','plugin_dir'=>'/classroom',
    'write_runtime'=>static fn(string $runtime) => null,
    'recording_bot'=>static fn(string $platform) => $platform === 'bale'
        ? ['revision'=>str_repeat('b',64),'prefix'=>'https://bale.example.test/botQA/'] : null,
    'config'=>['enabled'=>true,'site_origin'=>'https://school.example.test','signal_url'=>'wss://media.example.test',
        'api_key'=>str_repeat('k',16),'api_secret'=>str_repeat('s',48),'room_prefix'=>'test-' . str_repeat('a',20),
        'record_lessons'=>true,'recording_secret'=>str_repeat('r',48),
        'screen_share'=>true,'student_screen_share'=>true,
        'friday_test'=>['date'=>'2026-11-20','id'=>str_repeat('e',32),'students'=>true]]];
$GLOBALS['MTAG_PLUGIN_CONTEXT'] = $ctx;
if (!isset($seed)) return;
$s = new ClassroomStore($db, $table);
$cfg = cl_config();
$now = time();
if ($seed === 'open') {
    $db->exec("CREATE TABLE $table (bucket TEXT,item TEXT,payload TEXT,PRIMARY KEY(bucket,item));
        CREATE TABLE academic_years (year_name TEXT,status INT,is_default INT);
        INSERT INTO academic_years VALUES ('1405/1406',1,1);
        CREATE TABLE settings (key_name TEXT,key_value TEXT);
        CREATE TABLE classes (id INT,name TEXT,academic_year TEXT);
        INSERT INTO classes VALUES (22,'7/1','1405/1406');
        CREATE TABLE students (id INT,first_name TEXT,last_name TEXT,class_name TEXT,academic_year TEXT,status TEXT);
        INSERT INTO students VALUES (1,'Fixture','Pupil','7/1','1405/1406','active');
        CREATE TABLE admins (id INT,name TEXT,role TEXT,permissions TEXT,status INT);
        INSERT INTO admins VALUES (1,'Fixture Manager','admin','[\"manage_classes\"]',1),(2,'Unauthorized','admin','[]',1);
        CREATE TABLE teachers (id INT,full_name TEXT,status INT,is_deputy INT);
        INSERT INTO teachers VALUES (1,'Fixture Teacher',1,0),(2,'Fixture Deputy',1,1);
        CREATE TABLE bot_chats (platform TEXT,chat_id TEXT,title TEXT,chat_type TEXT,status TEXT);
        INSERT INTO bot_chats VALUES ('bale','-100','Fixture group','group','member');
        CREATE TABLE class_schedules (id INT,teacher_id INT,class_name TEXT,academic_year TEXT,day_of_week TEXT,period_num TEXT,subject_name TEXT);
        INSERT INTO class_schedules VALUES (11,1,'7/1','1405/1406','چهارشنبه','زنگ 1','ریاضی');");
    $class = cl_classes($db, cl_current_year($db))[22];
    $manager = cl_school_user($db, ['admin_id'=>1], $class['year']);
    $lesson = cl_open_lesson($s,$ctx,$cfg,$class,$manager,['slot'=>$demo ? CL_FRIDAY_SLOT : 11],$now,static fn()=>[]);
    $lesson['roster'] += cl_roster($db,$class,[1]);
} else $lesson = cl_active($s,22);
$epoch = cl_epoch($lesson);
$peers = ['teacher-2'=>'PA_Deputy','admin-1'=>'PA_Manager','student-1'=>'PA_Pupil'];
if (in_array($seed, ['teacher','record'], true)) $peers['teacher-1'] = 'PA_Teacher';
foreach ($peers as $identity=>$sid) {
    $lesson = cl_touch($s,$lesson,['identity'=>$identity],['lesson'=>$lesson['id'],'epoch'=>$epoch,'sid'=>$sid],$now);
}
$lesson = cl_observe($lesson,$peers,array_diff_key($peers,['student-1'=>true]),$now,$now);
$lesson['event_received'] = true;
$lesson['last_attempt'] = $now; // Recent SFU sample: status doesn't need an external RPC.
if ($seed === 'record-ready') {
    cl_rec_set_link($s,$cfg,cl_classes($db,cl_current_year($db))[22],'bale:-100',
        static fn()=>['ok'=>true,'result'=>['id'=>-100,'type'=>'group','title'=>'Fixture group']]);
}
if ($seed === 'record') {
    // A narrowly scoped active recorder for testing the authenticated rendering path, without a live worker.
    $id = $lesson['id'];
    $lesson['recorder'] = 'recorder-' . $id;
    $lesson['camera_codec'] = 'vp8';
    $lesson['screen'] = ['identity'=>'student-1','sid'=>'PA_Pupil','until'=>$now+60];
    $s->put('recording',$id,['id'=>$id,'class'=>22,'worker'=>str_repeat('9',32),'state'=>'recording',
        'protocol'=>CL_REC_PROTOCOL,'layout_capable'=>true,'live'=>true,'begin'=>$now-6,'deadline'=>$lesson['deadline'],'target'=>['title'=>'test','provider'=>'bale']]);
}
$s->put('lesson',$lesson['id'],$lesson);
echo json_encode(['lesson'=>$lesson['id'],'epoch'=>$epoch,'teacher'=>$lesson['teacher']], JSON_THROW_ON_ERROR);
