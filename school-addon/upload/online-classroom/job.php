<?php
declare(strict_types=1);
require_once __DIR__ . '/activity.php';
require_once __DIR__ . '/notifications.php';

$ctx = cl_context();
$mode = $ctx['job_mode'] ?? '';
if (!in_array($mode, ['cli','after-response','friday-cleanup'], true)) throw new ClassroomError(404, '');
$cfg = cl_config();
$s = cl_store();
// Host cron recovers unfinished delivery batches and finalizes expired lessons. It does not supervise live media.
if ($mode !== 'after-response') {
    $failed = false;
    $testOnly = $mode === 'friday-cleanup';
    $now = time();
    $rpc = static fn(string $method, string $room, array $data): array => cl_rpc($cfg, $room, $method, $data);
    foreach ($s->rows('active', 1000) as $id=>$index) {
        try {
            cl_class_lock($s, $cfg, (int)$id, static function () use ($s, $id, $cfg, $now, $rpc, $testOnly) {
                $lesson = cl_active($s, (int)$id);
                if ($lesson && (!$testOnly || cl_is_friday_test($lesson))
                    && (cl_lesson_expired($cfg, $lesson, $now) || $lesson['phase'] === 'closing')) {
                    cl_close_lesson($s, $cfg, $lesson, $now, $rpc);
                }
            });
        } catch (ClassroomError $error) {
            // The next authorized request or scheduled run retries; don't lose pending notices for other rooms.
            $failed = true;
            if ($error->status >= 500) error_log('School classroom: deferred close not confirmed');
        }
    }
    // The manager calls this after revoking the date, under its exclusive lock. Do not send real notifications here.
    if ($testOnly) {
        if ($failed) throw new ClassroomError(502, 'بستن بعضی اتاق‌های آزمایشی تأیید نشد.');
        return;
    }
}
// Return the HTTP reply before doing the same bounded batch as the CLI job.
cl_delivery_job($s, $cfg, $ctx['school_bot']);
