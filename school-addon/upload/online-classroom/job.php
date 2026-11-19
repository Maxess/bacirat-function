<?php
declare(strict_types=1);
require_once __DIR__ . '/activity.php';
require_once __DIR__ . '/notifications.php';

$ctx = cl_context();
$mode = $ctx['job_mode'] ?? '';
if (!in_array($mode, ['cli','after-response'], true)) throw new ClassroomError(404, '');
$cfg = cl_config();
$s = cl_store();
// Optional host cron finalizes abandoned lessons past their deadline. It does not supervise live media.
if ($mode === 'cli') {
    $now = time();
    $rpc = static fn(string $method, string $room, array $data): array => cl_rpc($cfg, $room, $method, $data);
    foreach ($s->rows('active', 1000) as $id=>$index) {
        try {
            cl_class_lock($s, $cfg, (int)$id, static function () use ($s, $id, $cfg, $now, $rpc) {
                $lesson = cl_active($s, (int)$id);
                if ($lesson && ($now >= $lesson['deadline'] || $lesson['date'] !== cl_day($now)->format('Y-m-d') || $lesson['phase'] === 'closing')) {
                    cl_close_lesson($s, $cfg, $lesson, $now, $rpc);
                }
            });
        } catch (ClassroomError $error) {
            // The next authorized request or scheduled run retries; don't lose pending notices for other rooms.
            if ($error->status >= 500) error_log('School classroom: deferred close not confirmed');
        }
    }
}
cl_delivery_job($s, $cfg, $ctx['school_bot'], $mode === 'cli' ? 40 : 1);
