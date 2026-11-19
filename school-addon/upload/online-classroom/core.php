<?php
declare(strict_types=1);

// Shared HTTP, session, locking and media primitives for the managed installation.
final class ClassroomError extends RuntimeException
{
    public int $status;

    public function __construct(int $status, string $message)
    {
        parent::__construct($message);
        $this->status = $status;
    }
}

require_once __DIR__ . '/media.php';

function cl_headers(): void
{
    header('Cache-Control: no-store, private');
    header('Referrer-Policy: same-origin');
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

function cl_manager_context(): ?array
{
    $context = $GLOBALS['MTAG_PLUGIN_CONTEXT'] ?? null;
    return is_array($context) ? $context : null;
}

function cl_context(): array
{
    $context = cl_manager_context();
    if (!$context || !isset($context['store'])) throw new ClassroomError(503, 'مدیر افزونه‌ها را به نسخهٔ سازگار به‌روزرسانی کن.');
    return $context;
}

function cl_href(array $params = []): string
{
    return cl_context()['base'] . ($params ? '&' . http_build_query($params, '', '&', PHP_QUERY_RFC3986) : '');
}

function cl_asset(string $name): string
{
    return cl_href(['route'=>'asset', 'name'=>'assets/' . $name]);
}

function cl_site_href(string $page): string
{
    return cl_context()['site_base'] . $page;
}

function cl_html(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function cl_config(): array
{
    $cfg = cl_context()['config'];
    $cfg['site_origin'] = cl_origin((string)($cfg['site_origin'] ?? ''), 'https');
    $cfg['signal_url'] = cl_origin((string)($cfg['signal_url'] ?? ''), 'wss');
    $cfg['api_url'] = 'https://' . substr($cfg['signal_url'], 6);
    if (!in_array(strtolower((string)($_SERVER['HTTPS'] ?? '')), ['on', '1'], true)
        || 'https://' . strtolower((string)($_SERVER['HTTP_HOST'] ?? '')) !== $cfg['site_origin']) {
        throw new ClassroomError(403, 'این افزونه فقط روی دامنهٔ HTTPS سایت تست فعال است.');
    }
    if (($cfg['enabled'] ?? null) !== true || !preg_match('/^test-[a-f0-9]{20}$/D', (string)($cfg['room_prefix'] ?? ''))
        || !preg_match('/^[A-Za-z0-9_-]{12,64}$/D', (string)($cfg['api_key'] ?? ''))
        || !preg_match('/^[A-Za-z0-9_-]{32,128}$/D', (string)($cfg['api_secret'] ?? ''))) {
        throw new ClassroomError(503, 'شناسه و کلیدهای رسانه در تنظیمات کلاس معتبر نیستند.');
    }
    $cfg['max_participants'] = max(4, min(60, (int)($cfg['max_participants'] ?? 4)));
    return $cfg;
}

function cl_require_post(): void
{
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
        header('Allow: POST');
        throw new ClassroomError(405, 'فقط درخواست POST مجاز است.');
    }
}

function cl_json_body(int $limit): array
{
    if (strtolower(trim(explode(';', (string)($_SERVER['CONTENT_TYPE'] ?? ''))[0])) !== 'application/json') {
        throw new ClassroomError(415, 'نوع درخواست پذیرفته نیست.');
    }
    $raw = file_get_contents('php://input', false, null, 0, $limit + 1);
    if ($raw === false || strlen($raw) > $limit) throw new ClassroomError(413, 'درخواست بیش از اندازه بزرگ است.');
    return [$raw, json_decode($raw, true, 24)];
}

function cl_json_reply(callable $work): void
{
    cl_headers();
    header('Content-Type: application/json; charset=UTF-8');
    try {
        echo json_encode(['ok'=>true] + $work(), JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
    } catch (ClassroomError $error) {
        http_response_code($error->status);
        echo json_encode(['ok'=>false,'message'=>$error->getMessage()], JSON_UNESCAPED_UNICODE);
    } catch (Throwable $error) {
        http_response_code(503);
        error_log('School classroom: endpoint failed (' . get_class($error) . ')');
        echo json_encode(['ok'=>false,'message'=>'ارتباط امن با سرویس کلاس موقتاً برقرار نیست.'], JSON_UNESCAPED_UNICODE);
    } finally {
        if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
    }
}

function cl_id(mixed $value): int
{
    if (!is_int($value) && !is_string($value)) return 0;
    return filter_var($value, FILTER_VALIDATE_INT, ['options'=>['min_range'=>1, 'max_range'=>2147483647]]) ?: 0;
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

function cl_query(PDO $pdo, string $sql, array $params): PDOStatement
{
    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    return $stmt;
}

function cl_session_identity(array $session): array
{
    $found = [];
    foreach (['student', 'teacher', 'admin'] as $role) {
        $id = cl_id($session[$role . '_id'] ?? null);
        if ($id) {
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
    ], 35, $actor);
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
    if ($pdo->getAttribute(PDO::ATTR_DRIVER_NAME) === 'sqlite') return $work(); // Local fixtures only.
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

function cl_create_room(string $room, int $max, callable $rpc): void
{
    $rpc('CreateRoom', $room, ['name'=>$room,'maxParticipants'=>$max,'emptyTimeout'=>60,'departureTimeout'=>20]);
}

function cl_delete_room(string $room, callable $rpc): void
{
    try {
        $rpc('DeleteRoom', $room, ['room'=>$room]);
    } catch (ClassroomError $error) {
        if ($error->status !== 409) throw $error; // Twirp not_found means already closed.
    }
}
