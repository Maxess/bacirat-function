<?php
// Isolated QA only. Uses the shipping recording policy with a synthetic bell
// and SQLite store. Never deploy this router or point it at a real school.
declare(strict_types=1);
require __DIR__ . '/../../school-addon/upload/online-classroom/events.php';

const QA_BELLS = [
    'approve'=>'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    'refuse'=>'dddddddddddddddddddddddddddddddd',
    'revoke'=>'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
];
const QA_ROOM_PREFIX = 'test-aaaaaaaaaaaaaaaaaaaa-v4-c15-';
const QA_DOC = 'cccccccccccccccccccccccccccccccc';
function qa_need(bool $ready, string $why): void {
    if (!$ready) throw new RuntimeException($why);
}

$dir = getenv('BACI_QA_DIR');
qa_need(is_string($dir) && realpath($dir) === $dir, 'test directory required');
$pair = json_decode(file_get_contents($dir . '/pair.json'), true, 8, JSON_THROW_ON_ERROR);
qa_need(in_array($pair['signal_url'], ['wss://media.example.test', 'wss://classroom-new.example.com'], true)
    && $pair['site'] === 'https://school.example.test/campus', 'isolated pair required');
$cfg = $pair + ['api_url'=>'https://' . parse_url($pair['signal_url'], PHP_URL_HOST), 'record_lessons'=>true,
    'recording_delay_minutes'=>1, 'screen_share'=>true, 'camera_share'=>true, 'room_prefix'=>'test-' . str_repeat('a',20)];
