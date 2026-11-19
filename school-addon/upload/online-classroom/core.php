<?php
declare(strict_types=1);

// School-side adapter. No schema changes, shared hosting and same-origin session.
final class ClassroomError extends RuntimeException
{
    public int $status;

    public function __construct(int $status, string $message)
    {
        parent::__construct($message);
        $this->status = $status;
    }
}

function cl_headers(): void
{
    header('Cache-Control: no-store, private');
    header('Referrer-Policy: no-referrer');
    header('X-Content-Type-Options: nosniff');
    header('Permissions-Policy: microphone=(self), camera=(), display-capture=()');
}

function cl_origin(string $value, string $scheme): string
{
    // A bare TLS origin: no credentials, path, query, fragment or arbitrary port.
    $part = '[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?';
    if (!preg_match('~^' . $scheme . '://(?:' . $part . '\.)+' . $part . '$~Di', $value)) {
        throw new ClassroomError(503, 'آدرس امن سایت یا رسانه در تنظیمات معتبر نیست.');
    }
    return strtolower($value);
}

function cl_config(): array
{
    $path = getenv('SCHOOL_CLASSROOM_CONFIG') ?: dirname(__DIR__, 2) . '/school-classroom.local.php';
    if (!is_file($path) || !is_readable($path)) {
        throw new ClassroomError(503, 'پایلوت هنوز روی این هاست تنظیم نشده است.');
    }
    $real = realpath($path);
    $public = !empty($_SERVER['DOCUMENT_ROOT']) ? realpath($_SERVER['DOCUMENT_ROOT']) : false;
    if (!$real || ($public && str_starts_with($real, rtrim($public, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR))) {
        throw new ClassroomError(503, 'فایل تنظیمات باید خارج از پوشهٔ عمومی سایت باشد.');
    }
    $cfg = require $real;
    if (!is_array($cfg) || ($cfg['enabled'] ?? null) !== true) {
        throw new ClassroomError(503, 'پایلوت در تنظیمات این هاست فعال نیست.');
    }
    $cfg['site_origin'] = cl_origin((string)($cfg['site_origin'] ?? ''), 'https');
    $cfg['signal_url'] = cl_origin((string)($cfg['signal_url'] ?? ''), 'wss');
    $cfg['api_url'] = 'https://' . substr($cfg['signal_url'], 6);
    if (!in_array(strtolower((string)($_SERVER['HTTPS'] ?? '')), ['on', '1'], true)
        || 'https://' . strtolower((string)($_SERVER['HTTP_HOST'] ?? '')) !== $cfg['site_origin']) {
        throw new ClassroomError(403, 'این افزونه فقط روی دامنهٔ HTTPS آزمایشیِ تنظیم‌شده فعال است.');
    }
    if (!is_int($cfg['class_id'] ?? null) || $cfg['class_id'] < 1
        || !is_string($cfg['expected_test_database'] ?? null) || $cfg['expected_test_database'] === ''
        || !preg_match('/^[a-z0-9][a-z0-9-]{2,39}$/D', (string)($cfg['room_prefix'] ?? ''))
        || !preg_match('/^[A-Za-z0-9_-]{12,64}$/D', (string)($cfg['api_key'] ?? ''))
        || !preg_match('/^[A-Za-z0-9_-]{32,128}$/D', (string)($cfg['api_secret'] ?? ''))) {
        throw new ClassroomError(503, 'شناسهٔ کلاس، نام پایگاه تست، پیشوند اتاق یا کلیدهای رسانه تنظیم نشده‌اند.');
    }
    return $cfg;
}

function cl_load_site(array $cfg): PDO
{
    $root = dirname(__DIR__);
    $functions = $root . '/includes/functions.php';
    $dbFile = $root . '/config/database.php';
    if (!is_file($functions) || !is_file($dbFile)) {
        throw new ClassroomError(503, 'پوشهٔ online-classroom را کنار پوشه‌های includes و config سایت تست قرار بده.');
    }
    // Reuse only the original session setup. Open MySQL explicitly to avoid the site's SQLite fallback.
    require_once $functions;
    $db = require $dbFile;
    if (!is_array($db) || ($db['driver'] ?? 'mysql') !== 'mysql'
        || ($db['database'] ?? null) !== $cfg['expected_test_database']) {
        throw new ClassroomError(503, 'نام پایگاه واقعی سایت با نام پایگاه تست در تنظیم پایلوت یکسان نیست.');
    }
    $host = $db['host'] ?? '127.0.0.1';
    $port = $db['port'] ?? 3306;
    $name = $db['database'];
    $charset = $db['charset'] ?? 'utf8mb4';
    return new PDO("mysql:host={$host};port={$port};dbname={$name};charset={$charset}",
        $db['username'] ?? '', $db['password'] ?? '', [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_EMULATE_PREPARES => false,
        ]);
}

function cl_year(?string $text): string
{
    $text = strtr(trim((string)$text), [
        '۰'=>'0','۱'=>'1','۲'=>'2','۳'=>'3','۴'=>'4','۵'=>'5','۶'=>'6','۷'=>'7','۸'=>'8','۹'=>'9',
        '٠'=>'0','١'=>'1','٢'=>'2','٣'=>'3','٤'=>'4','٥'=>'5','٦'=>'6','٧'=>'7','٨'=>'8','٩'=>'9',
        '–'=>'-','—'=>'-','−'=>'-',
    ]);
    if (!preg_match('/^(1[34]\d{2})\s*[-\/]\s*(1[34]\d{2})$/D', $text, $m)
        || (int)$m[2] !== (int)$m[1] + 1) {
        return '';
    }
    return $m[1] . '/' . $m[2];
}

function cl_same_class(array $class, ?string $name, ?string $year): bool
{
    // Fail closed on missing or ambiguous identifiers; never match a student by display name.
    return trim((string)$name) === trim($class['name']) && cl_year($year) === $class['year'];
}

function cl_query(PDO $pdo, string $sql, array $params): PDOStatement
{
    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    return $stmt;
}

function cl_class(PDO $pdo, array $cfg): array
{
    $class = cl_query($pdo, 'SELECT id, name, academic_year FROM classes WHERE id = ?', [$cfg['class_id']])->fetch(PDO::FETCH_ASSOC);
    if (!$class || trim((string)$class['name']) === '' || ($year = cl_year($class['academic_year'])) === '') {
        throw new ClassroomError(503, 'کلاس آزمایشی یا سال تحصیلیِ آن در جدول classes معتبر نیست.');
    }
    $duplicates = cl_query($pdo, 'SELECT academic_year FROM classes WHERE TRIM(name) = TRIM(?) AND id <> ?', [$class['name'], $cfg['class_id']]);
    while ($row = $duplicates->fetch(PDO::FETCH_ASSOC)) {
        if (cl_year($row['academic_year']) === $year) {
            throw new ClassroomError(503, 'در آن سال دو کلاس همنام وجود دارد؛ تطبیق عضویت امن نیست.');
        }
    }
    $class['year'] = $year;
    $class['room'] = $cfg['room_prefix'] . '-c' . $cfg['class_id'] . '-' . str_replace('/', '-', $year);
    return $class;
}

function cl_session_identity(array $session): array
{
    $found = [];
    foreach (['student', 'teacher', 'admin'] as $role) {
        $id = filter_var($session[$role . '_id'] ?? null, FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]);
        if ($id !== false) {
            $found[$role] = $id;
        }
    }
    if (count($found) !== 1) {
        throw new ClassroomError(count($found) === 0 ? 401 : 403,
            'با یک حساب دانش‌آموز، دبیر یا مدیر در پنل سایت آزمایشی وارد شو.');
    }
    $role = array_key_first($found);
    return [$role, $found[$role]];
}

