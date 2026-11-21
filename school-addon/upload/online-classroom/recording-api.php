<?php
declare(strict_types=1);
require_once __DIR__ . '/activity.php';

// Machine-only, not a teacher session or bot webhook. No media-download route for browsers.
cl_headers();
header('Cache-Control: no-store, private, no-transform');
header("Content-Security-Policy: default-src 'none'; sandbox; frame-ancestors 'none'");
$file = null;
try {
    cl_require_post();
    $cfg = cl_config();
    cl_rec_worker_auth($cfg, $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? null);
    [, $input] = cl_json_body(8192);
    if (!is_array($input)) throw new ClassroomError(400, 'درخواست سرویس ضبط معتبر نیست.');
    $rpc = static fn(string $method, string $room, array $data): array => cl_rpc($cfg, $room, $method, $data);
    $out = cl_rec_worker(cl_store(), $cfg, $input, time(), $rpc);
    if (isset($out['file'])) {
        [$doc, $file] = $out['file'];
        cl_reply_board_file($doc, $file);
    } else cl_json_reply(static fn() => $out);
} catch (ClassroomError $error) {
    cl_json_reply(static fn() => throw $error);
} catch (Throwable $error) {
    error_log('School classroom: recording callback failed (' . get_class($error) . ')');
    cl_json_reply(static fn() => throw new ClassroomError(503, 'سرویس ضبط در دسترس نیست.'));
} finally {
    if (is_resource($file)) fclose($file);
}
