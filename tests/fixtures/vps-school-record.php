<?php
// Disposable VM QA only. Load the packaged school code from our isolated copy.
declare(strict_types=1);
const QA_ROOMS = 9;
const QA_PUPILS = 20;
const QA_DOC = 'cccccccccccccccccccccccccccccccc';
const QA_PREFIX = 'test-aaaaaaaaaaaaaaaaaaaa';
const QA_BOT = 'https://school.example.test/fixture-bot/';
const QA_CHAT = '-100321';

function need(bool $value, string $what): void
{
    if (!$value) throw new RuntimeException($what);
}
function teacher(int $c): array
{
    return ['role'=>'teacher','id'=>700 + $c,'identity'=>'teacher-' . (700 + $c),'office'=>null];
}
function bell(int $c): string { return sprintf('%032x', $c); }
function room(int $c): string { return QA_PREFIX . '-v4-c' . $c . '-' . substr(bell($c), -24); }
function reply(mixed $data): string { return json_encode($data, JSON_THROW_ON_ERROR) . "\n"; }

$dir = getenv('BACI_QA_DIR');
need(is_string($dir) && realpath($dir) === $dir, 'private fixture directory required');
$pair = json_decode(file_get_contents($dir . '/pair.json'), true, 8, JSON_THROW_ON_ERROR);
need($pair['signal_url'] === 'wss://media.example.test' && $pair['site'] === 'https://school.example.test/campus', 'isolated pair required');
require $dir . '/site/events.php';
$cfg = $pair + ['api_url'=>'https://media.example.test','record_lessons'=>true,'recording_delay_minutes'=>1,
    'screen_share'=>true,'camera_share'=>true,'max_participants'=>24,'room_prefix'=>QA_PREFIX];
