<?php
declare(strict_types=1);
require_once __DIR__ . '/school.php';
require_once __DIR__ . '/store.php';

const CL_SAMPLE_TTL = 11;
const CL_PULSE_TTL = 12;
const CL_START_SECONDS = 32;
const CL_MAX_LESSON_SECONDS = 7200;

function cl_room_class(array $cfg, string $room): int
{
    return preg_match('/^' . preg_quote($cfg['room_prefix'], '/') . '-v3-c([1-9][0-9]{0,9})-[a-f0-9]{24}$/D', $room, $m) ? cl_id($m[1]) : 0;
}

function cl_class_lock(ClassroomStore $s, array $cfg, int $id, callable $work): mixed
{
    return cl_room_lock($s->db, $cfg['room_prefix'] . ':class:' . $id, $work);
}

function cl_lesson_id(array $class, string $period, string $date): string
{
    // A weekly reimport can replace row IDs; it cannot create a second occurrence of today's same bell.
    return substr(hash('sha256', $class['id'] . ':' . $class['year'] . ':' . $date . ':' . cl_period_key($period)), 0, 32);
}

function cl_active(ClassroomStore $s, int $id): ?array
{
    $index = $s->get('active', (string)$id);
    if (!$index) return null;
    return $s->get('lesson', $index['id']) ?? throw new LogicException('Missing active lesson');
}

function cl_epoch(array $lesson): ?string
{
    return $lesson['room'] === null ? null : substr($lesson['room'], -24);
}

function cl_match_transport(array $lesson, array $input): bool
{
    return $lesson['room'] !== null && ($input['lesson'] ?? null) === $lesson['id'] && ($input['epoch'] ?? null) === cl_epoch($lesson);
}

function cl_can_lesson(array $lesson, array $actor): bool
{
    return !cl_is_staff($actor) || cl_assigned($actor, $lesson['year'], $lesson['teacher']);
}

function cl_watch_ready(ClassroomStore $s, int $now): bool
{
    $seen = $s->get('meta', 'watch');
    return $seen && ($seen['at'] ?? 0) >= $now - CL_SAMPLE_TTL;
}

function cl_has_staff(array $lesson, int $now): bool
{
    return (bool)array_filter($lesson['staff'], static fn($sid, $who) => ($lesson['pulses'][$who]['sid'] ?? null) === $sid
        && $lesson['pulses'][$who]['at'] >= $now - CL_PULSE_TTL, ARRAY_FILTER_USE_BOTH);
}

function cl_lesson_live(array $lesson, int $now): bool
{
    return $lesson['phase'] === 'live' && $lesson['sample'] >= $now - CL_SAMPLE_TTL && $now < $lesson['deadline']
        && $lesson['date'] === cl_day($now)->format('Y-m-d')
        && cl_has_staff($lesson, $now);
}

function cl_sid(mixed $sid): bool
{
    return is_string($sid) && preg_match('/^[A-Za-z0-9_-]{4,72}$/D', $sid) === 1;
}

function cl_entered(array $lesson, array $actor, mixed $sid, int $now): bool
{
    $id = $actor['identity'];
    return cl_sid($sid) && ($lesson['seen'][$id] ?? null) === $sid && ($lesson['pulses'][$id]['sid'] ?? null) === $sid
        && ($lesson['pulses'][$id]['at'] ?? 0) >= $now - CL_PULSE_TTL;
}

function cl_check_live(?array $lesson, array $actor, array $input, int $now): array
{
    if (!$lesson || !cl_can_lesson($lesson, $actor) || !cl_lesson_live($lesson, $now) || !cl_match_transport($lesson, $input)
        || !cl_entered($lesson, $actor, $input['sid'] ?? null, $now)) {
        throw new ClassroomError(409, 'کلاس در حالت انتظار است یا ورودت هنوز تأیید نشده است.');
    }
    return $lesson;
}

