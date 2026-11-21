<?php
declare(strict_types=1);

use BaciClassroom\Calendar;

if (!class_exists(Calendar::class, false)) require_once __DIR__ . '/calendar.php';
require_once __DIR__ . '/core.php';

// The school owns these records. The plugin only reads them and never repairs the host's schema.
function cl_current_year(PDO $db): string
{
    $years = $defaults = [];
    $rows = cl_query($db, 'SELECT year_name,status,is_default FROM academic_years', []);
    while ($row = $rows->fetch(PDO::FETCH_ASSOC)) {
        $key = cl_year($row['year_name']);
        if ($key !== '') $years[$key][] = (int)$row['status'];
        if ((int)$row['is_default'] === 1) $defaults[] = $key;
    }
    if (count($defaults) <= 1) {
        // Baci's classes.php opens the school's current year first, not necessarily the default flag.
        // Never open a different year if that selection is inactive or ambiguous.
        $selected = cl_year((string)cl_query($db, 'SELECT key_value FROM settings WHERE key_name=?', ['current_academic_year'])->fetchColumn());
        if (!isset($years[$selected])) $selected = $defaults[0] ?? '';
        if (($years[$selected] ?? []) === [1]) return $selected;
    }
    throw new ClassroomError(409, 'در مدیریت دروس، یک سال تحصیلیِ فعال را به‌عنوان سال جاری مدرسه انتخاب کن.');
}

function cl_class_key(?string $s): string
{
    $s = strtr((string)$s, ['ي'=>'ی', 'ك'=>'ک','۰'=>'0','۱'=>'1','۲'=>'2','۳'=>'3','۴'=>'4','۵'=>'5','۶'=>'6','۷'=>'7','۸'=>'8','۹'=>'9',
        '٠'=>'0','١'=>'1','٢'=>'2','٣'=>'3','٤'=>'4','٥'=>'5','٦'=>'6','٧'=>'7','٨'=>'8','٩'=>'9']);
    return preg_replace('~[\s\p{Z}\x{200C}_/\-]+~u', '', trim($s)) ?? '';
}

function cl_classes(PDO $db, string $year): array
{
    $classes = $keys = [];
    $rows = cl_query($db, 'SELECT id,name,academic_year FROM classes ORDER BY id', []);
    while ($row = $rows->fetch(PDO::FETCH_ASSOC)) {
        $id = cl_id($row['id']);
        $key = cl_class_key($row['name']);
        if (!$id || $key === '' || cl_year($row['academic_year']) !== $year) continue;
        if (array_key_exists($key, $keys)) {
            unset($classes[$keys[$key]]);
            $keys[$key] = 0; // Refuse all ambiguous mappings; other classes remain usable.
            continue;
        }
        $keys[$key] = $id;
        $classes[$id] = ['id'=>$id, 'name'=>trim($row['name']), 'key'=>$key, 'year'=>$year];
    }
    return $classes;
}

/** These are labels only; keep the original school class key for access and reports. */
function cl_class_label(string $name): string
{
    $name = cl_safe_label($name, 100);
    $grades = ['اول'=>1, 'دوم'=>2, 'سوم'=>3, 'چهارم'=>4, 'پنجم'=>5, 'ششم'=>6,
        'هفتم'=>7, 'هشتم'=>8, 'نهم'=>9, 'دهم'=>10, 'یازدهم'=>11, 'دوازدهم'=>12];
    if (preg_match('~^([\p{L}]+)[\s\p{Z}\x{200C}_-]*([0-9۰-۹٠-٩]+)$~uD', $name, $m)) {
        $grade = $grades[cl_class_key($m[1])] ?? null;
        if ($grade !== null) return $grade . '/' . cl_class_key($m[2]);
    }
    if (preg_match('~^([0-9۰-۹٠-٩]{1,2})\s*/\s*([0-9۰-۹٠-٩]+)$~uD', $name, $m)) {
        return cl_class_key($m[1]) . '/' . cl_class_key($m[2]);
    }
    return $name;
}

function cl_school_name(PDO $db): string
{
    return cl_safe_label((string)cl_query($db, 'SELECT key_value FROM settings WHERE key_name=? LIMIT 1', ['school_name'])->fetchColumn(), 150);
}

function cl_day(int $now): DateTimeImmutable
{
    return Calendar::tehran($now);
}

function cl_tehran_clock(int $stamp, bool $withSeconds = false): string
{
    return Calendar::clock($stamp, $withSeconds);
}

function cl_tehran_midnight(int $now): int
{
    return cl_day($now)->modify('tomorrow')->getTimestamp();
}

