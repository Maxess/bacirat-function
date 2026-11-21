<?php
declare(strict_types=1);
require_once __DIR__ . '/school.php';
require_once __DIR__ . '/store.php';

const CL_SAMPLE_TTL = 11;
const CL_SAMPLE_INTERVAL = 3;
const CL_PULSE_TTL = 12;
const CL_START_SECONDS = 32;
const CL_MAX_LESSON_SECONDS = 7200;
const CL_REPEAT_PREFIX = 'repeat-';

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
    return !cl_is_staff($actor) || cl_assigned($actor, $lesson['teacher']);
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

function cl_lesson_choice(array $lesson): int|string
{
    return isset($lesson['repeat_of']) ? CL_REPEAT_PREFIX . $lesson['repeat_of'] : $lesson['slot'];
}

/** Repeat only the actual, still-assigned, original bell, never an ordinary empty or demo room. */
function cl_repeatable(array $cfg, ?array $prior, array $slot, int $now): bool
{
    return ($cfg['repeat_bells'] ?? false) === true && ($prior['phase'] ?? null) === 'finished'
        && !isset($prior['repeat_of']) && !cl_is_friday_test($prior) && isset($prior['started'], $prior['ended'])
        && $prior['ended'] <= $now && cl_matches_slot($prior, $slot);
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
        $label = $slot['period'] . ' · ' . $slot['subject'] . ($slot['shared'] ? ' · ' . $slot['teacher_name'] . ' · تک‌زنگ' : '');
        $base = cl_lesson_id($class, $slot['period'], $date);
        $previous = $s->get('lesson', $base);
        if (($previous['phase'] ?? null) === 'finished') {
            if (cl_repeatable($cfg, $previous, $slot, $now)) {
                $choices[] = ['id'=>CL_REPEAT_PREFIX . $base, 'label'=>'برگزاری مجدد · ' . $label, 'manual'=>true];
            }
            continue;
        }
        $window = cl_bell_window($hours, $slot['period'], $now);
        if ($hours && (!$window || $now >= $window['end'])) continue;
        $when = $window ? ' · ' . $window['label'] : '';
        $label .= $when;
        if ($window && $now < $window['start']) {
            if ($window['start'] < $soonest) {
                $soonest = $window['start'];
                $next = $slot['shared'] ? $slot['period'] . ' · تک‌زنگ (انتخاب دستی)' . $when : $label;
            }
        } else $choices[] = ['id'=>$slot['id'], 'label'=>$label, 'manual'=>$slot['shared']];
    }
    return ['date'=>$date, 'timed'=>$hours !== [], 'choices'=>$choices, 'next'=>$next];
}

function cl_matches_slot(array $lesson, ?array $slot): bool
{
    return $slot !== null && $lesson['slot'] === $slot['id'] && $lesson['teacher'] === $slot['teacher']
        && $lesson['subject'] === $slot['subject'] && cl_period_key($lesson['period']) === cl_period_key($slot['period']);
}