function cl_touch(ClassroomStore $s, array $lesson, array $actor, array $input, int $now): array
{
    $sid = $input['sid'] ?? null;
    if (cl_match_transport($lesson, $input) && cl_sid($sid) && in_array($lesson['phase'], ['starting','live'], true)) {
        $id = $actor['identity'];
        $existing = $lesson['pulses'][$id] ?? null;
        if (!$existing || $existing['at'] < $now || $existing['sid'] !== $sid) {
            if ($existing && ($existing['at'] < $now - CL_PULSE_TTL || $existing['sid'] !== $sid)) {
                unset($lesson['pulses'][$id], $lesson['staff'][$id], $lesson['seen'][$id]);
            }
            if ($lesson['phase'] === 'live' && !cl_has_staff($lesson, $now)) cl_pause($lesson);
            else $lesson['pulses'][$id] = ['sid'=>$sid, 'at'=>$now];
            $s->put('lesson', $lesson['id'], $lesson);
        }
    }
    return $lesson;
}

function cl_open_lesson(ClassroomStore $s, array $ctx, array $cfg, array $class, array $actor, array $input, int $now, callable $rpc): array
{
    if (!cl_is_staff($actor)) throw new ClassroomError(403, 'شروع کلاس فقط برای دبیر یا ناظر مجاز است.');
    if (!cl_watch_ready($s, $now)) throw new ClassroomError(503, 'پایش امن حضور رسانه فعال نیست؛ سرویس ناظر را روی VPS بررسی کن. (WATCH-OFFLINE)');
    $slotId = cl_id($input['slot'] ?? null);
    $slot = $slotId ? cl_get_slot($s->db, $class, $slotId, $now) : null;
    if (!$slot || !cl_assigned($actor, $class['year'], $slot['teacher'])) {
        throw new ClassroomError(403, 'برای شروع، زنگ همین کلاس از برنامهٔ امروز و دبیر مجاز آن را انتخاب کن.');
    }
    $assigned = cl_school_account($s->db, 'teacher', $slot['teacher'], $class['year']);
    if (!$assigned || $assigned['year'] !== $class['year']) throw new ClassroomError(409, 'دبیرِ این زنگ برای سال پیش‌فرض فعال نیست.');
    $date = cl_day($now)->format('Y-m-d');
    $id = cl_lesson_id($class, $slot['period'], $date);
    $lesson = cl_active($s, $class['id']);
    if ($lesson && ($lesson['id'] !== $id || $lesson['phase'] === 'closing' || $now >= $lesson['deadline'])) {
        throw new ClassroomError(409, 'جلسهٔ دیگری برای این کلاس باز یا در حال بسته‌شدن است.');
    }
    if ($lesson && $lesson['room'] !== null) {
        if (in_array($lesson['phase'], ['starting','live'], true)) return $lesson;
        throw new ClassroomError(409, 'در حال بستن اتصال قبلی کلاس؛ چند لحظه صبر کن.');
    }
    if (!$lesson && $s->get('lesson', $id)) throw new ClassroomError(409, 'این زنگ امروز پایان یافته است.');
    $lesson ??= ['id'=>$id,'class_id'=>$class['id'],'class_name'=>$class['name'],'year'=>$class['year'],'date'=>$date,
        'slot'=>$slotId,'period'=>$slot['period'],'subject'=>$slot['subject'],'teacher'=>$slot['teacher'],'teacher_name'=>$assigned['name'],
        'created'=>$now,'deadline'=>$now + CL_MAX_LESSON_SECONDS,'active_seconds'=>0,'started'=>null,
        'roster'=>cl_roster($s->db, $class),'seconds'=>[], 'pulses'=>[], 'seen'=>[], 'staff'=>[], 'sample'=>0];
    $lesson['room'] = $cfg['room_prefix'] . '-v3-c' . $class['id'] . '-' . bin2hex(random_bytes(12));
    $lesson['phase'] = 'starting';
    $lesson['transport_at'] = $now;
    $lesson['seen'] = $lesson['staff'] = $lesson['pulses'] = [];
    $lesson['sample'] = 0;
    // Record ownership before CreateRoom; even an uncertain network reply is cleaned up by the watcher.
    ($ctx['write_runtime'])('scheduled-v1');
    $s->tx(static function () use ($s, $class, $lesson, $id) {
        $s->put('lesson', $id, $lesson);
        $s->put('active', (string)$class['id'], ['id'=>$id]);
    });
    try {
        cl_create_room($lesson['room'], $cfg['max_participants'], $rpc);
    } catch (ClassroomError $error) {
        cl_pause($lesson);
        $s->put('lesson', $id, $lesson);
        throw $error;
    }
    return $lesson;
}

