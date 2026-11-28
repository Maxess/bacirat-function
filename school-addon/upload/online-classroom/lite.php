<?php
declare(strict_types=1);
require_once __DIR__ . '/activity.php';

const CL_LITE_ROWS = 128;
const CL_LITE_POINTS = 2600;
const CL_LITE_MESSAGES = 16;

/** One bounded snapshot, only on navigation. No SFU calls, writes, attendance or background jobs. */
function cl_lite_snapshot(ClassroomStore $s, array $cfg, array $class, array $actor): ?array
{
    return cl_class_lock($s, $cfg, $class['id'], static function () use ($s, $cfg, $class, $actor) {
        $now = time();
        $lesson = cl_active($s, $class['id']);
        if (!cl_readable_lesson($cfg, $lesson, $class, $actor, $now)) return null;
        $id = $lesson['id'];
        $state = cl_activity($s, $id);
        $layer = cl_layer($s, $id, $state);
        return ['lesson'=>$id, 'epoch'=>cl_epoch($lesson), 'subject'=>$lesson['subject'], 'at'=>$now,
            'doc'=>$state['docs'][$state['doc'] ?? ''] ?? null, 'page'=>$state['page'],
            'chat'=>$state['messages'] ? $s->rows('chat:' . $id, CL_LITE_MESSAGES, cl_seq($state['messages'] + 1), true) : [],
            'undone'=>$layer['undone'] ?? [], 'ink'=>$layer['seq'] > CL_LITE_ROWS ? null
                : ($layer['seq'] ? $s->rows('ink:' . $id . ':' . $layer['key'], CL_LITE_ROWS) : [])];
    });
}

/** Return only bounded SVG geometry. A partial preview must never resurrect erased marks. */
function cl_lite_paths(?array $ink, array $removed): ?array
{
    if ($ink === null || count($ink) > CL_LITE_ROWS) return null;
    foreach ($ink as $op) {
        if ($op['tool'] === 'erase') return null; // Legacy pixel erasers cannot be represented by a stroke filter.
        if ($op['tool'] === 'erase-stroke') foreach ($op['targets'] as $id) $removed[$id] = true;
    }
    $paths = [];
    $points = 0;
    foreach ($ink as $seq=>$op) {
        if ($op['tool'] !== 'pen' || isset($removed[$op['id'] ?? 'old-' . $seq])) continue;
        $points += count($op['points']);
        if ($points > CL_LITE_POINTS) return null;
        $color = $op['color'];
        if (!preg_match('/^#[0-9a-f]{6}$/iD', $color)) return null;
        $width = max(1, min(36, (int)$op['size'])) * 18;
        $pairs = array_map(static fn(array $pt) => (int)$pt[0] . ',' . (int)$pt[1], $op['points']);
        if (count($pairs) === 1) $pairs[] = $pairs[0]; // Round linecap also draws a dot.
        $paths[] = ['d'=>'M' . implode(' ', $pairs), 'color'=>$color, 'width'=>$width];
    }
    return $paths;
}