/** Wall-clock labels, never elapsed time or the host/browser's default timezone. */
function cl_lesson_times(array $lesson): string
{
    $start = $lesson['started'] ?? null;
    $end = $lesson['ended'] ?? null;
    if (!is_int($start) || $start <= 0) return '';
    $label = cl_tehran_clock($start);
    if (is_int($end) && $end >= $start) {
        $label .= ' تا ' . cl_tehran_clock($end);
        if (cl_day($end)->format('Y-m-d') !== cl_day($start)->format('Y-m-d')) $label .= ' (روز بعد)';
    }
    return cl_persian_digits($label);
}

function cl_display_day(string $iso): string
{
    return Calendar::label($iso);
}

function cl_persian_digits(string $value): string
{
    return Calendar::digits($value);
}

const CL_DAYS = Calendar::WEEKDAYS;
const CL_FRIDAY_SLOT = 'friday-test';
const CL_FRIDAY_TITLE = 'آزمایش کلاس مجازی جمعه';
const CL_FRIDAY_HOURS = '۰۰:۰۰ تا پایان جمعه · وقت تهران';

/** A single opt-in civil Friday; settings never create weekly timetable rows. */
function cl_friday_window(array $cfg, int $now): ?array
{
    $plan = $cfg['friday_test'] ?? null;
    if (!is_array($plan) || !is_string($plan['id'] ?? null) || !preg_match('/^[a-f0-9]{32}$/D', $plan['id'])) return null;
    $day = cl_day($now);
    if ($day->format('w') !== '5' || $day->format('Y-m-d') !== ($plan['date'] ?? null)) return null;
    return ['id'=>$plan['id'], 'end'=>cl_tehran_midnight($now), 'label'=>CL_FRIDAY_HOURS,
        'students'=>($plan['students'] ?? false) === true];
}

function cl_schedule_day(string $value): ?int
{
    foreach (CL_DAYS as $n => $day) if (cl_class_key($value) === cl_class_key($day)) return $n;
    return null;
}

function cl_school_account(PDO $db, string $role, int $id, string $year): ?array
{
    if (!$id) return null;
    if ($role === 'student') {
        $row = cl_query($db, 'SELECT first_name,last_name,class_name,academic_year,status FROM students WHERE id=?', [$id])->fetch(PDO::FETCH_ASSOC);
        if (!$row || $row['status'] !== 'active' || cl_year($row['academic_year']) !== $year) return null;
        $name = trim($row['first_name'] . ' ' . $row['last_name']);
        $class = cl_class_key($row['class_name']);
    } elseif ($role === 'teacher') {
        $row = cl_query($db, 'SELECT * FROM teachers WHERE id=?', [$id])->fetch(PDO::FETCH_ASSOC);
        if (!$row || (int)$row['status'] !== 1) return null;
        $name = (string)$row['full_name'];
        // Native school_roles.php adds last_name to older schema snapshots. Do not infer it from full_name.
        $surname = cl_safe_label((string)($row['last_name'] ?? ''), 90);
        $roomName = $surname !== '' ? 'آقای ' . $surname : 'دبیر';
        // Baci v4.40+: teachers and their roles are global. class_schedules owns the year of each allocation.
        if ((int)($row['is_deputy'] ?? 0) === 1) $office = 'deputy';
    } elseif ($role === 'admin') {
        $row = cl_query($db, 'SELECT name,role,permissions,status FROM admins WHERE id=?', [$id])->fetch(PDO::FETCH_ASSOC);
        if (!$row || (int)$row['status'] !== 1) return null;
        $permissions = json_decode((string)($row['permissions'] ?? ''), true);
        if ($row['role'] !== 'super_admin' && (!is_array($permissions) || (!in_array('all', $permissions, true) && !in_array('manage_classes', $permissions, true)))) return null;
        $name = (string)$row['name'];
        $office = 'admin';
    } else {
        return null;
    }
    return ['id'=>$id, 'identity'=>$role . '-' . $id, 'name'=>cl_safe_label($name ?: $role, 90), 'role'=>$role,
        'class'=>$class ?? null, 'office'=>$office ?? null, 'year'=>$year] + (isset($roomName) ? ['room_name'=>$roomName] : []);
}

function cl_safe_label(string $name, int $limit): string
{
    $name = preg_replace('/[\x00-\x1f\x7f\x{202A}-\x{202E}\x{2066}-\x{2069}]+/u', ' ', $name) ?? '';
    preg_match('/^.{0,' . $limit . '}/us', trim($name), $m);
    return $m[0] ?? '';
}

