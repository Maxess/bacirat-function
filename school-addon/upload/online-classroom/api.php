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
    $csrf = cl_csrf();
    if (!is_string($csrf) || !hash_equals($csrf, (string)($_SERVER['HTTP_X_CLASSROOM_CSRF'] ?? ''))) {
        throw new ClassroomError(403, 'نشست صفحه منقضی شده؛ صفحهٔ کلاس را دوباره باز کن.');
    }
    $session = $_SESSION;
    session_write_close();
    $class = cl_class($pdo, $cfg);
    $actor = cl_actor($pdo, $session, $class);
    $action = $body['action'];
    $mediaControl = in_array($action, CL_MEDIA_ACTIONS, true);
    if (!$mediaControl && !in_array($action, ['join', 'open', 'close'], true)) {
        throw new ClassroomError(400, 'عملیات شناخته نشد.');
    }
    if ($action !== 'join' && !$actor['moderator']) {
        throw new ClassroomError(403, 'این عملیات فقط برای دبیر یا مدیر مجاز است.');
    }
    $target = $mediaControl ? cl_media_target($action, $body['identity'] ?? null) : null;
    $rpc = static fn(string $method, string $room, array $data): array => cl_rpc($cfg, $room, $method, $data);
    $room = $class['room'];
    if ($context = cl_manager_context()) {
        $room = cl_room_lock($pdo, $cfg['room_prefix'], static fn() => cl_managed_action($cfg, $context, $action, $target, $rpc));
    } elseif ($action === 'open') {
        cl_room_lock($pdo, $room, static fn() => cl_create_room($room, $rpc));
    } elseif ($action === 'close') {
        cl_room_lock($pdo, $room, static fn() => $rpc('DeleteRoom', $room, ['room' => $room]));
    } elseif ($mediaControl) {
        cl_room_lock($pdo, $room, static fn() => cl_control_media($room, $action, $target, static fn($method, $data) => $rpc($method, $room, $data)));
    } elseif (!cl_room_exists($room, $rpc)) {
        throw new ClassroomError(409, 'دبیر هنوز کلاس را شروع نکرده یا کلاس پایان یافته است.');
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
