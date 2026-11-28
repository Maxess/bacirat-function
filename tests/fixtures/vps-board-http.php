<?php
// Local synthetic request/DB benchmark, not an application entry point.
declare(strict_types=1);
const QA_BOARD_ROOMS = 7;
const QA_BOARD_PREFIX = 'test-bbbbbbbbbbbbbbbbbbbb';
const QA_BOARD_TABLE = 'mtg_plug_store_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';

class BoardQAStatement extends PDOStatement
{
    protected function __construct() {}
    public function execute(?array $params = null): bool
    {
        $GLOBALS['sql']++;
        return parent::execute($params);
    }
}
function board_qa_lesson(int $c): string { return sprintf('%032x', $c); }
function board_qa_actor(int $c, int $seat): array
{
    $role = $seat ? 'student' : 'teacher';
    $id = $seat ? ($c-1)*20+$seat : $c;
    return ['role'=>$role,'id'=>$id,'identity'=>$role.'-'.$id,'office'=>null];
}

$site = getenv('BACI_BOARD_QA_SITE');
$dsn = getenv('BACI_BOARD_QA_DSN');
if (!$site || !is_dir($site) || !preg_match('/^mysql:host=127\.0\.0\.1;port=[0-9]+;dbname=baci_board_qa;charset=utf8mb4$/D', (string)$dsn)
    || (PHP_SAPI !== 'cli' && ($_SERVER['REMOTE_ADDR'] ?? null) !== '127.0.0.1')) exit(1);
require $site . '/activity.php';
$db = new PDO($dsn, 'board_qa', 'disposable-QA', [PDO::ATTR_EMULATE_PREPARES=>false, PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_STATEMENT_CLASS=>[BoardQAStatement::class]]);
$s = new ClassroomStore($db, QA_BOARD_TABLE);
$cfg = ['room_prefix'=>QA_BOARD_PREFIX];
$now = time();
$classes = range(1,QA_BOARD_ROOMS);
$sql = 0;
if (PHP_SAPI === 'cli') {
    if (($argv[1] ?? '') !== 'init') exit(1);
    $db->exec('DROP TABLE IF EXISTS ' . QA_BOARD_TABLE);
    $db->exec('CREATE TABLE ' . QA_BOARD_TABLE . ' (bucket VARCHAR(80), item VARCHAR(120), payload MEDIUMTEXT, PRIMARY KEY(bucket,item))');
    foreach ($classes as $c) {
        $id = board_qa_lesson($c);
        $room = QA_BOARD_PREFIX . '-v4-c' . $c . '-' . substr($id, -24);
        $pulses = $seen = [];
        $owner = board_qa_actor($c,0)['identity'];
        for ($seat = 0; $seat <= 20; ++$seat) {
            $identity = board_qa_actor($c,$seat)['identity'];
            $seen[$identity] = 'PA_' . $identity;
            $pulses[$identity] = ['sid'=>$seen[$identity],'at'=>$now];
        }
        $lesson = ['id'=>$id,'room'=>$room,'class_id'=>$c,'phase'=>'live','teacher'=>$c,'staff'=>[$owner=>$seen[$owner]],
            'seen'=>$seen,'pulses'=>$pulses,'sample'=>$now,'date'=>cl_day($now)->format('Y-m-d'),'deadline'=>$now+240,'access'=>[]];
        $s->put('lesson',$id,$lesson);
        $s->put('active',(string)$c,['id'=>$id]);
    }
    exit;
}

try {
    if ($_SERVER['REQUEST_METHOD'] !== 'POST' || $_SERVER['REQUEST_URI'] !== '/board') throw new ClassroomError(404, 'fixture route');
    $n = filter_var($_SERVER['HTTP_X_QA_SEAT'] ?? false,FILTER_VALIDATE_INT);
    $input = json_decode(file_get_contents('php://input',false,null,0,32768),true,20,JSON_THROW_ON_ERROR);
    $c = $input['class'] ?? null;
    if (!is_int($c) || !in_array($c,$classes,true) || $n === false || $n < 0 || $n > 20) throw new ClassroomError(403, 'fixture seat');
    $actor = board_qa_actor($c,$n);
    $begin = hrtime(true);
    $wait = 0;
    $result = cl_class_lock($s,$cfg,$c,static function () use ($s,$cfg,$c,$actor,$input,$now,$n,$begin,&$wait): array {
        $wait = (hrtime(true)-$begin)/1e6;
        $lesson = cl_active($s,$c);
        $action = $input['action'];
        $scope = ['lesson'=>$lesson['id'],'epoch'=>cl_epoch($lesson)];
        if ($action === 'ticket') return $scope + ['url'=>'wss://synthetic.invalid','token'=>'fixture'];
        if ($action === 'status') {
            $base = $scope + ['ready'=>true,'phase'=>'live','joinable'=>true,'slot'=>1];
            if (!isset($input['sid'])) return $base;
            // Only the transport presence is pre-seeded. Real per-browser pulses,
            // board reads, ordered writes, SQL and per-class MySQL locks run here.
            if (!$n) $lesson['sample'] = $now;
            $lesson = cl_touch($s,$lesson,$actor,$input,$now);
            $lesson = cl_check_live($cfg,$lesson,$actor,$input,$now);
            return $base + ['admitted'=>true,'mediaMode'=>'audio','access'=>$lesson['access']] + cl_activity_sync($s,$lesson,$actor,$input,$now);
        }
        if ($action === 'leave') return [];
        $lesson = cl_check_live($cfg,$lesson,$actor,$input,$now);
        return match ($action) {
            'board-sync' => ['board'=>cl_board_read($s,$lesson,$actor,$input,$_SERVER['REQUEST_TIME_FLOAT'],$now)],
            'board-ink' => cl_board_action($s,$lesson,cl_ink_input($lesson,$actor,$input),$action),
            'set-access' => cl_set_access($s,$cfg,$lesson,$actor,$input,$now,static fn() => throw new LogicException('no SFU calls')),
            default => throw new ClassroomError(400,'fixture action'),
        };
    });
    header('Content-Type: application/json');
    header('X-QA-SQL: ' . $sql);
    header('X-QA-Lock-Ms: ' . $wait);
    $body = json_encode(['ok'=>true]+$result,JSON_THROW_ON_ERROR);
    header('X-QA-App-Ms: ' . ((hrtime(true)-$begin)/1e6));
    echo $body;
} catch (Throwable $error) {
    error_log('QA board: ' . get_class($error) . ': ' . $error->getMessage());
    http_response_code($error instanceof ClassroomError ? $error->status : 500);
    echo '{"ok":false}';
}