function cl_actor(PDO $pdo, array $session, array $class): array
{
    [$role, $id] = cl_session_identity($session);
    if ($role === 'student') {
        $row = cl_query($pdo, 'SELECT first_name, last_name, class_name, academic_year, status FROM students WHERE id = ?', [$id])->fetch(PDO::FETCH_ASSOC);
        $allowed = $row && $row['status'] === 'active' && cl_same_class($class, $row['class_name'], $row['academic_year']);
        $name = ($row['first_name'] ?? '') . ' ' . ($row['last_name'] ?? '');
    } elseif ($role === 'teacher') {
        $row = cl_query($pdo, 'SELECT full_name, status FROM teachers WHERE id = ?', [$id])->fetch(PDO::FETCH_ASSOC);
        $allowed = false;
        if ($row && (int)$row['status'] === 1) {
            $schedule = cl_query($pdo, 'SELECT class_name, academic_year FROM class_schedules WHERE teacher_id = ?', [$id]);
            while ($entry = $schedule->fetch(PDO::FETCH_ASSOC)) {
                if (cl_same_class($class, $entry['class_name'], $entry['academic_year'])) {
                    $allowed = true;
                    break;
                }
            }
        }
        $name = (string)($row['full_name'] ?? '');
    } else {
        $row = cl_query($pdo, 'SELECT name, role, permissions, status FROM admins WHERE id = ?', [$id])->fetch(PDO::FETCH_ASSOC);
        $permissions = json_decode((string)($row['permissions'] ?? ''), true);
        $allowed = $row && (int)$row['status'] === 1 && ($row['role'] === 'super_admin'
            || (is_array($permissions) && (in_array('all', $permissions, true) || in_array('manage_classes', $permissions, true))));
        $name = (string)($row['name'] ?? '');
    }
    if (!$allowed) {
        throw new ClassroomError(403, 'این حساب برای کلاس آزمایشی مجوز ندارد. کلاس و سال تحصیلی یا برنامهٔ دبیر را بررسی کن.');
    }
    preg_match('/^.{1,60}/us', trim($name), $matches);
    return ['identity' => $role . '-' . $id, 'name' => $matches[0] ?? $role, 'role' => $role, 'moderator' => $role !== 'student'];
}