function cl_school_user(PDO $db, array $session, string $year): array
{
    [$role, $id] = cl_session_identity($session);
    return cl_school_account($db, $role, $id, $year)
        ?? throw new ClassroomError(403, 'این حساب برای کلاس آنلاین مجوز فعال ندارد.');
}

function cl_is_staff(array $actor): bool
{
    return $actor['role'] !== 'student';
}

function cl_assigned(array $actor, int $teacher): bool
{
    return $actor['office'] !== null || ($actor['role'] === 'teacher' && $actor['id'] === $teacher);
}

function cl_class_for(PDO $db, array $classes, int $classId, array $actor, bool $friday = false): array
{
    $class = $classes[$classId] ?? null;
    if (!$class || !cl_access_class($db, $class, $actor, $friday)) throw new ClassroomError(403, 'این کلاس برای حساب تو در سال جاری مدرسه در دسترس نیست.');
    return $class;
}

function cl_access_class(PDO $db, array $class, array $actor, bool $friday = false): bool
{
    if ($actor['role'] === 'student') return $actor['class'] === $class['key'] && $actor['year'] === $class['year'];
    if ($friday || $actor['office']) return true; // The demo only widens visibility for authenticated staff.
    $rows = cl_query($db, 'SELECT class_name,academic_year FROM class_schedules WHERE teacher_id=?', [$actor['id']]);
    while ($slot = $rows->fetch(PDO::FETCH_ASSOC)) if (cl_slot_class($slot, $class)) return true;
    return false;
}

function cl_slot_class(array $slot, array $class): bool
{
    return cl_year($slot['academic_year']) === $class['year'] && cl_class_key($slot['class_name']) === $class['key'];
}

function cl_period_key(string $label): string
{
    return preg_match('/\A(?:زنگ)?0*([1-4])\z/u', cl_class_key($label), $parts) ? $parts[1] : '';
}

/** The weekly timetable maps the numbered bell to a class; its hours come from the plugin. */
function cl_bell_window(array $hours, string $period, int $now): ?array
{
    $pair = $hours[cl_period_key($period)] ?? null;
    if ($pair === null) return null;
    $day = cl_day($now);
    $stamp = static fn(string $time): int => $day->setTime((int)substr($time, 0, 2), (int)substr($time, 3, 2))->getTimestamp();
    return ['start'=>$stamp($pair['start']), 'end'=>$stamp($pair['end']), 'label'=>$pair['start'] . ' تا ' . $pair['end']];
}

