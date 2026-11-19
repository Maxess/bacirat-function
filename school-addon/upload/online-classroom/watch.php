<?php
declare(strict_types=1);
require_once __DIR__ . '/lesson.php';
require_once __DIR__ . '/activity.php';

// This machine-only route is intentionally independent of cookies.
cl_json_reply(static function (): array {
    cl_require_post();
    $cfg = cl_config();
    [$raw, $body] = cl_json_body(512 * 1024);
    $now = time();
    $rooms = cl_watch_auth($cfg, $raw, $_SERVER['HTTP_X_CLASSROOM_SIGNATURE'] ?? null, $body, $now);
    return cl_watch_tick(cl_store(), $cfg, $rooms, $body['at'], $now);
});