function cl_b64url(string $value): string
{
    return rtrim(strtr(base64_encode($value), '+/', '-_'), '=');
}

function cl_jwt(array $cfg, array $video, int $ttl, ?array $actor = null): string
{
    $now = time();
    $body = ['iss' => $cfg['api_key'], 'nbf' => $now - 5, 'iat' => $now, 'exp' => $now + $ttl, 'video' => $video];
    if ($actor !== null) {
        $body['sub'] = $actor['identity'];
        $body['name'] = $actor['name'];
    }
    $unsigned = cl_b64url('{"alg":"HS256","typ":"JWT"}') . '.' . cl_b64url(json_encode($body, JSON_THROW_ON_ERROR));
    return $unsigned . '.' . cl_b64url(hash_hmac('sha256', $unsigned, $cfg['api_secret'], true));
}

function cl_join_ticket(array $cfg, string $room, array $actor): string
{
    return cl_jwt($cfg, [
        'roomJoin' => true, 'room' => $room, 'canSubscribe' => true,
        'canPublish' => false, 'canPublishData' => false,
    ], 120, $actor);
}

function cl_service_grant(string $method, string $room): array
{
    return match ($method) {
        'CreateRoom', 'DeleteRoom' => ['roomCreate' => true],
        'ListRooms' => ['roomList' => true],
        'ListParticipants', 'UpdateParticipant' => ['roomAdmin' => true, 'room' => $room],
        default => throw new LogicException('Unknown room service action'),
    };
}

function cl_rpc(array $cfg, string $room, string $method, array $payload): array
{
    $grant = cl_service_grant($method, $room);
    $ch = curl_init($cfg['api_url'] . '/twirp/livekit.RoomService/' . $method);
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json', 'Authorization: Bearer ' . cl_jwt($cfg, $grant, 45)],
        CURLOPT_POSTFIELDS => json_encode($payload, JSON_THROW_ON_ERROR),
        CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 3, CURLOPT_TIMEOUT => 6,
    ]);
    $response = curl_exec($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    unset($ch);
    if ($status === 404 && is_string($response)) {
        $error = json_decode($response, true);
        if (is_array($error) && ($error['code'] ?? '') === 'not_found') {
            throw new ClassroomError(409, 'کلاس بسته شده یا شرکت‌کننده قطع شده است؛ دوباره بررسی کن.');
        }
    }
    $body = is_string($response) ? json_decode($response, true) : null;
    if ($status !== 200 || !is_array($body)) {
        // Never show bearer tokens, curl diagnostics or upstream bodies to users.
        throw new ClassroomError(502, 'ارتباط امن هاست مدرسه با سرور رسانه برقرار نشد.');
    }
    return $body;
}

function cl_room_lock(PDO $pdo, string $room, callable $work): mixed
{
    $key = 'classroom:' . substr(hash('sha256', $room), 0, 48);
    if ((int)cl_query($pdo, 'SELECT GET_LOCK(?, 2)', [$key])->fetchColumn() !== 1) {
        throw new ClassroomError(409, 'کلاس در حال تغییر است؛ لحظه‌ای بعد دوباره امتحان کن.');
    }
    try {
        return $work();
    } finally {
        cl_query($pdo, 'SELECT RELEASE_LOCK(?)', [$key]);
    }
}

function cl_transfer_floor(string $room, ?string $target, callable $rpc): void
{
    $peers = $rpc('ListParticipants', ['room' => $room])['participants'] ?? [];
    $positions = [];
    foreach ($peers as $peer) {
        if (!empty($peer['identity'])) {
            $positions[$peer['identity']] = $peer['permission'] ?? null;
        }
    }
    if ($target !== null && !array_key_exists($target, $positions)) {
        throw new ClassroomError(409, 'این شرکت‌کننده اکنون در کلاس نیست.');
    }
    $permission = ['canSubscribe' => true, 'canPublish' => false, 'canPublishData' => false];
    // No grant is sent if a previous publisher cannot be revoked.
    foreach ($positions as $name => $current) {
        if ($name !== $target && (!is_array($current) || ($current['canPublish'] ?? false) !== false)) {
            $rpc('UpdateParticipant', ['room' => $room, 'identity' => $name, 'permission' => $permission]);
        }
    }
    if ($target !== null) {
        $permission['canPublish'] = true;
        $permission['canPublishSources'] = ['MICROPHONE'];
        $rpc('UpdateParticipant', ['room' => $room, 'identity' => $target, 'permission' => $permission]);
    }
}