function cl_pause(array &$lesson, string $phase = 'paused'): void
{
    $lesson['phase'] = $phase;
    $lesson['seen'] = $lesson['staff'] = $lesson['pulses'] = [];
}

function cl_leave_lesson(ClassroomStore $s, array $lesson, array $actor, array $input, int $now): void
{
    $id = $actor['identity'];
    if (!cl_match_transport($lesson, $input) || !cl_sid($input['sid'] ?? null) || ($lesson['pulses'][$id]['sid'] ?? null) !== $input['sid']) return;
    unset($lesson['pulses'][$id], $lesson['seen'][$id], $lesson['staff'][$id]);
    if ($lesson['phase'] === 'live' && !cl_has_staff($lesson, $now)) cl_pause($lesson);
    $s->put('lesson', $lesson['id'], $lesson);
}

function cl_starting(array $lesson, int $now): bool
{
    return $lesson['phase'] === 'starting' && $lesson['sample'] >= $now - CL_SAMPLE_TTL
        && $now < min($lesson['transport_at'] + CL_START_SECONDS, $lesson['deadline'])
        && $lesson['date'] === cl_day($now)->format('Y-m-d');
}

function cl_joinable(array $lesson, array $actor, int $now): bool
{
    return cl_lesson_live($lesson, $now) || (cl_is_staff($actor) && cl_starting($lesson, $now));
}

function cl_ticket(array $cfg, ?array $lesson, array $actor, array $input, int $now): array
{
    if (!$lesson || !cl_can_lesson($lesson, $actor) || !cl_match_transport($lesson, $input)
        || !cl_joinable($lesson, $actor, $now)) {
        throw new ClassroomError(409, 'در انتظار اتصال دبیر یا ناظر مجاز هستیم.');
    }
    return ['url'=>$cfg['signal_url'], 'token'=>cl_join_ticket($cfg, $lesson['room'], $actor), 'lesson'=>$lesson['id'], 'epoch'=>cl_epoch($lesson)];
}

// Account only intervals bracketed by independent, fresh SFU samples and session pulses.
// Room rotation, an unobserved gap, a changed connection SID or a staff-free interval earns no time.
function cl_observe(array $lesson, array $observed, array $staff, int $at, int $now): array
{
    if ($at <= $lesson['sample'] || $at < $lesson['transport_at']) return $lesson;
    $lesson['pulses'] = array_filter($lesson['pulses'], static fn($pulse) => $pulse['at'] >= $now - CL_PULSE_TTL);
    $seen = [];
    foreach ($observed as $who=>$sid) if (($lesson['pulses'][$who]['sid'] ?? null) === $sid) $seen[$who] = $sid;
    $presentStaff = array_intersect_key($seen, $staff);
    if (!$presentStaff) {
        if ($lesson['phase'] === 'live' || $at >= $lesson['transport_at'] + CL_START_SECONDS) cl_pause($lesson);
        $lesson['sample'] = $at;
        return $lesson;
    }
    $elapsed = $at - $lesson['sample'];
    if ($lesson['phase'] === 'live' && $elapsed <= CL_SAMPLE_TTL && array_intersect_assoc($presentStaff, $lesson['staff'])) {
        $lesson['active_seconds'] += $elapsed;
        foreach (array_intersect_assoc($seen, $lesson['seen']) as $who=>$sid) {
            if (isset($lesson['roster'][$who])) $lesson['seconds'][$who] = ($lesson['seconds'][$who] ?? 0) + $elapsed;
        }
    }
    $lesson['phase'] = 'live';
    $lesson['started'] ??= $at;
    $lesson['sample'] = $at;
    $lesson['staff'] = $presentStaff;
    $lesson['seen'] = $seen;
    return $lesson;
}