function cl_open_lesson(ClassroomStore $s, array $ctx, array $cfg, array $class, array $actor, array $input, int $now, callable $rpc): array
{
    if (!cl_is_staff($actor)) throw new ClassroomError(403, 'شروع کلاس فقط برای دبیر یا مسئول مجاز است.');
    $lesson = cl_active($s, $class['id']);
    $date = cl_day($now)->format('Y-m-d');
    $test = $window = $repeatOf = null;
    $choice = $input['slot'] ?? null;
    if ($choice === CL_FRIDAY_SLOT) {
        $test = $window = cl_friday_window($cfg, $now);
        if (!$test) throw new ClassroomError(403, 'آزمایش کلاس مجازی فقط در جمعهٔ تعیین‌شده باز است.');
        $id = ($lesson['test'] ?? null) === $test['id'] ? $lesson['id'] : bin2hex(random_bytes(16));
        $slot = ['id'=>CL_FRIDAY_SLOT,'period'=>'آزمایشی','subject'=>CL_FRIDAY_TITLE,'teacher'=>$actor['role'] === 'teacher' ? $actor['id'] : 0];
        $teacherName = $actor['name'];
    } else {
        $original = null;
        if (is_string($choice) && preg_match('/\A' . CL_REPEAT_PREFIX . '([a-f0-9]{32})\z/', $choice, $m)) {
            $repeatOf = $m[1];
            $original = $s->get('lesson', $repeatOf);
            if (($original['class_id'] ?? null) !== $class['id'] || $original['year'] !== $class['year'] || $original['date'] !== $date) {
                throw new ClassroomError(409, 'فقط زنگ پایان‌یافتهٔ همین کلاس در همین روز به وقت تهران قابل بازبرگزاری است.');
            }
        }
        $slotId = cl_id($repeatOf === null ? $choice : $original['slot']);
        $slot = $slotId ? cl_get_slot($s->db, $class, $slotId, $now) : null;
        if (!$slot || !cl_assigned($actor, $slot['teacher'])) {
            throw new ClassroomError(403, 'برای شروع، زنگ همین کلاس از برنامهٔ امروز با دبیر فعال و مجاز انتخاب کن.');
        }
        $teacherName = $slot['teacher_name'];
        $id = cl_lesson_id($class, $slot['period'], $date);
        if ($repeatOf !== null) {
            if ($id !== $repeatOf || !cl_repeatable($cfg, $original, $slot, $now)) {
                throw new ClassroomError(409, 'برگزاری مجدد این زنگ در تنظیمات یا برنامهٔ امروز مجاز نیست.');
            }
            // Keep the original report and bell key. Resume only the active repetition of this exact bell.
            $id = ($lesson['repeat_of'] ?? null) === $repeatOf ? $lesson['id'] : bin2hex(random_bytes(16));
        }
    }
    if ($lesson && ($lesson['id'] !== $id || $lesson['year'] !== $class['year'] || (!$test && !cl_matches_slot($lesson, $slot))
        || $lesson['phase'] === 'closing' || cl_lesson_expired($cfg, $lesson, $now))) {
        throw new ClassroomError(409, 'جلسهٔ دیگری برای این کلاس باز یا در حال بسته‌شدن است.');
    }
    if ($lesson && $lesson['room'] !== null) {
        if (in_array($lesson['phase'], ['starting','live'], true)) return $lesson;
        throw new ClassroomError(409, 'در حال بستن اتصال قبلی کلاس؛ چند لحظه صبر کن.');
    }
    if (!$lesson) {
        if ($repeatOf !== null) {
            $regular = cl_bell_window($cfg['bell_hours'] ?? [], $slot['period'], $now);
            $length = $regular ? min(CL_MAX_LESSON_SECONDS, $regular['end'] - $regular['start']) : CL_MAX_LESSON_SECONDS;
            $end = min($now + $length, cl_tehran_midnight($now));
            $window = ['end'=>$end, 'label'=>'بازبرگزاری · ' . cl_tehran_clock($now) . ' تا ' . cl_tehran_clock($end)];
        } elseif (!$test) {
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
            'created'=>$now,'deadline'=>$window['end'] ?? min($now + CL_MAX_LESSON_SECONDS, cl_tehran_midnight($now)), 'bell'=>$window['label'] ?? null,
            'active_seconds'=>0,'started'=>null,'roster'=>$test ? [] : cl_roster($s->db, $class),'seconds'=>[], 'pulses'=>[], 'seen'=>[], 'staff'=>[], 'sample'=>0];
        if ($repeatOf !== null) $lesson['repeat_of'] = $repeatOf;
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
    unset($lesson['camera_codec'], $lesson['media_pending'], $lesson['screen'], $lesson['screen_used']);
    // Record this generation before asking the SFU; a failed reply must not orphan it.
    ($ctx['write_runtime'])('scheduled-v2');
    $s->tx(static function () use ($s, $class, $lesson, $id) {
        $s->put('lesson', $id, $lesson);
        $s->put('active', (string)$class['id'], ['id'=>$id]);
    });
    try {
        $lesson['camera_codec'] = cl_camera_codec(cl_create_room($lesson['room'], $cfg['max_participants'], $rpc));
        $s->put('lesson', $id, $lesson);
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
    $lesson['seen'] = $lesson['staff'] = $lesson['pulses'] = $lesson['profiles'] = [];
    unset($lesson['screen']);
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

/** Only for an admitted status response. A validator is a compression hint, never proof of admission. */
function cl_status_people(array $lesson, array $input): array
{
    $profiles = array_intersect_key($lesson['profiles'] ?? [], $lesson['seen']);
    if (($input['compact'] ?? null) !== true) return ['profiles'=>$profiles,'seconds'=>$lesson['seconds']];
    $rev = substr(hash('sha256', json_encode($profiles, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE)), 0, 24);
    $result = ['profilesRev'=>$rev];
    if (($input['profilesRev'] ?? null) !== $rev) $result['profiles'] = $profiles;
    if (($input['people'] ?? null) === true) $result['seconds'] = array_intersect_key($lesson['seconds'], $lesson['seen']);
    return $result;
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
    // Timestamp the first verified sighting, not the ticket, wait, or the first credited interval.
    if (!cl_is_friday_test($lesson)) {
        foreach (array_intersect_key($seen, $lesson['roster']) as $who=>$sid) {
            $lesson['first_seen'][$who] ??= $at;
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
function cl_room_presence(array $lesson, int $now, callable $rpc, array &$peers): array
{
    $room = $lesson['room'];
    $peers = cl_live_media_peers($rpc('ListParticipants', $room, ['room'=>$room]));
    $people = [];
    foreach ($peers as $id=>$p) {
        $sid = $p['sid'];
        if (!str_starts_with($id, 'student-')) {
            // Snapshots may lag behind a disconnected or replaced teacher. Only a matching active SID can supervise.
            $pulse = $lesson['pulses'][$id] ?? null;
            if (!$pulse || $pulse['sid'] !== $sid || $pulse['at'] < $now - CL_PULSE_TTL) continue;
            try { $real = $rpc('UpdateParticipant', $room, ['room'=>$room,'identity'=>$id]); }
            catch (ClassroomError $error) { if ($error->status === 409) continue; throw $error; }
            if (($real['sid'] ?? null) !== $sid || ($real['identity'] ?? null) !== $id || !in_array($real['state'] ?? null, ['ACTIVE',2], true)) continue;
            $peers[$id] = $real;
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
    if ($valid && !$test) $valid = cl_matches_slot($lesson, cl_get_slot($s->db, $class, $lesson['slot'], $now));
    $closingNow = false;
    if (!$valid) {
        $closingNow = $lesson['phase'] !== 'closing';
        cl_pause($lesson, 'closing');
    }
    $priorPhase = $lesson['phase'];
    if ($lesson['phase'] === 'live' && !cl_lesson_live($lesson, $now)) cl_pause($lesson);
    if ($lesson['phase'] === 'starting' && !cl_starting($lesson, $now)) cl_pause($lesson);
    // The last presence sample must not delay the first attempt to close a bell at its deadline.
    if (!$closingNow && $now - ($lesson['last_attempt'] ?? 0) < CL_SAMPLE_INTERVAL) {
        if ($priorPhase !== $lesson['phase']) $s->put('lesson', $lesson['id'], $lesson); // cl_touch already saved the pulse.
        return $lesson;
    }
    if ($lesson['phase'] === 'closing' || ($lesson['phase'] === 'paused' && $lesson['room'] !== null)) {
        return cl_retire_transport($s, $cfg, $lesson, $now, $rpc);
    }
    if ($lesson['room'] === null) return $lesson;
    if (empty($lesson['event_received'])) return $lesson;
    if (cl_room_class($cfg, $lesson['room']) !== $lesson['class_id']) throw new LogicException('Invalid owned room record');
    $lesson['last_attempt'] = $now;
    $peers = [];
    try { $observed = cl_room_presence($lesson, $now, $rpc, $peers); }
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
        $previous = $lesson['seen'];
        $lesson = cl_observe($lesson, $observed, $staff, $now, $now);
        if ($lesson['phase'] === 'live') {
            if ($previous !== $lesson['seen'] || ($lesson['profiles_at'] ?? 0) + 60 <= $now) {
                $lesson['profiles'] = cl_class_profiles($s->db, $class, $lesson['seen']);
                $lesson['profiles_at'] = $now;
            }
            // Do not stop attendance because an individual device's media policy is still catching up.
            cl_refresh_screen($lesson, $cfg, $peers, $now);
            $lesson['media_pending'] = !cl_sync_media($cfg, $lesson, $peers, $rpc);
        }
    }
    if ($lesson['phase'] === 'paused') return cl_retire_transport($s, $cfg, $lesson, $now, $rpc);
    $s->put('lesson', $lesson['id'], $lesson);
    return $lesson;
}

/** Under the same class lock as presence, page changes and ink writes. */
function cl_set_access(ClassroomStore $s, array $cfg, array $lesson, array $actor, array $body, int $now, callable $rpc): array
{
    if (!cl_is_staff($actor)) throw new ClassroomError(403, 'تغییر دسترسی فقط برای دبیر یا مسئول مجاز است.');
    $feature = $body['feature'] ?? null;
    $enabled = $body['enabled'] ?? null;
    $identity = $body['identity'] ?? null; // null/omitted is the whole class in this lesson only.
    if (!in_array($feature, ['mic','camera','board'], true) || !is_bool($enabled)
        || ($identity !== null && !cl_media_identity($identity, $feature === 'board'))) {
        throw new ClassroomError(400, 'نوع دسترسی یا شرکت‌کننده معتبر نیست.');
    }
    $sid = $identity === null ? null : ($lesson['seen'][$identity] ?? null);
    if ($identity !== null && !cl_entered($lesson, ['identity'=>$identity], $sid, $now)) {
        throw new ClassroomError(409, 'ورود این شرکت‌کننده هنوز تأیید نشده است.');
    }
    if ($feature === 'camera' && $enabled && cl_webcam_codec($cfg, $lesson) === null) {
        throw new ClassroomError(409, ($cfg['camera_share'] ?? false) === true
            ? 'سرور رسانهٔ این اتاق از تصویر دوربین پشتیبانی نمی‌کند.' : 'وبکم در تنظیمات افزونه فعال نیست.');
    }
    if ($identity === null) $lesson['access'][$feature] = ['all'=>$enabled, 'members'=>[]];
    else {
        $rule = $lesson['access'][$feature] ?? ['all'=>false, 'members'=>[]];
        if ($enabled === $rule['all']) unset($rule['members'][$identity]);
        else $rule['members'][$identity] = $enabled;
        $lesson['access'][$feature] = $rule;
    }
    // Commit the intended revocation before touching the SFU so a retry cannot restore the old grant.
    $s->put('lesson', $lesson['id'], $lesson);
    if ($feature !== 'board') {
        try {
            $room = $lesson['room'];
            if ($identity === null) $peers = cl_live_media_peers($rpc('ListParticipants', $room, ['room'=>$room]));
            else {
                // Confirm that the live SFU identity has not already reconnected with a different SID.
                $peer = $rpc('UpdateParticipant', $room, ['room'=>$room,'identity'=>$identity]);
                $peers = cl_live_media_peers(['participants'=>[$peer]]);
                if (($peers[$identity]['sid'] ?? null) !== $sid) $peers = [];
            }
            $verified = cl_sync_media($cfg, $lesson, $peers, $rpc);
        } catch (ClassroomError $error) { $verified = false; }
        if (!$verified) {
            $lesson['media_pending'] = true;
            $s->put('lesson', $lesson['id'], $lesson);
            throw cl_media_bad_reply();
        }
    }
    return ['access'=>$lesson['access']];
}

const CL_WAKE_MODES = ['live', 'idle', 'system'];

function cl_wake_mode(array $lesson): string
{
    $mode = $lesson['wake_mode'] ?? null;
    return in_array($mode, CL_WAKE_MODES, true) ? $mode : 'live';
}

function cl_wake_control(array $lesson, array $actor): bool
{
    // This is a teaching choice for this bell, not a per-device preference or a global admin setting.
    return $actor['role'] === 'teacher' && $actor['id'] === ($lesson['teacher'] ?? 0);
}

function cl_set_wake(ClassroomStore $s, array $lesson, array $actor, array $body): array
{
    if (!cl_wake_control($lesson, $actor)) throw new ClassroomError(403, 'این تنظیم فقط در اختیار دبیر همین زنگ است.');
    $mode = $body['mode'] ?? null;
    if (!in_array($mode, CL_WAKE_MODES, true)) throw new ClassroomError(400, 'حالت روشن‌ماندن صفحه معتبر نیست.');
    $sid = $body['sid'] ?? null;
    if (!cl_match_transport($lesson, $body) || !cl_sid($sid) || ($lesson['staff'][$actor['identity']] ?? null) !== $sid) {
        throw new ClassroomError(409, 'اتصال دبیر عوض شده است؛ دوباره وارد کلاس شو.');
    }
    if ($mode !== cl_wake_mode($lesson)) {
        $lesson['wake_mode'] = $mode;
        $s->put('lesson', $lesson['id'], $lesson);
    }
    return ['wakeMode'=>$mode];
}

const CL_SCREEN_LEASE = 40;

function cl_screen_enabled(array $cfg, array $lesson): bool
{
    return ($cfg['screen_share'] ?? false) === true && ($lesson['camera_codec'] ?? null) === 'vp8';
}

function cl_screen_state(array $lesson): ?array
{
    $owner = $lesson['screen'] ?? null;
    return $owner && ($lesson['staff'][$owner['identity']] ?? null) === $owner['sid']
        ? ['identity'=>$owner['identity'],'sid'=>$owner['sid']] : null;
}

function cl_refresh_screen(array &$lesson, array $cfg, array $peers, int $now): void
{
    $owner = $lesson['screen'] ?? null;
    if (!$owner) return;
    $id = $owner['identity'];
    $peer = $peers[$id] ?? [];
    if (!cl_screen_enabled($cfg, $lesson) || ($lesson['staff'][$id] ?? null) !== $owner['sid'] || ($peer['sid'] ?? null) !== $owner['sid']) {
        unset($lesson['screen']);
        return;
    }
    $tracks = $peer['tracks'] ?? [];
    foreach (is_array($tracks) ? $tracks : [] as $track) {
        if (is_array($track) && cl_track_source($track['source'] ?? null) === 'SCREEN_SHARE') {
            $lesson['screen']['until'] = $now + CL_SCREEN_LEASE;
            return;
        }
    }
    if ($owner['until'] <= $now) unset($lesson['screen']);
}

/** One presenter per transport. Revoke an old source before assigning a new one. */
function cl_set_screen(ClassroomStore $s, array $cfg, array $lesson, array $actor, array $body, int $now, callable $rpc): array
{
    if (!cl_is_staff($actor)) throw new ClassroomError(403, 'اشتراک صفحه فقط برای دبیر یا مسئول مجاز است.');
    $enable = $body['enabled'] ?? null;
    if (!is_bool($enable)) throw new ClassroomError(400, 'وضعیت اشتراک صفحه معتبر نیست.');
    if ($enable && !cl_screen_enabled($cfg, $lesson)) throw new ClassroomError(409, 'آزمایش اشتراک صفحه در این اتاق فعال نیست.');
    $id = $actor['identity'];
    $sid = $body['sid'] ?? null;
    if (!cl_sid($sid) || ($lesson['staff'][$id] ?? null) !== $sid) throw new ClassroomError(409, 'ورود ارائه‌دهنده تأیید نشده است.');
    $room = $lesson['room'];
    $owner = cl_screen_state($lesson);
    $mine = ['identity'=>$id,'sid'=>$sid];
    if ($owner && $owner !== $mine) throw new ClassroomError(409, 'اشتراک صفحه در اختیار ارائه‌دهندهٔ دیگری است.');
    if (!$enable && !$owner) return ['screen'=>null];
    if (!$owner) unset($lesson['screen']);
    $lesson['screen_used'] = true;
    if (!$enable) {
        unset($lesson['screen']);
        // Persist denial before calling the SFU; its next presence sample can retry a failed release.
        $s->put('lesson', $lesson['id'], $lesson);
    }
    $peers = cl_live_media_peers($rpc('ListParticipants', $room, ['room'=>$room]));
    if ($enable) {
        if (($peers[$id]['sid'] ?? null) !== $sid) throw new ClassroomError(409, 'اتصال ارائه‌دهنده عوض شده است.');
        if (!$owner && !cl_sync_media($cfg, $lesson, $peers, $rpc, true)) throw cl_media_bad_reply();
        $lesson['screen'] = $mine + ['until'=>$now + CL_SCREEN_LEASE];
        $s->put('lesson', $lesson['id'], $lesson);
        $confirmed = cl_sync_media($cfg, $lesson, [$id=>$peers[$id]], $rpc, true);
        if (!$confirmed) unset($lesson['screen']);
    } else $confirmed = cl_sync_media($cfg, $lesson, $peers, $rpc, true);
    if (!$confirmed) $lesson['media_pending'] = true;
    $s->put('lesson', $lesson['id'], $lesson);
    if (!$confirmed) throw cl_media_bad_reply();
    return ['screen'=>cl_screen_state($lesson)];
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
            foreach (['sample','transport_at','room','pulses','seen','staff','deadline','last_attempt','event_received','profiles','profiles_at','access','camera_codec','media_pending','screen','screen_used','wake_mode'] as $key) unset($lesson[$key]);
            $lesson['phase'] = 'finished';
            $s->put('lesson', $id, $lesson);
            $key = sprintf('%012d:%s', $lesson['created'], $id);
            $entry = array_intersect_key($lesson, array_flip(['id','class_id','class_name','year','date','period','subject','teacher_name','active_seconds','started','ended','repeat_of']));
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
