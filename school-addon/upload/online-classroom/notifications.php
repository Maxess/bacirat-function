<?php
declare(strict_types=1);
require_once __DIR__ . '/store.php';
require_once __DIR__ . '/school.php';

const CL_NOTICE_TTL = 172800;
const CL_PROVIDERS = ['bale','telegram','rubika'];

function cl_duration(int $seconds): string
{
    return sprintf('%02d:%02d:%02d', intdiv($seconds, 3600), intdiv($seconds % 3600, 60), $seconds % 60);
}

function cl_attendance_text(array $lesson, int $student): string
{
    $id = 'student-' . $student;
    return 'حضور در کلاس مجازی — ' . $lesson['roster'][$id] . "\n" . $lesson['class_name'] . ' · ' . $lesson['subject'] . ' · ' . $lesson['period']
        . "\nتاریخ (تهران): " . cl_display_day($lesson['date']) . "\nحضور: " . cl_persian_digits(cl_duration($lesson['seconds'][$id] ?? 0))
        . ' از ' . cl_persian_digits(cl_duration($lesson['active_seconds'])) . "\nزمان انتظار و وقفهٔ کلاس در این مدت منظور نشده است.";
}

function cl_delivery_add(ClassroomStore $s, string $lesson, string $type, int $delta = 1): void
{
    $report = $s->get('delivery', $lesson) ?? [];
    $report[$type] = ($report[$type] ?? 0) + $delta;
    $s->put('delivery', $lesson, $report);
}

function cl_delivery_adapter(array $cfg, callable $host, string $provider): ?array
{
    if (!in_array($provider, CL_PROVIDERS, true) || ($cfg['notify_school_' . $provider] ?? false) !== true) return null;
    try { return $host($provider); }
    catch (Throwable $error) {
        error_log('School classroom: a school bot integration is unavailable');
        return null;
    }
}

function cl_chat_links(array $bot, int $student): array
{
    $links = ($bot['links'])($student);
    $safe = [];
    foreach ($links as $id) if ((is_int($id) || is_string($id)) && preg_match('/^[A-Za-z0-9_-]{1,120}$/D', (string)$id)) $safe[] = (string)$id;
    return array_values(array_unique($safe));
}

function cl_outbox_key(array $item, int $due): string
{
    return sprintf('%012d:', $due) . hash('sha256', json_encode([$item['lesson'],$item['student'],$item['provider'],$item['chat']], JSON_THROW_ON_ERROR));
}

function cl_expand_outbox(ClassroomStore $s, array $cfg, callable $host, string $id, array $pending, int $now): void
{
    $lesson = $s->get('lesson', $id);
    $s->tx(static function () use ($s, $cfg, $host, $id, $pending, $now, $lesson) {
        if ($lesson && $lesson['phase'] === 'finished' && $lesson['ended'] + CL_NOTICE_TTL > $now) {
            foreach ($pending['providers'] as $provider) {
                $bot = cl_delivery_adapter($cfg, $host, $provider);
                if (!$bot) { cl_delivery_add($s, $id, 'skipped', count($lesson['roster'])); continue; }
                foreach ($lesson['roster'] as $identity=>$unused) {
                    $student = (int)substr($identity, 8);
                    try { $links = cl_chat_links($bot, $student); }
                    catch (PDOException $error) {
                        if (!in_array($error->getCode(), ['42S02','42S22'], true)) throw $error;
                        $links = [];
                    }
                    if (!$links) cl_delivery_add($s, $id, 'skipped');
                    foreach ($links as $chat) {
                        $item = ['lesson'=>$id,'student'=>$student,'provider'=>$provider,'chat'=>$chat,
                            'revision'=>$bot['revision'],'attempt'=>0];
                        $s->put('host-outbox', cl_outbox_key($item, $now), $item);
                    }
                }
            }
        }
        $s->delete('host-pending', $id);
    });
}

function cl_deliver_one(ClassroomStore $s, array $cfg, callable $host, string $key, array $item, int $now, array &$activation): void
{
    ['lesson'=>$id, 'student'=>$student, 'provider'=>$provider, 'chat'=>$chat] = $item;
    $lesson = $s->get('lesson', $id);
    $bot = cl_delivery_adapter($cfg, $host, $provider);
    $outcome = 'skipped';
    $started = false;
    $retry = false;
    try {
        if ($bot && $lesson && $lesson['phase'] === 'finished' && $lesson['ended'] + CL_NOTICE_TTL > $now
            && hash_equals($item['revision'], $bot['revision']) && cl_school_account($s->db, 'student', $student, $lesson['year'])
            && in_array($chat, cl_chat_links($bot, $student), true)) {
            $tag = $provider . ':' . $item['revision'];
            if (!array_key_exists($tag, $activation)) {
                try { $activation[$tag] = ($bot['active'])() === true; }
                catch (Throwable $error) { $activation[$tag] = null; }
            }
            if ($activation[$tag] === null) $retry = true;
            elseif ($activation[$tag] && ($bot['private'])($chat) === true) {
                // Recheck the current host link/config after the network round trips, before consuming the entry.
                $current = cl_delivery_adapter($cfg, $host, $provider);
                if ($current && hash_equals($item['revision'], $current['revision']) && in_array($chat, cl_chat_links($current, $student), true)) {
                    $s->tx(static function () use ($s, $key, $id) {
                        $s->delete('host-outbox', $key);
                        cl_delivery_add($s, $id, 'uncertain');
                    });
                    $started = true;
                    $outcome = ($current['send'])($student, $chat, cl_attendance_text($lesson, $student)) === true ? 'sent' : 'uncertain';
                }
            }
        }
    } catch (Throwable $error) {
        // Never retry an ambiguous send. Before sending, communication errors can be retried in a later batch.
        $retry = !$started;
        $outcome = $started ? 'uncertain' : 'failed';
    }
    $s->tx(static function () use ($s, $key, $id, $item, $now, $outcome, $started, $retry) {
        $s->delete('host-outbox', $key);
        if ($started) cl_delivery_add($s, $id, 'uncertain', -1);
        if ($retry && $item['attempt'] < 3) {
            $item['attempt']++;
            $s->put('host-outbox', cl_outbox_key($item, $now + 60 * $item['attempt']), $item);
        } else cl_delivery_add($s, $id, $retry ? 'failed' : $outcome);
    });
}

function cl_delivery_job(ClassroomStore $s, array $cfg, callable $host, int $limit = 40): void
{
    cl_room_lock($s->db, $cfg['room_prefix'] . ':delivery', static function () use ($s, $cfg, $host, $limit) {
        $activation = [];
        $start = time();
        foreach ($s->rows('host-pending', 4) as $id=>$pending) cl_expand_outbox($s, $cfg, $host, $id, $pending, $start);
        foreach ($s->rows('host-outbox', $limit) as $key=>$item) {
            $now = time();
            if ($now - $start >= 35 || (int)substr($key, 0, 12) > $now) break;
            cl_deliver_one($s, $cfg, $host, $key, $item, $now, $activation);
        }
    });
}
