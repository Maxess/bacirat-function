<?php
declare(strict_types=1);
require_once __DIR__ . '/lesson.php';
require_once __DIR__ . '/activity.php';

// The manager holds its exclusive lifecycle lock, so no browser or watcher mutates this installation here.
$context = cl_context();
$cfg = cl_config();
$s = cl_store();
$rpc = static fn(string $method, string $room, array $data): array => cl_rpc($cfg, $room, $method, $data);
foreach ($s->rows('active', 1000) as $classId=>$unused) {
    if ($lesson = cl_active($s, (int)$classId)) cl_close_lesson($s, $cfg, $lesson, time(), $rpc);
}
($context['write_runtime'])(null);
