<?php
// Isolated QA only. Uses the shipping recording policy with a synthetic bell
// and SQLite store. Never deploy this router or point it at a real school.
declare(strict_types=1);
require __DIR__ . '/../../school-addon/upload/online-classroom/events.php';

const QA_JOB = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const QA_ROOM = 'test-aaaaaaaaaaaaaaaaaaaa-v4-c15-bbbbbbbbbbbbbbbbbbbbbbbb';
const QA_DOC = 'cccccccccccccccccccccccccccccccc';
const QA_GROUP = '-100321';
const QA_PREFIX = 'https://school.example.test/fixture-bot/';

function qa_need(bool $ready, string $why): void {
    if (!$ready) throw new RuntimeException($why);
}

$dir = getenv('BACI_QA_DIR');
qa_need(is_string($dir) && realpath($dir) === $dir, 'test directory required');
$pair = json_decode(file_get_contents($dir . '/pair.json'), true, 8, JSON_THROW_ON_ERROR);
qa_need(in_array($pair['signal_url'], ['wss://media.example.test', 'wss://classroom-new.example.com'], true)
    && $pair['site'] === 'https://school.example.test/campus', 'isolated pair required');
$cfg = $pair + ['api_url'=>'https://' . parse_url($pair['signal_url'], PHP_URL_HOST), 'record_lessons'=>true,
    'recording_delay_minutes'=>1, 'screen_share'=>true, 'room_prefix'=>'test-' . str_repeat('a',20)];