$db = new PDO('sqlite:' . $dir . '/school.db', null, null, [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
$db->exec('PRAGMA busy_timeout=2000');
$table = 'mtg_plug_store_' . str_repeat('0', 32);
$s = new ClassroomStore($db, $table);
$GLOBALS['MTAG_PLUGIN_CONTEXT'] = ['plugin_dir'=>$dir,'recording_bot'=>static fn(string $name) => $name === 'telegram'
    ? ['revision'=>hash('sha256', QA_BOT), 'prefix'=>QA_BOT] : null];
$provider = static function (string $url, array $data): array {
    need(str_starts_with($url, QA_BOT), 'fixture bot only');
    return match (basename($url)) {
        'getChat' => ['ok'=>true,'result'=>['id'=>(int)QA_CHAT,'type'=>'supergroup','title'=>'گروه آزمایشی']],
        'getMe' => ['ok'=>true,'result'=>['id'=>9]],
        'getChatMember' => ['ok'=>true,'result'=>['status'=>'member','user'=>['id'=>9]]],
        default => throw new RuntimeException('unexpected provider request'),
    };
};
$classes = range(1, QA_ROOMS);
$rooms = array_map(room(...), $classes);
$rpc = static function (string $method, string $room, array $data) use ($cfg, $rooms): array {
    need(in_array($room, $rooms, true) && in_array($method, ['ListParticipants','DeleteRoom'], true), 'fixture rooms only');
    return cl_rpc($cfg, $room, $method, $data);
};
$now = time();

try {
    if (PHP_SAPI === 'cli') {
        switch ($argv[1] ?? '') {
            case 'init':
                $db->exec("PRAGMA journal_mode=WAL;
                    CREATE TABLE `$table` (bucket TEXT,item TEXT,payload TEXT,PRIMARY KEY(bucket,item));
                    CREATE TABLE academic_years (year_name TEXT,status INT,is_default INT);
                    INSERT INTO academic_years VALUES ('1405/1406',1,1);
                    CREATE TABLE settings (key_name TEXT PRIMARY KEY,key_value TEXT);
                    INSERT INTO settings VALUES ('current_academic_year','1405/1406'), ('school_name','مدرسهٔ آزمایشی');
                    CREATE TABLE classes (id INT PRIMARY KEY,name TEXT,academic_year TEXT);
                    CREATE TABLE bot_chats (platform TEXT,chat_id TEXT,title TEXT,chat_type TEXT,status TEXT)");
                $db->prepare("INSERT INTO bot_chats VALUES ('telegram',?,'آزمایش مقیاس','supergroup','member')")->execute([QA_CHAT]);
                foreach ($classes as $c) {
                    $db->prepare("INSERT INTO classes VALUES (?,?,'1405/1406')")->execute([$c,sprintf('QA-C%02d', $c)]);
                    cl_rec_set_link($s, $cfg, cl_classes($db, '1405/1406')[$c], 'telegram:' . QA_CHAT, $provider);
                }
                break;
            case 'tickets':
                $plans = [];
                foreach ($classes as $c) {
                    $actors = [teacher($c)];
                    for ($i = 1; $i <= QA_PUPILS; $i++) {
                        $actors[] = ['identity'=>'student-' . (($c - 1)*QA_PUPILS + $i),'name'=>'دانش‌آموز آزمایشی'];
                    }
                    $plans[] = ['room'=>room($c),'tickets'=>array_map(static fn(array $actor): array =>
                        ['identity'=>$actor['identity'],'token'=>cl_join_ticket($cfg,room($c),$actor,240)], $actors)];
                }
                echo reply($plans);
                break;
            case 'open':
                $roster = cl_classes($db, '1405/1406');
                $trackTotals = [];
                foreach ($classes as $c) {
                    $teacher = teacher($c);
                    $id = bell($c);
                    $peers = cl_live_media_peers($rpc('ListParticipants', room($c), ['room'=>room($c)]));
                    need(count($peers) === QA_PUPILS + 1 && isset($peers[$teacher['identity']]), 'actual 21 active peers per room required');
                    $seen = [];
                    $kinds = [];
                    foreach ($peers as $identity=>$peer) {
                        $seen[$identity] = $peer['sid'];
                        foreach (($peer['tracks'] ?? []) as $track) {
                            need(($track['muted'] ?? false) === false, 'unexpected muted QA track');
                            $kinds[] = cl_track_source($track['source'] ?? null);
                        }
                    }
                    sort($kinds);
                    $expected = match ($c) {
                        8 => ['CAMERA','MICROPHONE','MICROPHONE'],
                        9 => ['MICROPHONE','MICROPHONE','SCREEN_SHARE'],
                        default => ['MICROPHONE','MICROPHONE','MICROPHONE'],
                    };
                    need($kinds === $expected, 'actual source mix must match the scenario');
                    $trackTotals[] = $kinds;
                    $sid = $seen[$teacher['identity']];
                    $live = ['id'=>$id,'class_id'=>$c,'class_name'=>$roster[$c]['name'],'year'=>$roster[$c]['year'],
                        'teacher'=>$teacher['id'],'slot'=>1,'subject'=>'آزمایش ظرفیت','period'=>'زنگ ۱','date'=>cl_day($now)->format('Y-m-d'),
                        'phase'=>'live','sample'=>$now,'room'=>room($c),'started'=>$now-10,'created'=>$now-11,'deadline'=>$now+360,
                        'staff'=>[$teacher['identity']=>$sid],'active_seconds'=>10,
                        'pulses'=>[$teacher['identity']=>['sid'=>$sid,'at'=>$now]], 'seen'=>$seen, 'camera_codec'=>'vp8',
                        'media_mode'=>$c <= 7 ? 'audio' : 'standard','access'=>['camera'=>['members'=>[$teacher['identity']=>true]]]];
                    if ($c === 9) $live['screen'] = ['identity'=>$teacher['identity'],'sid'=>$sid,'until'=>$now+360];
                    $s->put('lesson', $id, $live);
                    $s->put('active', (string)$c, ['id'=>$id]);
                    need(copy($dir . '/board.pdf', cl_lesson_dir($id, true) . '/' . QA_DOC), 'synthetic PDF required');
                    $state = cl_activity($s, $id);
                    $state['doc'] = QA_DOC;
                    $state['docs'][QA_DOC] = ['id'=>QA_DOC,'mime'=>'application/pdf','bytes'=>filesize($dir . '/board.pdf'),'name'=>'QA.pdf','pages'=>1];
                    $s->put('activity',$id,$state);
                }
                need(count($trackTotals) === QA_ROOMS, 'nine rooms required');
                echo "QA_SCHOOL_SOURCES pupils=180 teachers=9 rooms=9 microphones=25 cameras=1 screens=1\n";
                break;
            case 'start':
                $count = (int)($argv[2] ?? -1);
                need(in_array($count, [0,3,9], true) && CL_REC_CAPTURE_LIMIT === ($count === 9 ? 9 : 3), 'unexpected tested capacity');
                $roster = cl_classes($db, '1405/1406');
                $chosen = $count === 9 ? $classes : ($count === 3 ? [1,8,9] : []);
                $start = static function (int $c) use ($s,$cfg,$roster,$now): array {
                    $live = cl_active($s, $c);
                    return cl_class_lock($s, $cfg, $c, static fn() => cl_rec_start($s,$cfg,$roster[$c],$live,teacher($c),$now));
                };
                foreach ($chosen as $c) need($start($c)['recording']['notice'] === 'preparing', 'recording countdown required');
                if ($count === 3) {
                    try { $start(2); throw new RuntimeException('stock incorrectly accepted fourth capture'); }
                    catch (ClassroomError $e) { need($e->status === 409 && str_contains($e->getMessage(), 'سه ضبط'), 'unexpected fourth capture refusal'); }
                    echo "QA_SCHOOL_LIMIT stock fourth live capture refused (409)\n";
                }
                break;
            case 'status':
                $jobs = [];
                foreach ($classes as $c) {
                    $job = $s->get('recording', bell($c));
                    if ($job === null) continue;
                    $jobs[sprintf('QA-C%02d', $c)] = ['state'=>$job['state'],'live'=>$job['live'],'sent'=>$job['sent'],'total'=>$job['total'],
                        'reason'=>$job['reason'] ?? '','not_before'=>$job['not_before'] ?? null,'rendered_at'=>$job['rendered_at'] ?? null,
                        'message'=>$job['last']['message'] ?? null,'cleaned'=>isset($job['cleaned'])];
                }
                echo reply((object)$jobs);
                break;
            case 'close':
                foreach ($classes as $c) {
                    $live = cl_active($s, $c);
                    need($live !== null, 'open synthetic bell required');
                    $now = time();
                    if (isset($live['recorder'])) {
                        $dec = cl_rec_close_choice($s,$cfg,$live,teacher($c),['recording'=>$live['id'],'sendRecording'=>true],$now);
                        need($dec === 'queued', 'approved synthetic film must be queued');
                        $live['recording_consent'] = true;
                    }
                    cl_close_lesson($s,$cfg,$live,$now,$rpc);
                    need(cl_active($s,$c) === null, 'verified retirement required');
                    if (isset($live['recorder'])) {
                        $job = $s->get('recording', $live['id']);
                        need($job['state'] === 'queued' && $job['not_before'] === $now + 60 && $job['sent'] === 0, 'off-peak delay required');
                    }
                }
                echo "QA_SCHOOL_CLOSE nine verified retirements; only consented jobs queued with a one-minute test delay\n";
                break;
            default: throw new RuntimeException('unexpected fixture operation');
        }
        exit;
    }

    // The TLS stub never forwards this local-only synthetic writer. A write
    // uses the real board helpers in the packaged plugin. A sample pupil per
    // bell verifies the delta; this is deliberately not the web capacity test.
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && $_SERVER['REQUEST_URI'] === '/qa-local-board'
        && $_SERVER['REMOTE_ADDR'] === '127.0.0.1') {
        $tick = (int)file_get_contents('php://input', false, null, 0, 12);
        need($tick > 0 && $tick <= 120, 'invalid board sample');
        $sizes = [];
        foreach (range(1, 7) as $c) {
            $lesson = cl_active($s, $c);
            need($lesson !== null && cl_lesson_live($lesson, $now), 'board needs live class');
            $actor = teacher($c);
            $board = cl_activity($s, $lesson['id']);
            $rev = $board['rev'];
            $ink = cl_layer($s, $lesson['id'], $board)['clock'] ?? 0;
            $y = 2500 + ($tick % 22) * 225;
            $points = [];
            for ($i = 0; $i < 12; $i++) $points[] = [1600 + 430*$i, $y + (int)(220*sin($i/1.7))];
            $input = ['wire'=>1,'board'=>$rev,'sid'=>$lesson['staff'][$actor['identity']], 'seq'=>$tick,
                'strokes'=>[['tool'=>'pen','id'=>sprintf('%032x', $tick),'part'=>0,'end'=>true,
                    'size'=>24,'color'=>'#ed1164','points'=>$points]]];
            $stroke = cl_board_action($s,$lesson,cl_ink_input($lesson,$actor,$input),'board-ink');
            $pupil = ['role'=>'student','id'=>($c-1)*QA_PUPILS+1,'identity'=>'student-' . (($c-1)*QA_PUPILS+1)];
            $view = cl_board_view($s,$lesson['id'],$pupil,['wire'=>1,'compact'=>true,'board'=>$rev,'ink'=>$ink],cl_activity($s,$lesson['id']),$lesson,$now);
            need($stroke['ink'] === $ink+count($points) && ($view['ink'] ?? $view['cursor'] ?? null) === $stroke['ink']
                && count($view['ops'] ?? []) === 1 && $view['ops'][0]['points'] === $points, 'a sample pupil must receive the new ink');
            $sizes[] = strlen(reply($view));
        }
        header('Content-Type: application/json');
        echo reply(['tick'=>$tick,'readers'=>count($sizes),'max_delta_json_bytes'=>max($sizes)]);
        exit;
    }

    if ($_SERVER['REQUEST_METHOD'] !== 'POST' || $_SERVER['REQUEST_URI'] !== '/campus/plugins/school-plugins.php?plugin=school-classroom&route=recording') {
        http_response_code(404); exit;
    }
    cl_rec_worker_auth($cfg, $_SERVER['HTTP_AUTHORIZATION'] ?? null);
    $input = json_decode(file_get_contents('php://input', false, null, 0, 32768), true, 20, JSON_THROW_ON_ERROR);
    // Keep only synthetic staff pulses current. This is not a PHP website load test.
    // Refresh on plans (one write per bell per 4s), not every board read.
    if (($input['action'] ?? null) === 'plan') {
        $s->tx(static function () use ($s,$classes,$now): void {
            foreach ($classes as $c) {
                $live = cl_active($s,$c);
                if (!$live || $live['phase'] !== 'live' || $now - $live['sample'] < 4) continue;
                $live['sample'] = $now;
                $live['pulses'][teacher($c)['identity']]['at'] = $now;
                $s->put('lesson',$live['id'],$live);
            }
        });
    }
    $result = cl_rec_worker($s,$cfg,$input,$now,$rpc,$provider);
    if (isset($result['file'])) cl_reply_board_file(...$result['file']);
    else {
        header('Content-Type: application/json');
        echo reply(['ok'=>true] + $result);
    }
} catch (Throwable $e) {
    if (PHP_SAPI === 'cli') {
        fwrite(STDERR,'QA school: ' . get_class($e) . ': ' . $e->getMessage() . "\n");
        exit(1);
    }
    http_response_code($e instanceof ClassroomError ? $e->status : 500);
    error_log('QA school ' . get_class($e) . ' action=' . ($input['action'] ?? 'auth') . ': ' . $e->getMessage());
}
