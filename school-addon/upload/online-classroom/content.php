<?php
declare(strict_types=1);
require_once __DIR__ . '/activity.php';

cl_headers();
header("Content-Security-Policy: default-src 'none'; sandbox; frame-ancestors 'none'");
$file = null;
try {
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'GET') { header('Allow: GET'); throw new ClassroomError(405, ''); }
    $cfg = cl_config();
    $s = cl_store();
    $year = cl_current_year($s->db);
    $actor = cl_school_user($s->db, $_SESSION ?? [], $year);
    if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
    $class = cl_class_for($s->db, cl_classes($s->db, $year), cl_id($_GET['class'] ?? null), $actor);
    [$doc, $file] = cl_class_lock($s, $cfg, $class['id'], static function () use ($s, $class, $actor) {
        $now = time();
        if (!cl_watch_ready($s, $now)) throw new ClassroomError(409, '');
        $live = cl_check_live(cl_active($s, $class['id']), $actor, $_GET, $now);
        $key = $_GET['doc'] ?? null;
        $state = cl_activity($s, $live['id']);
        if (!is_string($key) || !preg_match('/^[a-f0-9]{32}$/D', $key) || !isset($state['docs'][$key])
            || (!cl_is_staff($actor) && $state['doc'] !== $key)) throw new ClassroomError(404, '');
        $dir = cl_lesson_dir($live['id'], false);
        $path = $dir ? $dir . '/' . $key : null;
        if (!$path || is_link($path) || !is_file($path) || realpath($path) !== $path || !($fp = @fopen($path, 'rb'))) throw new ClassroomError(404, '');
        return [$state['docs'][$key], $fp];
    });
    $size = $doc['bytes'];
    $start = 0; $end = $size - 1;
    $range = $_SERVER['HTTP_RANGE'] ?? null;
    if ($doc['mime'] === 'application/pdf') header('Accept-Ranges: bytes');
    if ($range !== null && $doc['mime'] === 'application/pdf') {
        if (!preg_match('/^bytes=(\d{1,10})-(\d{0,10})$/D', $range, $m) || (int)$m[1] >= $size || ($m[2] !== '' && (int)$m[2] < (int)$m[1])) {
            header('Content-Range: bytes */' . $size);
            throw new ClassroomError(416, '');
        }
        $start = (int)$m[1];
        $end = $m[2] === '' ? $end : min($end, (int)$m[2]);
        fseek($file, $start);
        http_response_code(206);
        header("Content-Range: bytes $start-$end/$size");
    }
    header('Content-Type: ' . $doc['mime']);
    header('Content-Disposition: attachment; filename="' . ($doc['mime'] === 'application/pdf' ? 'board.pdf' : 'board-image') . '"');
    header('Content-Length: ' . ($end - $start + 1));
    for ($left = $end - $start + 1; $left > 0 && !feof($file); $left -= strlen($chunk)) {
        $chunk = fread($file, min($left, 65536));
        if ($chunk === false || $chunk === '') break;
        echo $chunk;
    }
} catch (ClassroomError $error) {
    http_response_code($error->status);
} catch (Throwable $error) {
    error_log('School classroom: attachment request failed (' . get_class($error) . ')');
    http_response_code(503);
} finally {
    if (is_resource($file)) fclose($file);
    if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
}
