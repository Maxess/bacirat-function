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
    $class = cl_class_for($s->db, cl_classes($s->db, $year), cl_id($_GET['class'] ?? null), $actor, cl_friday_window($cfg, time()) !== null);
    $content = cl_class_lock($s, $cfg, $class['id'], static function () use ($s, $cfg, $class, $actor) {
        $now = time();
        $live = cl_active($s, $class['id']);
        if (($_GET['light'] ?? null) === '1') {
            // A snapshot link is valid only for the current live epoch and displayed file.
            // This read path intentionally cannot serve photos or old/other board files.
            if (!cl_readable_lesson($cfg, $live, $class, $actor, $now) || !cl_match_transport($live, $_GET)
                || array_key_exists('photo', $_GET)) throw new ClassroomError(404, '');
            $state = cl_activity($s, $live['id']);
            if ($state['doc'] === null || ($_GET['doc'] ?? null) !== $state['doc']) throw new ClassroomError(404, '');
            return cl_board_file($live, $state, $state['doc']);
        }
        $live = cl_check_live($cfg, $live, $actor, $_GET, $now);
        if (array_key_exists('photo', $_GET)) {
            $identity = $_GET['photo'];
            if (!cl_media_identity($identity, true) || !cl_entered($live, ['identity'=>$identity], $live['seen'][$identity] ?? null, $now)) {
                throw new ClassroomError(404, '');
            }
            $id = cl_id(substr($identity, 8));
            $pupil = cl_school_account($s->db, 'student', $id, $class['year']);
            if (!$pupil || $pupil['class'] !== $class['key']) throw new ClassroomError(404, '');
            $source = cl_photo_paths($s->db, [$id])[$id] ?? null;
            $photo = cl_read_photo(cl_context()['site_root'] ?? '', $source);
            if (!$photo) throw new ClassroomError(404, '');
            return ['photo'=>$photo];
        }
        $key = $_GET['doc'] ?? null;
        $state = cl_activity($s, $live['id']);
        if (!is_string($key) || !preg_match('/^[a-f0-9]{32}$/D', $key) || !isset($state['docs'][$key])
            || (!cl_is_staff($actor) && $state['doc'] !== $key)) throw new ClassroomError(404, '');
        return cl_board_file($live, $state, $key);
    });
    if (isset($content['photo'])) {
        $photo = $content['photo'];
        header('Content-Type: ' . $photo['mime']);
        header('Content-Disposition: inline');
        header('Content-Length: ' . strlen($photo['raw']));
        echo $photo['raw'];
        return;
    }
    [$doc, $file] = $content;
    cl_reply_board_file($doc, $file);
} catch (ClassroomError $error) {
    http_response_code($error->status);
} catch (Throwable $error) {
    error_log('School classroom: attachment request failed (' . get_class($error) . ')');
    http_response_code(503);
} finally {
    if (is_resource($file)) fclose($file);
    if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
}
