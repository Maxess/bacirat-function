<?php
declare(strict_types=1);
require_once __DIR__ . '/school.php';
require_once __DIR__ . '/store.php';

const CL_SAMPLE_TTL = 11;
const CL_SAMPLE_INTERVAL = 3;
const CL_PULSE_TTL = 12;
const CL_START_SECONDS = 32;
const CL_MAX_LESSON_SECONDS = 7200;

function cl_room_class(array $cfg, string $room): int
{
    return preg_match('/^' . preg_quote($cfg['room_prefix'], '/') . '-v[34]-c([1-9][0-9]{0,9})-[a-f0-9]{24}$/D', $room, $m) ? cl_id($m[1]) : 0;
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

function cl_is_friday_test(array $lesson): bool
{
    return isset($lesson['test']);
}

function cl_lesson_expired(array $cfg, array $lesson, int $now): bool
{
    if ($now >= $lesson['deadline'] || $lesson['date'] !== cl_day($now)->format('Y-m-d')) return true;
    if (!cl_is_friday_test($lesson)) return false;
    $grant = cl_friday_window($cfg, $now);
    return ($grant['id'] ?? null) !== $lesson['test'] || $grant['students'] !== (($lesson['test_students'] ?? false) === true);
}

function cl_can_lesson(array $lesson, array $actor): bool
{
    if (cl_is_friday_test($lesson)) {
        return cl_is_staff($actor) || (($lesson['test_students'] ?? false) === true
            && $actor['year'] === $lesson['year'] && $actor['class'] === cl_class_key($lesson['class_name']));
    }
    return !cl_is_staff($actor) || cl_assigned($actor, $lesson['year'], $lesson['teacher']);
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

function cl_check_live(array $cfg, ?array $lesson, array $actor, array $input, int $now): array
{
    if (!$lesson || !cl_can_lesson($lesson, $actor) || cl_lesson_expired($cfg, $lesson, $now)
        || !cl_lesson_live($lesson, $now) || !cl_match_transport($lesson, $input)
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

/** Server-clock choices for an idle class; a time alone never grants an assignment. */
function cl_staff_schedule(ClassroomStore $s, array $cfg, array $class, array $actor, int $now): array
{
    $hours = $cfg['bell_hours'] ?? [];
    $choices = [];
    $next = null;
    $soonest = PHP_INT_MAX;
    $date = cl_day($now)->format('Y-m-d');
    if (cl_is_staff($actor) && cl_friday_window($cfg, $now) !== null) {
        $choices[] = ['id'=>CL_FRIDAY_SLOT, 'label'=>CL_FRIDAY_TITLE . ' · ' . CL_FRIDAY_HOURS];
    }
    foreach (cl_today_slots($s->db, $class, $actor, $now) as $slot) {
        $window = cl_bell_window($hours, $slot['period'], $now);
        if ($hours && (!$window || $now >= $window['end'])) continue;
        $teacher = cl_school_account($s->db, 'teacher', $slot['teacher'], $class['year']);
        if (!$teacher || $teacher['year'] !== $class['year']
            || ($s->get('lesson', cl_lesson_id($class, $slot['period'], $date))['phase'] ?? null) === 'finished') continue;
        $label = $slot['period'] . ' · ' . $slot['subject'] . ($window ? ' · ' . $window['label'] : '');
        if ($window && $now < $window['start']) {
            if ($window['start'] < $soonest) {
                $soonest = $window['start'];
                $next = $label;
            }
        } else $choices[] = ['id'=>$slot['id'], 'label'=>$label];
    }
    return ['date'=>$date, 'timed'=>$hours !== [], 'choices'=>$choices, 'next'=>$next];
}

function cl_open_lesson(ClassroomStore $s, array $ctx, array $cfg, array $class, array $actor, array $input, int $now, callable $rpc): array
{
    if (!cl_is_staff($actor)) throw new ClassroomError(403, 'شروع کلاس فقط برای دبیر یا مسئول مجاز است.');
    $lesson = cl_active($s, $class['id']);
    $date = cl_day($now)->format('Y-m-d');
    $test = null;
    $window = null;
    if (($input['slot'] ?? null) === CL_FRIDAY_SLOT) {
        $test = $window = cl_friday_window($cfg, $now);
        if (!$test) throw new ClassroomError(403, 'آزمایش کلاس مجازی فقط در جمعهٔ تعیین‌شده باز است.');
        $id = ($lesson['test'] ?? null) === $test['id'] ? $lesson['id'] : bin2hex(random_bytes(16));
        $slot = ['id'=>CL_FRIDAY_SLOT,'period'=>'آزمایشی','subject'=>CL_FRIDAY_TITLE,'teacher'=>$actor['role'] === 'teacher' ? $actor['id'] : 0];
        $teacherName = $actor['name'];
    } else {
        $slotId = cl_id($input['slot'] ?? null);
        $slot = $slotId ? cl_get_slot($s->db, $class, $slotId, $now) : null;
        if (!$slot || !cl_assigned($actor, $class['year'], $slot['teacher'])) {
            throw new ClassroomError(403, 'برای شروع، زنگ همین کلاس از برنامهٔ امروز و دبیر مجاز آن را انتخاب کن.');
        }
        $assigned = cl_school_account($s->db, 'teacher', $slot['teacher'], $class['year']);
        if (!$assigned || $assigned['year'] !== $class['year']) throw new ClassroomError(409, 'دبیرِ این زنگ برای سال پیش‌فرض فعال نیست.');
        $teacherName = $assigned['name'];
        $id = cl_lesson_id($class, $slot['period'], $date);
    }
    if ($lesson && ($lesson['id'] !== $id || $lesson['year'] !== $class['year'] || $lesson['phase'] === 'closing' || cl_lesson_expired($cfg, $lesson, $now))) {
        throw new ClassroomError(409, 'جلسهٔ دیگری برای این کلاس باز یا در حال بسته‌شدن است.');
    }
    if ($lesson && $lesson['room'] !== null) {
        if (in_array($lesson['phase'], ['starting','live'], true)) return $lesson;
        throw new ClassroomError(409, 'در حال بستن اتصال قبلی کلاس؛ چند لحظه صبر کن.');
    }
    if (!$lesson) {
        if (!$test) {
            if ($s->get('lesson', $id)) throw new ClassroomError(409, 'این زنگ امروز پایان یافته است.');
            $hours = $cfg['bell_hours'] ?? [];
            if ($hours) {
                $window = cl_bell_window($hours, $slot['period'], $now);
                if (!$window) throw new ClassroomError(409, 'برای این زنگ در تنظیمات کلاس مجازی ساعتی ثبت نشده است.');
                if ($now < $window['start'] || $now >= $window['end']) {
                    throw new ClassroomError(409, 'این زنگ فقط از ' . $window['label'] . ' به وقت تهران قابل شروع است.');
                }
            }
        }
        // Save the cutoff on creation. Editing ordinary bell hours cannot change it later.
        $lesson = ['id'=>$id,'class_id'=>$class['id'],'class_name'=>$class['name'],'year'=>$class['year'],'date'=>$date,
            'slot'=>$slot['id'],'period'=>$slot['period'],'subject'=>$slot['subject'],'teacher'=>$slot['teacher'],'teacher_name'=>$teacherName,
            'created'=>$now,'deadline'=>$window['end'] ?? $now + CL_MAX_LESSON_SECONDS, 'bell'=>$window['label'] ?? null,
            'active_seconds'=>0,'started'=>null,'roster'=>$test ? [] : cl_roster($s->db, $class),'seconds'=>[], 'pulses'=>[], 'seen'=>[], 'staff'=>[], 'sample'=>0];
        if ($test) {
            $lesson['test'] = $test['id'];
            $lesson['test_students'] = $test['students'];
        }
    }
    $lesson['room'] = $cfg['room_prefix'] . '-v4-c' . $class['id'] . '-' . bin2hex(random_bytes(12));
    $lesson['phase'] = 'starting';
    $lesson['transport_at'] = $now;
    $lesson['seen'] = $lesson['staff'] = $lesson['pulses'] = [];
    $lesson['sample'] = 0;
    $lesson['last_attempt'] = 0;
    $lesson['event_received'] = false;
    // Record this generation before asking the SFU; a failed reply must not orphan it.
    ($ctx['write_runtime'])('scheduled-v2');
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
    return $lesson['phase'] === 'starting' && $lesson['room'] !== null
        && $now < min($lesson['transport_at'] + CL_START_SECONDS, $lesson['deadline'])
        && $lesson['date'] === cl_day($now)->format('Y-m-d');
}

function cl_joinable(array $lesson, array $actor, int $now): bool
{
    return cl_lesson_live($lesson, $now) || (cl_is_staff($actor) && cl_starting($lesson, $now));
}

function cl_ticket(array $cfg, ?array $lesson, array $actor, array $input, int $now): array
{
    if (!$lesson || !cl_can_lesson($lesson, $actor) || cl_lesson_expired($cfg, $lesson, $now)
        || !cl_match_transport($lesson, $input) || !cl_joinable($lesson, $actor, $now)) {
        throw new ClassroomError(409, 'در انتظار اتصال دبیر یا مسئول مجاز هستیم.');
    }
    $token = cl_join_ticket($cfg, $lesson['room'], $actor, min(35, $lesson['deadline'] - $now), $now);
    return ['url'=>$cfg['signal_url'], 'token'=>$token, 'lesson'=>$lesson['id'], 'epoch'=>cl_epoch($lesson)];
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

/** Sample the existing LiveKit service from the school request; never trust a browser's participant list. */
function cl_room_presence(array $lesson, int $now, callable $rpc): array
{
    $room = $lesson['room'];
    $participants = $rpc('ListParticipants', $room, ['room'=>$room])['participants'] ?? [];
    if (!is_array($participants) || !array_is_list($participants) || count($participants) > 60) throw cl_media_bad_reply();
    $people = [];
    foreach ($participants as $p) {
        if (!is_array($p)) throw cl_media_bad_reply();
        if (!in_array($p['state'] ?? null, ['ACTIVE', 2], true)) continue;
        $id = $p['identity'] ?? null;
        $sid = $p['sid'] ?? null;
        if (!cl_media_identity($id) || !cl_sid($sid) || isset($people[$id])) throw cl_media_bad_reply();
        if (!str_starts_with($id, 'student-')) {
            // Snapshots may lag behind a disconnected or replaced teacher. Only a matching active SID can supervise.
            $pulse = $lesson['pulses'][$id] ?? null;
            if (!$pulse || $pulse['sid'] !== $sid || $pulse['at'] < $now - CL_PULSE_TTL) continue;
            try { $real = $rpc('UpdateParticipant', $room, ['room'=>$room,'identity'=>$id]); }
            catch (ClassroomError $error) { if ($error->status === 409) continue; throw $error; }
            if (($real['sid'] ?? null) !== $sid || ($real['identity'] ?? null) !== $id || !in_array($real['state'] ?? null, ['ACTIVE',2], true)) continue;
        }
        $people[$id] = $sid;
    }
    return $people;
}

function cl_retire_transport(ClassroomStore $s, array $cfg, array $lesson, int $now, callable $rpc, bool $gone = false): ?array
{
    $lesson['last_attempt'] = $now;
    $s->put('lesson', $lesson['id'], $lesson); // Keep access closed even if the RPC is not reachable.
    if ($lesson['room'] !== null && !$gone) {
        if (cl_room_class($cfg, $lesson['room']) !== $lesson['class_id']) throw new LogicException('Invalid owned room record');
        cl_delete_room($lesson['room'], $rpc);
    }
    if ($lesson['phase'] === 'closing') {
        cl_finish_lesson($s, $cfg, $lesson, $now);
        return null;
    }
    $lesson['room'] = null;
    $s->put('lesson', $lesson['id'], $lesson);
    return $lesson;
}

// Called under the per-class lock. A single sample serves all polling tabs in that room.
function cl_reconcile(ClassroomStore $s, array $cfg, ?array $class, array $lesson, int $now, callable $rpc): ?array
{
    $test = cl_is_friday_test($lesson);
    $valid = $class !== null && $class['year'] === $lesson['year'] && !cl_lesson_expired($cfg, $lesson, $now);
    if ($valid && !$test) {
        $slot = cl_get_slot($s->db, $class, $lesson['slot'], $now);
        $valid = $slot && $slot['teacher'] === $lesson['teacher'] && $slot['subject'] === $lesson['subject']
            && cl_period_key($slot['period']) === cl_period_key($lesson['period']);
    }
    $closingNow = false;
    if (!$valid) {
        $closingNow = $lesson['phase'] !== 'closing';
        cl_pause($lesson, 'closing');
    }
    if ($lesson['phase'] === 'live' && !cl_lesson_live($lesson, $now)) cl_pause($lesson);
    if ($lesson['phase'] === 'starting' && !cl_starting($lesson, $now)) cl_pause($lesson);
    // The last presence sample must not delay the first attempt to close a bell at its deadline.
    if (!$closingNow && $now - ($lesson['last_attempt'] ?? 0) < CL_SAMPLE_INTERVAL) {
        $s->put('lesson', $lesson['id'], $lesson);
        return $lesson;
    }
    if ($lesson['phase'] === 'closing' || ($lesson['phase'] === 'paused' && $lesson['room'] !== null)) {
        return cl_retire_transport($s, $cfg, $lesson, $now, $rpc);
    }
    if ($lesson['room'] === null) return $lesson;
    if (empty($lesson['event_received'])) return $lesson;
    if (cl_room_class($cfg, $lesson['room']) !== $lesson['class_id']) throw new LogicException('Invalid owned room record');
    $lesson['last_attempt'] = $now;
    try { $observed = cl_room_presence($lesson, $now, $rpc); }
    catch (ClassroomError $error) {
        cl_pause($lesson);
        // A definitive absent room retires immediately; other errors are retried by the next page request.
        if ($error->status === 409) return cl_retire_transport($s, $cfg, $lesson, $now, $rpc, true);
        $s->put('lesson', $lesson['id'], $lesson);
        throw $error;
    }
    $students = $staff = [];
    foreach ($observed as $identity=>$sid) {
        [$role, $pk] = explode('-', $identity, 2);
        if ($role === 'student') { $students[] = (int)$pk; continue; }
        $account = cl_school_account($s->db, $role, cl_id($pk), $lesson['year']);
        if ($account && cl_can_lesson($lesson, $account)) $staff[$identity] = true;
    }
    // Only observed, active classmates count in pupil practice; never copy a school's roster into a demo.
    $roster = $test && ($lesson['test_students'] ?? false) !== true ? [] : cl_roster($s->db, $class, $students);
    if (count($observed) !== count($staff) + count($roster)) cl_pause($lesson);
    else {
        $lesson['roster'] += $roster;
        $lesson = cl_observe($lesson, $observed, $staff, $now, $now);
    }
    if ($lesson['phase'] === 'paused') return cl_retire_transport($s, $cfg, $lesson, $now, $rpc);
    $s->put('lesson', $lesson['id'], $lesson);
    return $lesson;
}

function cl_finish_lesson(ClassroomStore $s, array $cfg, array $lesson, int $now): void
{
    $s->tx(static function () use ($s, $cfg, $lesson, $now) {
        $id = $lesson['id'];
        $s->delete('active', (string)$lesson['class_id']);
        if (cl_is_friday_test($lesson) || $lesson['started'] === null) {
            $s->delete('lesson', $id);
        } else {
            $lesson['ended'] = min($now, $lesson['deadline']);
            foreach (['sample','transport_at','room','pulses','seen','staff','deadline','last_attempt','event_received'] as $key) unset($lesson[$key]);
            $lesson['phase'] = 'finished';
            $s->put('lesson', $id, $lesson);
            $key = sprintf('%012d:%s', $lesson['created'], $id);
            $entry = array_intersect_key($lesson, array_flip(['id','class_id','class_name','year','date','period','subject','teacher_name','active_seconds']));
            $s->put('reports', $key, $entry);
            $s->put('reports:teacher-' . $lesson['teacher'], $key, $entry);
            $providers = array_values(array_filter(['bale','rubika','telegram'], static fn($p) => ($cfg['notify_school_' . $p] ?? false) === true));
            if ($providers && $lesson['active_seconds'] > 0) $s->put('host-pending', $id, ['providers'=>$providers]);
        }
        cl_clear_activity($s, $id);
    });
    cl_clear_files($lesson['id']);
}

function cl_close_lesson(ClassroomStore $s, array $cfg, array $lesson, int $now, callable $rpc): void
{
    cl_pause($lesson, 'closing');
    cl_retire_transport($s, $cfg, $lesson, $now, $rpc);
}