function cl_slots_for_date(PDO $db, array $class, int $now): array
{
    $rows = cl_query($db, 'SELECT cs.id,cs.academic_year,cs.class_name,cs.day_of_week,cs.period_num,cs.subject_name,cs.teacher_id,
        t.id AS assigned_id,t.full_name AS assigned_name FROM class_schedules cs
        LEFT JOIN teachers t ON t.id=cs.teacher_id AND t.status=1 ORDER BY cs.id', []);
    $day = (int)cl_day($now)->format('w');
    $periods = [];
    while ($slot = $rows->fetch(PDO::FETCH_ASSOC)) {
        if (!cl_slot_class($slot, $class) || cl_schedule_day($slot['day_of_week']) !== $day) continue;
        $number = cl_period_key($slot['period_num']);
        if ($number !== '' && cl_id($slot['id'])) $periods[$number][] = $slot;
    }
    ksort($periods, SORT_NUMERIC);
    $slots = [];
    // classes.php groups its cells by the native class key and the stored year/day/period.
    $cell = static fn($row) => [$row['academic_year'], preg_replace('/[\s\-\/_]+/', '', trim($row['class_name'])), $row['day_of_week'], $row['period_num']];
    foreach ($periods as $group) {
        $shared = count($group) === 2;
        if (count($group) > 2 || ($shared && ($cell($group[0]) !== $cell($group[1])
            || [$group[0]['teacher_id'], trim($group[0]['subject_name'])] === [$group[1]['teacher_id'], trim($group[1]['subject_name'])]))) continue;
        // Baci stores both alternatives but no week parity. Keep both rows in the same bell;
        // require manual selection even if only one teacher is active/eligible for this viewer.
        foreach ($group as $row) {
            $id = cl_id($row['id']);
            $teacher = cl_id($row['assigned_id']);
            $subject = cl_safe_label($row['subject_name'], 100);
            if ($teacher && $subject !== '') $slots[$id] = ['id'=>$id, 'teacher'=>$teacher, 'subject'=>$subject,
                'teacher_name'=>cl_safe_label((string)$row['assigned_name'], 90), 'period'=>cl_safe_label($row['period_num'], 50), 'shared'=>$shared];
        }
    }
    return $slots;
}

function cl_today_slots(PDO $db, array $class, array $actor, int $now): array
{
    return array_filter(cl_slots_for_date($db, $class, $now), static fn($info) => cl_assigned($actor, $info['teacher']));
}

function cl_get_slot(PDO $db, array $class, int $slotId, int $now): ?array
{
    return cl_slots_for_date($db, $class, $now)[$slotId] ?? null;
}

function cl_roster_rows(PDO $db, array $class, ?array $studentIds = null): Generator
{
    if ($studentIds === []) return;
    $params = $studentIds ?? [];
    $where = $params ? ' AND id IN (' . implode(',', array_fill(0, count($params), '?')) . ')' : '';
    $rows = cl_query($db, 'SELECT id,first_name,last_name,class_name,academic_year FROM students WHERE status=?' . $where . ' ORDER BY last_name,first_name,id', ['active', ...$params]);
    while ($row = $rows->fetch(PDO::FETCH_ASSOC)) {
        if (cl_slot_class($row, $class) && ($id = cl_id($row['id']))) yield $id => $row;
    }
}

function cl_roster(PDO $db, array $class, ?array $studentIds = null): array
{
    $roster = [];
    foreach (cl_roster_rows($db, $class, $studentIds) as $id=>$row) {
        $roster['student-' . $id] = cl_safe_label($row['first_name'] . ' ' . $row['last_name'], 90);
    }
    return $roster;
}

function cl_photo_relative(mixed $value): ?string
{
    if (!is_string($value) || !preg_match('~^/?uploads/photos/([^/\\\\]{1,235}\.(?:jpe?g|png|webp))$~iuD', trim($value), $m)
        || preg_match('~[\p{C}?#%:]~u', $m[1])) return null;
    return 'uploads/photos/' . $m[1];
}

function cl_photo_paths(PDO $db, array $ids): array
{
    if (!$ids) return [];
    try {
        $params = implode(',', array_fill(0, count($ids), '?'));
        return cl_query($db, "SELECT id,photo_url FROM students WHERE id IN ($params)", $ids)->fetchAll(PDO::FETCH_KEY_PAIR);
    } catch (PDOException $error) { return []; } // Optional native photo field; its absence cannot interrupt a lesson.
}

/** Count alike surnames across the class, including absent pupils. Return only admitted pupils' cards. */
function cl_class_profiles(PDO $db, array $class, array $seen): array
{
    $counts = $present = [];
    if (!array_filter(array_keys($seen), static fn($id) => cl_media_identity($id, true))) return [];
    foreach (cl_roster_rows($db, $class) as $id=>$row) {
        $surname = cl_safe_label((string)$row['last_name'], 90);
        $key = strtolower(cl_class_key($surname));
        if ($key !== '') $counts[$key] = ($counts[$key] ?? 0) + 1;
        if (!isset($seen['student-' . $id])) continue;
        $given = cl_safe_label((string)$row['first_name'], 90);
        preg_match('/\p{L}\p{M}*/u', $given, $initial);
        $present[$id] = [$key, $surname, $initial[0] ?? ''];
    }
    $photos = cl_photo_paths($db, array_keys($present));
    $cards = [];
    foreach ($present as $id=>[$key,$surname,$initial]) {
        $name = ($counts[$key] ?? 0) > 1 && $initial !== '' ? $initial . "\u{200C}" . $surname : $surname;
        $cards['student-' . $id] = ['name'=>$name !== '' ? $name : 'دانش‌آموز', 'photo'=>cl_photo_relative($photos[$id] ?? null) !== null];
    }
    return $cards;
}

/** Use the school's local photo only; do not return its path (which may contain a national ID). */
function cl_read_photo(string $root, mixed $source): ?array
{
    $relative = cl_photo_relative($source);
    if ($relative === null || $root === '' || ($base = realpath($root)) === false) return null;
    $path = $base . '/' . $relative;
    if (realpath($path) !== $path || !is_file($path)) return null;
    $raw = @file_get_contents($path, false, null, 0, 2 * 1024 * 1024 + 1);
    if ($raw === false || strlen($raw) > 2 * 1024 * 1024) return null;
    $image = @getimagesizefromstring($raw);
    if (!$image || !in_array($image['mime'], ['image/jpeg','image/png','image/webp'], true) || $image[0] * $image[1] > 12_000_000) return null;
    return ['mime'=>$image['mime'], 'raw'=>$raw];
}
