<?php
declare(strict_types=1);
require_once __DIR__ . '/lesson.php';
require_once __DIR__ . '/activity.php';

// The manager holds its exclusive lifecycle lock, so no classroom request mutates this installation here.
$context = cl_context();
$cfg = array_replace(cl_config(), ['notify_school_bale'=>false,'notify_school_telegram'=>false,'notify_school_rubika'=>false]);
$s = cl_store();
// Even a previously approved send must not resume after an operator disables the plugin.
if (($slot = $s->get('record-slot', 'current')) && ($job = $s->get('recording', $slot['id']))) {
    cl_rec_terminal($s, $job, isset($job['flight']) ? 'uncertain' : 'discarded', 'disabled');
}
$s->delete('record-worker', 'heartbeat');
$rpc = static fn(string $method, string $room, array $data): array => cl_rpc($cfg, $room, $method, $data);
$failed = null;
foreach ($s->rows('active', 1000) as $classId=>$unused) {
    try { if ($lesson = cl_active($s, (int)$classId)) cl_close_lesson($s, $cfg, $lesson, time(), $rpc); }
    catch (ClassroomError $error) { $failed = $error; }
}
$s->delete('host-pending');
$s->delete('host-outbox');
if ($failed !== null) throw $failed;
($context['write_runtime'])(null);