$teacher = ['role'=>'teacher', 'id'=>7, 'identity'=>'teacher-7', 'office'=>null];
$db = new PDO('sqlite:' . $dir . '/school.db', null, null, [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
$db->exec('PRAGMA busy_timeout=2000');
$table = 'mtg_plug_store_' . str_repeat('0', 32);
$s = new ClassroomStore($db, $table);
$platform = getenv('BACI_QA_PROVIDER') ?: 'telegram';
[$prefix, $group] = match ($platform) {
    'bale' => ['https://tapi.bale.ai/botQA-fixture/', '-100321'],
    'rubika' => ['https://botapi.rubika.ir/v3/QA-fixture/', 'g0fixture321'],
    'telegram' => ['https://school.example.test/fixture-bot/', '-100321'],
    default => throw new RuntimeException('unknown QA provider'),
};
$GLOBALS['MTAG_PLUGIN_CONTEXT'] = ['plugin_dir'=>$dir,'recording_bot'=>static fn(string $name) => $name === $platform
    ? ['revision'=>hash('sha256', $prefix), 'prefix'=>$prefix] : null];
// Stub only group metadata. The installed worker still needs a real school
// permit and performs the provider-specific upload over isolated HTTPS.
$provider = static function (string $url, array $data) use ($platform, $prefix, $group): array {
    qa_need(str_starts_with($url, $prefix), 'unexpected provider URL');
    if (basename($url) === 'getChat') {
        qa_need($data === ['chat_id'=>$group], 'wrong group metadata request');
        return $platform === 'rubika'
            ? ['status'=>'OK','data'=>['chat'=>['chat_id'=>$group,'chat_type'=>'Group','title'=>'گروه آزمایشی']]]
            : ['ok'=>true,'result'=>['id'=>(int)$group,'type'=>'supergroup','title'=>'گروه آزمایشی']];
    }
    qa_need($platform === 'telegram', 'unexpected provider action');
    return match (basename($url)) {
        'getMe' => ['ok'=>true,'result'=>['id'=>9]],
        'getChatMember' => ['ok'=>true,'result'=>['status'=>'member','user'=>['id'=>9]]],
        default => throw new RuntimeException('unexpected provider action'),
    };
};
$rooms = array_map(static fn(string $id): string => QA_ROOM_PREFIX . substr($id, -24), QA_BELLS);
$rpc = static function (string $method, string $room, array $data) use ($cfg, $rooms): array {
    qa_need(in_array($method, ['DeleteRoom','ListParticipants'], true) && in_array($room, $rooms, true), 'fixture rooms only');
    return cl_rpc($cfg, $room, $method, $data);
};
$now = time();

try {
    if (PHP_SAPI === 'cli') {
        $cmd = $argv[1] ?? '';
        $case = $argv[2] ?? 'approve';
        $id = QA_BELLS[$case] ?? throw new RuntimeException('unknown fixture bell');
        $room = $rooms[$case];
        if ($cmd === 'init') {
            $db->exec("CREATE TABLE `$table` (bucket TEXT,item TEXT,payload TEXT,PRIMARY KEY(bucket,item));
                CREATE TABLE academic_years (year_name TEXT,status INT,is_default INT);
                INSERT INTO academic_years VALUES ('1405/1406',1,1);
                CREATE TABLE settings (key_name TEXT PRIMARY KEY,key_value TEXT);
                INSERT INTO settings VALUES ('current_academic_year','1405/1406'), ('school_name','مدرسهٔ آزمایشی');
                CREATE TABLE classes (id INT PRIMARY KEY,name TEXT,academic_year TEXT);
                INSERT INTO classes VALUES (15,'تمرین ظرفیت','1405/1406');
                CREATE TABLE bot_chats (platform TEXT,chat_id TEXT,title TEXT,chat_type TEXT,status TEXT)");
            $db->prepare("INSERT INTO bot_chats VALUES (?,?,'آزمایش تحویل','supergroup','member')")->execute([$platform,$group]);
            cl_rec_set_link($s, $cfg, cl_classes($db, '1405/1406')[15], $platform . ':' . $group, $provider);
        } elseif ($cmd === 'tickets') {
            // Use the same grants and five-second nbf allowance as the real site.
            $tickets = ['teacher-7'=>cl_join_ticket($cfg, $room, $teacher)];
            for ($i = 1; $i <= 10; $i++) {
                $actor = ['identity'=>'student-' . $i,'name'=>'پژوهشگر آزمایشی ' . $i];
                $tickets[$actor['identity']] = cl_join_ticket($cfg, $room, $actor);
            }
            echo json_encode(['room'=>$room, 'tickets'=>$tickets], JSON_THROW_ON_ERROR), "\n";
        } elseif ($cmd === 'start') {
            $state = json_decode(file_get_contents($dir . '/live.json'), true, 4, JSON_THROW_ON_ERROR);
            qa_need(cl_active($s, 15) === null, 'only one live bell at a time');
            $sid = $state['teacherSID'];
            qa_need(cl_sid($sid) && cl_sid($state['screenSID']) && str_starts_with($state['screenSID'], 'TR_'), 'actual SFU SIDs required');
            $class = cl_classes($db, '1405/1406')[15];
            $live = ['id'=>$id, 'class_id'=>15, 'class_name'=>$class['name'], 'year'=>$class['year'], 'teacher'=>7,
                'slot'=>1,'subject'=>'ریاضی','period'=>'زنگ ۱','date'=>cl_day($now)->format('Y-m-d'), 'phase'=>'live',
                'sample'=>$now,'room'=>$room,'started'=>$now-10,'created'=>$now-11,'deadline'=>$now+120,
                'staff'=>['teacher-7'=>$sid],'active_seconds'=>10,'pulses'=>['teacher-7'=>['sid'=>$sid,'at'=>$now]],
                'camera_codec'=>'vp8', 'access'=>['camera'=>['members'=>['teacher-7'=>true]]],
                'seen'=>['teacher-7'=>$sid], 'screen'=>['identity'=>'teacher-7','sid'=>$sid,'until'=>$now+120]];
            $s->put('lesson', $id, $live);
            $s->put('active', '15', ['id'=>$id]);
            qa_need(cl_rec_start($s, $cfg, $class, $live, $teacher, $now)['recording']['notice'] === 'preparing', 'start did not show recording notice');
            $file = $dir . '/board.pdf';
            qa_need(is_file($file) && copy($file, cl_lesson_dir($id, true) . '/' . QA_DOC), 'synthetic board PDF required');
            $board = cl_activity($s, $id);
            $board['doc'] = QA_DOC;
            $board['docs'][QA_DOC] = ['id'=>QA_DOC,'mime'=>'application/pdf','bytes'=>filesize($file),'name'=>'Test.pdf','pages'=>1];
            $s->put('activity', $id, $board);
            echo "QA_RECORD teacher started a normal bell recording; five-second notice honored by site policy\n";
        } elseif ($cmd === 'layout') {
            $live = cl_active($s, 15);
            $state = json_decode(file_get_contents($dir . '/live.json'), true, 4, JSON_THROW_ON_ERROR);
            $scoped = ['lesson'=>$id,'epoch'=>cl_epoch($live),'sid'=>$state['teacherSID'],'enabled'=>true];
            cl_rec_set_source($s, $cfg, $live, $teacher, $scoped + ['source'=>'board'], $now);
        } elseif (in_array($cmd, ['focus-camera','clear-camera'], true)) {
            cl_class_lock($s, $cfg, 15, static function () use ($s,$cfg,$teacher,$id,$now,$dir,$rpc,$cmd): void {
                $live = cl_active($s, 15);
                $state = json_decode(file_get_contents($dir . '/live.json'), true, 4, JSON_THROW_ON_ERROR);
                $scoped = ['lesson'=>$id,'epoch'=>cl_epoch($live),'sid'=>$state['teacherSID'],'publisherSid'=>$state['teacherSID']];
                $on = $cmd === 'focus-camera';
                if ($on) cl_rec_set_source($s, $cfg, $live, $teacher, $scoped + ['source'=>'screen','enabled'=>true,'track'=>$state['screenSID']], $now);
                $result = cl_rec_set_source($s, $cfg, $live, $teacher, $scoped + ['source'=>'camera','enabled'=>$on,
                    'identity'=>'teacher-7','track'=>$state['cameraSID']], $now, $rpc)['archive'];
                qa_need(($result['camera']['track'] ?? null) === ($on ? $state['cameraSID'] : null)
                    && $result['screen'] === $state['screenSID'] && $result['board'], 'certified camera/board/screen layout required');
            });
        } elseif ($cmd === 'close') {
            $live = cl_active($s, 15);
            qa_need($live && $live['id'] === $id, 'unexpected active bell');
            $choice = $case !== 'refuse';
            $decision = cl_rec_close_choice($s, $cfg, $live, $teacher, ['recording'=>$id,'sendRecording'=>$choice], $now);
            qa_need($decision === ($choice ? 'queued' : 'discarded'), 'teacher choice was not honored');
            $live['recording_consent'] = $choice;
            cl_close_lesson($s, $cfg, $live, $now, $rpc);
            $job = $s->get('recording', $id);
            qa_need($job['state'] === $decision && ($choice ? $job['not_before'] === $now+60 : !isset($job['not_before']))
                && $job['total'] === 0, 'must defer or discard after verified closure');
            if ($case === 'revoke') {
                cl_rec_set_link($s, $cfg, cl_classes($db, '1405/1406')[15], '', $provider);
                echo "QA_RECORD destination revoked after closure but before eligibility\n";
            } elseif ($choice) echo "QA_RECORD confirmed closure, approving delivery only after the school's delay\n";
            else echo "QA_RECORD confirmed closure with explicit refusal\n";
        } elseif ($cmd === 'id') {
            echo $id, "\n";
        } elseif ($cmd === 'status') {
            $job = $s->get('recording', $id);
            echo json_encode(['state'=>$job['state'] ?? 'idle', 'reason'=>$job['reason'] ?? '', 'live'=>$job['live'] ?? false,
                'layout'=>$job['layout_capable'] ?? false, 'sent'=>$job['sent'] ?? 0, 'total'=>$job['total'] ?? 0,
                'not_before'=>$job['not_before'] ?? null, 'rendered_at'=>$job['rendered_at'] ?? null,
                'cleaned'=>isset($job['cleaned']),
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
        $s->put('lesson', $lesson['id'], $lesson);
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
