<?php
declare(strict_types=1);
require_once __DIR__ . '/core.php';

// The school owns these records. The plugin only reads them and never repairs the host's schema.
function cl_current_year(PDO $db): string
{
    $defaults = cl_query($db, 'SELECT year_name, status FROM academic_years WHERE is_default = 1 LIMIT 2', [])->fetchAll(PDO::FETCH_ASSOC);
    if (count($defaults) === 1 && (int)$defaults[0]['status'] === 1 && ($year = cl_year($defaults[0]['year_name'])) !== '') return $year;
    if ($defaults === []) {
        // Some host imports set the explicit current-year setting before setting a master-table flag. Never guess a year from date or class rows.
        $selected = cl_year((string)cl_query($db, 'SELECT key_value FROM settings WHERE key_name=?', ['current_academic_year'])->fetchColumn());
        if ($selected !== '') {
            $known = cl_query($db, 'SELECT year_name FROM academic_years WHERE status=1', [])->fetchAll(PDO::FETCH_COLUMN);
            if (count(array_filter($known, static fn($year) => cl_year($year) === $selected)) === 1) return $selected;
        }
    }
    throw new ClassroomError(409, 'در تنظیمات مدرسه یک سال تحصیلیِ فعال را به‌عنوان پیش‌فرض انتخاب کن.');
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

function cl_day(int $now): DateTimeImmutable
{
    return (new DateTimeImmutable('@' . $now))->setTimezone(new DateTimeZone('Asia/Tehran'));
}

const CL_DAYS = ['یک‌شنبه','دوشنبه','سه‌شنبه','چهارشنبه','پنجشنبه','جمعه','شنبه'];

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
        if ((int)($row['is_deputy'] ?? 0) === 1 && cl_year($row['academic_year'] ?? '') === $year) $office = 'deputy';
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
        'class'=>$class ?? null, 'office'=>$office ?? null, 'year'=>cl_year($row['academic_year'] ?? '')];
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

function cl_assigned(array $actor, string $year, int $teacher): bool
{
    return $actor['office'] !== null || ($actor['role'] === 'teacher' && $actor['id'] === $teacher && $actor['year'] === $year);
}

function cl_class_for(PDO $db, array $classes, int $classId, array $actor): array
{
    $class = $classes[$classId] ?? null;
    if (!$class || !cl_access_class($db, $class, $actor)) throw new ClassroomError(403, 'این کلاس برای حساب تو در سال پیش‌فرض در دسترس نیست.');
    return $class;
}

function cl_access_class(PDO $db, array $class, array $actor): bool
{
    if ($actor['role'] === 'student') return $actor['class'] === $class['key'] && $actor['year'] === $class['year'];
    if ($actor['office']) return true;
    if ($actor['year'] !== $class['year']) return false;
    $rows = cl_query($db, 'SELECT class_name,academic_year FROM class_schedules WHERE teacher_id=?', [$actor['id']]);
    while ($slot = $rows->fetch(PDO::FETCH_ASSOC)) if (cl_slot_class($slot, $class)) return true;
    return false;
}

function cl_slot_class(array $slot, array $class): bool
{
    return cl_year($slot['academic_year']) === $class['year'] && cl_class_key($slot['class_name']) === $class['key'];
}

function cl_slot_info(array $slot): array
{
    return ['id'=>cl_id($slot['id']), 'teacher'=>cl_id($slot['teacher_id']), 'day'=>cl_schedule_day((string)$slot['day_of_week']),
        'period'=>cl_safe_label((string)$slot['period_num'], 50), 'subject'=>cl_safe_label((string)$slot['subject_name'], 100)];
}

function cl_period_key(string $label): string
{
    return preg_replace('/^(?:زنگ)?0*([1-9][0-9]*)$/uD', '$1', cl_class_key($label)) ?? '';
}

function cl_slots_for_date(PDO $db, array $class, int $now): array
{
    $rows = cl_query($db, 'SELECT id,academic_year,class_name,day_of_week,period_num,subject_name,teacher_id FROM class_schedules ORDER BY period_num,id', []);
    $day = (int)cl_day($now)->format('w');
    $slots = $periods = [];
    while ($slot = $rows->fetch(PDO::FETCH_ASSOC)) {
        if (!cl_slot_class($slot, $class)) continue;
        $info = cl_slot_info($slot);
        $label = cl_period_key($info['period']);
        if (!$info['id'] || $info['day'] !== $day || $label === '') continue;
        if (isset($periods[$label])) {
            unset($slots[$periods[$label]]); // Conflicting rows do not define two separate occurrences of a bell.
            continue;
        }
        $periods[$label] = $info['id'];
        if ($info['teacher'] && $info['subject'] !== '') $slots[$info['id']] = $info;
    }
    return $slots;
}

function cl_today_slots(PDO $db, array $class, array $actor, int $now): array
{
    return array_filter(cl_slots_for_date($db, $class, $now), static fn($info) => cl_assigned($actor, $class['year'], $info['teacher']));
}

function cl_get_slot(PDO $db, array $class, int $slotId, int $now): ?array
{
    return cl_slots_for_date($db, $class, $now)[$slotId] ?? null;
}

function cl_roster(PDO $db, array $class, ?array $studentIds = null): array
{
    if ($studentIds === []) return [];
    $params = $studentIds ?? [];
    $where = $params ? ' AND id IN (' . implode(',', array_fill(0, count($params), '?')) . ')' : '';
    $rows = cl_query($db, 'SELECT id,first_name,last_name,class_name,academic_year FROM students WHERE status=?' . $where . ' ORDER BY last_name,first_name,id', ['active', ...$params]);
    $roster = [];
    while ($row = $rows->fetch(PDO::FETCH_ASSOC)) {
        if (cl_slot_class($row, $class) && ($id = cl_id($row['id']))) {
            $roster['student-' . $id] = cl_safe_label($row['first_name'] . ' ' . $row['last_name'], 90);
        }
    }
    return $roster;
}