function cl_watch_auth(array $cfg, string $raw, mixed $sig, mixed $body, int $now): array
{
    $wanted = hash_hmac('sha256', "school-classroom-watch-v1\n" . $cfg['site_origin'] . "\n" . $raw, $cfg['api_secret']);
    if (!is_string($sig) || !hash_equals($wanted, $sig) || !is_array($body) || ($body['watch_version'] ?? null) !== 2 || ($body['prefix'] ?? '') !== $cfg['room_prefix']
        || !is_int($body['at'] ?? null) || abs($now - $body['at']) > 10) {
        throw new ClassroomError(403, 'درخواست سرویس ناظر معتبر نیست.');
    }
    $rooms = $body['rooms'] ?? null;
    if (!is_array($rooms) || count($rooms) > 80) throw new ClassroomError(400, 'فهرست اتاق‌های ناظر معتبر نیست.');
    foreach ($rooms as $room=>$people) {
        if (!is_string($room) || !cl_room_class($cfg, $room) || !is_array($people) || count($people) > 60) throw new ClassroomError(400, 'وضعیت اتاق معتبر نیست.');
        foreach ($people as $id=>$sid) if (!cl_media_identity($id) || !cl_sid($sid)) throw new ClassroomError(400, 'وضعیت اتصال معتبر نیست.');
    }
    return $rooms;
}

function cl_watch_tick(ClassroomStore $s, array $cfg, array $rooms, int $at, int $now): array
{
    return cl_room_lock($s->db, $cfg['room_prefix'] . ':watch', static function () use ($s, $cfg, $rooms, $at, $now) {
        $last = $s->get('meta', 'watch');
        if (($last['at'] ?? 0) >= $at) throw new ClassroomError(409, 'نمونهٔ تکراری پایش.');
        try { $year = cl_current_year($s->db); }
        catch (ClassroomError $ignored) { $year = ''; } // Close old room access if the school removes its default year.
        $classes = $year === '' ? [] : cl_classes($s->db, $year);
        $byClass = [];
        foreach ($rooms as $room=>$people) $byClass[cl_room_class($cfg, $room)][$room] = $people;
        foreach ($s->rows('active', 1000) as $classId=>$unused) $byClass[(int)$classId] ??= [];
        $drops = $permit = [];
        foreach ($byClass as $id=>$observations) {
            $remove = cl_class_lock($s, $cfg, $id, static function () use ($s, $cfg, $classes, $id, $observations, $at, $now, &$permit) {
                $lesson = cl_active($s, $id);
                $room = $lesson['room'] ?? null;
                if (!$lesson) return array_keys($observations);
                if ($room !== null && cl_room_class($cfg, $room) !== $id) throw new LogicException('Invalid owned room record');
                $unowned = array_diff(array_keys($observations), [$room]);
                if ($at < $lesson['transport_at']) return $unowned; // Snapshot predates the CreateRoom operation.
                $present = $room !== null && array_key_exists($room, $observations);
                $class = $classes[$id] ?? null;
                $slot = $class ? cl_get_slot($s->db, $class, $lesson['slot'], $now) : null;
                if (!$slot || $slot['teacher'] !== $lesson['teacher'] || $slot['subject'] !== $lesson['subject']
                    || cl_period_key($slot['period']) !== cl_period_key($lesson['period']) || $lesson['year'] !== $class['year']
                    || $lesson['date'] !== cl_day($now)->format('Y-m-d') || $now >= $lesson['deadline']) cl_pause($lesson, 'closing');
                if ($lesson['phase'] === 'live' && $at - $lesson['sample'] > CL_SAMPLE_TTL) cl_pause($lesson);
                if ($lesson['phase'] === 'starting' && $now >= $lesson['transport_at'] + CL_START_SECONDS) cl_pause($lesson);
                if (in_array($lesson['phase'], ['paused','closing'], true)) {
                    if ($present) {
                        $s->put('lesson', $lesson['id'], $lesson);
                        return [...$unowned, $room];
                    }
                    if ($lesson['phase'] === 'closing') cl_finish_lesson($s, $cfg, $lesson, $now);
                    elseif ($lesson['room'] !== null) {
                        $lesson['room'] = null;
                        $s->put('lesson', $lesson['id'], $lesson);
                    }
                    return $unowned;
                }
                if (!$present && $lesson['phase'] === 'live') {
                    cl_pause($lesson);
                    $lesson['room'] = null;
                } elseif ($present) {
                    $observed = $observations[$room];
                    $students = $staff = [];
                    foreach ($observed as $identity=>$sid) {
                        [$role, $pk] = explode('-', $identity, 2);
                        if ($role === 'student') { $students[] = (int)$pk; continue; }
                        $account = cl_school_account($s->db, $role, cl_id($pk), $lesson['year']);
                        if ($account && cl_assigned($account, $lesson['year'], $lesson['teacher'])) $staff[$identity] = true;
                    }
                    $roster = cl_roster($s->db, $class, $students);
                    if (count($observed) !== count($staff) + count($roster)) {
                        cl_pause($lesson); // A disabled/moved account cannot keep a previously issued connection.
                    } else {
                        $lesson['roster'] += $roster; // Students enrolled mid-lesson appear once if admitted.
                        $lesson = cl_observe($lesson, $observed, $staff, $at, $now);
                    }
                }
                $s->put('lesson', $lesson['id'], $lesson);
                if ($present && (cl_lesson_live($lesson, $now) || cl_starting($lesson, $now))) $permit[$room] = $lesson['phase'];
                return $lesson['phase'] === 'paused' && $present ? [...$unowned, $room] : $unowned;
            });
            array_push($drops, ...$remove);
        }
        $s->put('meta', 'watch', ['at'=>$at]);
        return ['drop'=>$drops, 'permit'=>(object)$permit];
    });
}

