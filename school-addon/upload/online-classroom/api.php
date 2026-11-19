<?php
declare(strict_types=1);
require_once __DIR__ . '/core.php';

cl_headers();
header('Content-Type: application/json; charset=UTF-8');

try {
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
        header('Allow: POST');
        throw new ClassroomError(405, 'فقط درخواست POST مجاز است.');
    }
    $cfg = cl_config();
    if (isset($_SERVER['HTTP_ORIGIN']) && $_SERVER['HTTP_ORIGIN'] !== $cfg['site_origin']) {
        throw new ClassroomError(403, 'مبدأ درخواست معتبر نیست.');
    }
    if (strtolower(trim(explode(';', (string)($_SERVER['CONTENT_TYPE'] ?? ''))[0])) !== 'application/json') {
        throw new ClassroomError(415, 'نوع درخواست پذیرفته نیست.');
    }
    $raw = file_get_contents('php://input', false, null, 0, 4097);
    if ($raw === false || strlen($raw) > 4096) {
        throw new ClassroomError(413, 'درخواست بیش از اندازه بزرگ است.');
    }
    $body = json_decode($raw, true);
    if (!is_array($body) || !is_string($body['action'] ?? null)) {
        throw new ClassroomError(400, 'درخواست قابل خواندن نیست.');
    }
    $pdo = cl_load_site($cfg);
    $csrf = $_SESSION['school_classroom_csrf'] ?? null;
    if (!is_string($csrf) || !hash_equals($csrf, (string)($_SERVER['HTTP_X_CLASSROOM_CSRF'] ?? ''))) {
        throw new ClassroomError(403, 'نشست صفحه منقضی شده؛ صفحهٔ کلاس را دوباره باز کن.');
    }
    $session = $_SESSION;
    session_write_close();
    $class = cl_class($pdo, $cfg);
    $actor = cl_actor($pdo, $session, $class);
    $room = $class['room'];
    $rpc = static fn(string $method, array $data): array => cl_rpc($cfg, $room, $method, $data);
    $action = $body['action'];
    if (!in_array($action, ['join', 'open', 'close', 'floor'], true)) {
        throw new ClassroomError(400, 'عملیات شناخته نشد.');
    }
    if ($action !== 'join' && !$actor['moderator']) {
        throw new ClassroomError(403, 'این عملیات فقط برای دبیر یا مدیر مجاز است.');
    }
    if ($action === 'open') {
        cl_room_lock($pdo, $room, static fn() => $rpc('CreateRoom', [
            'name' => $room, 'maxParticipants' => 4, 'emptyTimeout' => 300, 'departureTimeout' => 20,
        ]));
    } elseif ($action === 'close') {
        cl_room_lock($pdo, $room, static fn() => $rpc('DeleteRoom', ['room' => $room]));
    } elseif ($action === 'floor') {
        $target = $body['identity'] ?? null;
        if ($target !== null && (!is_string($target) || !preg_match('/^(student|teacher|admin)-[1-9]\d{0,17}$/D', $target))) {
            throw new ClassroomError(400, 'شناسهٔ شرکت‌کننده معتبر نیست.');
        }
        cl_room_lock($pdo, $room, static fn() => cl_transfer_floor($room, $target, $rpc));
    } else {
        $rooms = $rpc('ListRooms', ['names' => [$room]])['rooms'] ?? [];
        if (!in_array($room, array_column($rooms, 'name'), true)) {
            throw new ClassroomError(409, 'دبیر هنوز کلاس را شروع نکرده یا کلاس پایان یافته است.');
        }
    }
    $response = ['ok' => true];
    if ($action === 'join' || $action === 'open') {
        $response += ['url' => $cfg['signal_url'], 'token' => cl_join_ticket($cfg, $room, $actor)];
    }
    echo json_encode($response, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
} catch (ClassroomError $error) {
    if (session_status() === PHP_SESSION_ACTIVE) {
        session_write_close();
    }
    http_response_code($error->status);
    echo json_encode(['ok' => false, 'message' => $error->getMessage()], JSON_UNESCAPED_UNICODE);
} catch (Throwable $error) {
    if (session_status() === PHP_SESSION_ACTIVE) {
        session_write_close();
    }
    http_response_code(503);
    error_log('School classroom pilot: endpoint failed (' . get_class($error) . ')');
    echo json_encode(['ok' => false, 'message' => 'تنظیمات هاست یا پایگاه دادهٔ آزمایشی را بررسی کن.'], JSON_UNESCAPED_UNICODE);
}