$teacher = ['role'=>'teacher', 'id'=>7, 'identity'=>'teacher-7', 'office'=>null];
$db = new PDO('sqlite:' . $dir . '/school.db', null, null, [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
$db->exec('PRAGMA busy_timeout=2000');
$table = 'mtg_plug_store_' . str_repeat('0', 32);
$s = new ClassroomStore($db, $table);
$GLOBALS['MTAG_PLUGIN_CONTEXT'] = ['plugin_dir'=>$dir,'recording_bot'=>static fn(string $name) => $name === 'telegram'
    ? ['revision'=>hash('sha256', QA_PREFIX), 'prefix'=>QA_PREFIX] : null];
// Stub only the provider's group metadata. The worker's multipart upload still
// traverses HTTPS to the fixture's provider endpoint and requires a real permit.
$provider = static fn(string $url, array $data) => match (basename($url)) {
    'getChat' => ['ok'=>true,'result'=>['id'=>(int)QA_GROUP,'type'=>'supergroup','title'=>'گروه آزمایشی']],
    'getMe' => ['ok'=>true,'result'=>['id'=>9]],
    'getChatMember' => ['ok'=>true,'result'=>['status'=>'member','user'=>['id'=>9]]],
    default => throw new RuntimeException('unexpected provider action'),
};
$rpc = static function (string $method, string $room, array $data) use ($cfg): array {
    qa_need($method === 'DeleteRoom' && $room === QA_ROOM, 'only fixture room may be closed');
    return cl_rpc($cfg, $room, $method, $data);
};
$now = time();

try {
    if (PHP_SAPI === 'cli') {
        $cmd = $argv[1] ?? '';
        if ($cmd === 'init') {
            $db->exec("CREATE TABLE `$table` (bucket TEXT,item TEXT,payload TEXT,PRIMARY KEY(bucket,item));
                CREATE TABLE academic_years (year_name TEXT,status INT,is_default INT);
                INSERT INTO academic_years VALUES ('1405/1406',1,1);
                CREATE TABLE settings (key_name TEXT PRIMARY KEY,key_value TEXT);
                INSERT INTO settings VALUES ('current_academic_year','1405/1406'), ('school_name','مدرسهٔ آزمایشی');
                CREATE TABLE classes (id INT PRIMARY KEY,name TEXT,academic_year TEXT);
                INSERT INTO classes VALUES (15,'تمرین ظرفیت','1405/1406');
                CREATE TABLE bot_chats (platform TEXT,chat_id TEXT,title TEXT,chat_type TEXT,status TEXT);
                INSERT INTO bot_chats VALUES ('telegram','-100321','آزمایش تحویل','supergroup','member')");
            cl_rec_set_link($s, $cfg, cl_classes($db, '1405/1406')[15], 'telegram:' . QA_GROUP, $provider);
        } elseif ($cmd === 'tickets') {
            // Use the same grants and five-second nbf allowance as the real site.
            $tickets = ['teacher-7'=>cl_join_ticket($cfg, QA_ROOM, $teacher)];
            for ($i = 1; $i <= 10; $i++) {
                $actor = ['identity'=>'student-' . $i,'name'=>'پژوهشگر آزمایشی ' . $i];
                $tickets[$actor['identity']] = cl_join_ticket($cfg, QA_ROOM, $actor);
            }
            echo json_encode($tickets, JSON_THROW_ON_ERROR), "\n";
        } elseif ($cmd === 'start') {
            $state = json_decode(file_get_contents($dir . '/live.json'), true, 4, JSON_THROW_ON_ERROR);
            $sid = $state['teacherSID'];
            qa_need(cl_sid($sid) && cl_sid($state['screenSID']) && str_starts_with($state['screenSID'], 'TR_'), 'actual SFU SIDs required');
            $class = cl_classes($db, '1405/1406')[15];
            $live = ['id'=>QA_JOB, 'class_id'=>15, 'class_name'=>$class['name'], 'year'=>$class['year'], 'teacher'=>7,
                'slot'=>1,'subject'=>'ریاضی','period'=>'زنگ ۱','date'=>cl_day($now)->format('Y-m-d'), 'phase'=>'live',
                'sample'=>$now,'room'=>QA_ROOM,'started'=>$now-10,'created'=>$now-11,'deadline'=>$now+120,
                'staff'=>['teacher-7'=>$sid],'active_seconds'=>10,'pulses'=>['teacher-7'=>['sid'=>$sid,'at'=>$now]],
                'seen'=>['teacher-7'=>$sid], 'screen'=>['identity'=>'teacher-7','sid'=>$sid,'until'=>$now+120]];
            $s->put('lesson', QA_JOB, $live);
            $s->put('active', '15', ['id'=>QA_JOB]);
            qa_need(cl_rec_start($s, $cfg, $class, $live, $teacher, $now)['recording']['notice'] === 'preparing', 'start did not show recording notice');
            $file = $dir . '/board.pdf';
            qa_need(is_file($file) && copy($file, cl_lesson_dir(QA_JOB, true) . '/' . QA_DOC), 'synthetic board PDF required');
            $board = cl_activity($s, QA_JOB);
            $board['doc'] = QA_DOC;
            $board['docs'][QA_DOC] = ['id'=>QA_DOC,'mime'=>'application/pdf','bytes'=>filesize($file),'name'=>'Test.pdf','pages'=>1];
            $s->put('activity', QA_JOB, $board);
            echo "QA_RECORD teacher started a normal bell recording; five-second notice honored by site policy\n";
        } elseif ($cmd === 'layout') {
            $live = cl_active($s, 15);
            $state = json_decode(file_get_contents($dir . '/live.json'), true, 4, JSON_THROW_ON_ERROR);
            $scoped = ['lesson'=>QA_JOB,'epoch'=>cl_epoch($live),'sid'=>$state['teacherSID'],'enabled'=>true];
            cl_rec_set_source($s, $cfg, $live, $teacher, $scoped + ['source'=>'board'], $now);
        } elseif ($cmd === 'close') {
            $live = cl_active($s, 15);
            qa_need(cl_rec_close_choice($s, $cfg, $live, $teacher, ['recording'=>QA_JOB,'sendRecording'=>true], $now) === 'queued', 'teacher consent did not queue');
            $live['recording_consent'] = true;
            cl_close_lesson($s, $cfg, $live, $now, $rpc);
            $job = $s->get('recording', QA_JOB);
            qa_need($job['state'] === 'queued' && $job['not_before'] === $now+60 && $job['total'] === 0, 'must defer after verified closure');
            echo "QA_RECORD confirmed closure, approving delivery only after the school's delay\n";
        } elseif ($cmd === 'status') {
            $job = $s->get('recording', QA_JOB);
            echo json_encode(['state'=>$job['state'] ?? 'idle', 'reason'=>$job['reason'] ?? '', 'live'=>$job['live'] ?? false,
                'layout'=>$job['layout_capable'] ?? false, 'sent'=>$job['sent'] ?? 0,
                'delivered'=>(($job['state'] ?? '') === 'delivered' && ($job['last']['message'] ?? '') === '321')], JSON_THROW_ON_ERROR), "\n";
        } else throw new RuntimeException('unknown QA command');
        exit;
    }

    if ($_SERVER['REQUEST_METHOD'] !== 'POST' || $_SERVER['REQUEST_URI'] !== '/campus/plugins/school-plugins.php?plugin=school-classroom&route=recording') {
        http_response_code(404); exit;
    }
    cl_rec_worker_auth($cfg, $_SERVER['HTTP_AUTHORIZATION'] ?? null);
    $input = json_decode(file_get_contents('php://input', false, null, 0, 32768), true, 20, JSON_THROW_ON_ERROR);
    // Keep verified synthetic presence current while the separately monitored
    // LiveKit clients run. Production attendance/timetable APIs are not mocked on the VPS.
    if (($lesson = cl_active($s, 15)) && $lesson['phase'] === 'live') {
        $lesson['sample'] = $now;
        $lesson['pulses']['teacher-7']['at'] = $now;
        $s->put('lesson', QA_JOB, $lesson);
    }
    $result = cl_rec_worker($s, $cfg, $input, $now, $rpc, $provider);
    if (isset($result['file'])) {
        cl_reply_board_file(...$result['file']);
    } else {
        header('Content-Type: application/json');
        echo json_encode(['ok'=>true] + $result, JSON_THROW_ON_ERROR);
    }
} catch (Throwable $error) {
    if (PHP_SAPI === 'cli') {
        fwrite(STDERR, 'QA school command failed: ' . get_class($error) . ': ' . $error->getMessage() . "\n");
        exit(1);
    }
    http_response_code($error instanceof ClassroomError ? $error->status : 500);
    error_log('QA school: ' . get_class($error) . ' action=' . ($input['action'] ?? 'auth'));
}