function cl_finish_lesson(ClassroomStore $s, array $cfg, array $lesson, int $now): void
{
    $s->tx(static function () use ($s, $cfg, $lesson, $now) {
        $id = $lesson['id'];
        $s->delete('active', (string)$lesson['class_id']);
        if ($lesson['started'] === null) {
            $s->delete('lesson', $id);
        } else {
            foreach (['sample','transport_at','room','pulses','seen','staff','deadline'] as $key) unset($lesson[$key]);
            $lesson['phase'] = 'finished';
            $lesson['ended'] = $now;
            $s->put('lesson', $id, $lesson);
            $key = sprintf('%012d:%s', $lesson['created'], $id);
            $entry = array_intersect_key($lesson, array_flip(['id','class_id','class_name','year','date','period','subject','teacher_name','active_seconds']));
            $s->put('reports', $key, $entry);
            $s->put('reports:teacher-' . $lesson['teacher'], $key, $entry);
            $providers = array_values(array_filter(['bale','rubika','telegram'], static fn($p) => ($cfg['notify_' . $p] ?? false) === true));
            if ($providers && $lesson['active_seconds'] > 0) $s->put('notify-pending', $id, ['providers'=>$providers]);
        }
        cl_clear_activity($s, $id);
    });
    cl_clear_files($lesson['id']);
}

function cl_close_lesson(ClassroomStore $s, array $cfg, array $lesson, int $now, callable $rpc): void
{
    $room = $lesson['room'];
    cl_pause($lesson, 'closing');
    $s->put('lesson', $lesson['id'], $lesson);
    if ($room !== null) {
        if (cl_room_class($cfg, $room) !== $lesson['class_id']) throw new LogicException('Invalid owned room record');
        cl_delete_room($room, $rpc);
    }
    cl_finish_lesson($s, $cfg, $lesson, $now);
}
