<?php
/**
 * export-school.php — produce the class server's school export from the
 * school management database.
 *
 * This script lives on the class server, NOT inside the management
 * platform: it only reads the database, it never writes to it and it
 * never modifies the platform's code.
 *
 * Usage:
 *   php export-school.php > /opt/bacirat/data/school-export.json
 *
 * Edit the settings below, or set them in the environment:
 *   DB_HOST, DB_NAME, DB_USER, DB_PASS, SCHOOL_YEAR
 *
 * Output:
 *   {"academicYear":"1404/1405","classes":[{"id":12,"name":"101 ریاضی",
 *    "grade":"دهم ریاضی","students":[{"nationalId":"...","firstName":"...",
 *    "lastName":"...","serial":"..."}],"teachers":[...]}]}
 */

declare(strict_types=1);

$host = getenv('DB_HOST') ?: '127.0.0.1';
$name = getenv('DB_NAME') ?: 'school';
$user = getenv('DB_USER') ?: 'root';
$pass = getenv('DB_PASS') ?: '';
$year = getenv('SCHOOL_YEAR') ?: '';   // empty = the system's default year

$dsn = "mysql:host={$host};dbname={$name};charset=utf8mb4";
$opts = [
    PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    PDO::ATTR_EMULATE_PREPARES   => false,
];

$pdo = new PDO($dsn, $user, $pass, $opts);

/**
 * The default academic year, the same way the management system works
 * it out: the setting first, then the master table.
 */
function default_year(PDO $pdo): string {
    $st = $pdo->prepare(
        'SELECT key_value FROM settings WHERE key_name = ? LIMIT 1'
    );
    $st->execute(['current_academic_year']);
    $y = (string)($st->fetchColumn() ?: '');
    if ($y === '') {
        $st = $pdo->query(
            'SELECT year_name FROM academic_years
             WHERE is_default = 1 AND status = 1 LIMIT 1'
        );
        $y = (string)($st->fetchColumn() ?: '');
    }
    return $y;
}

/**
 * The six-digit birth certificate serial.  Mirrors the management
 * system's own extract_certificate_serial().
 */
function cert_serial(string $raw): string {
    // Normalise Persian/Arabic digits first.
    $raw = strtr($raw, ['۰'=>'0','۱'=>'1','۲'=>'2','۳'=>'3','۴'=>'4',
                        '۵'=>'5','۶'=>'6','۷'=>'7','۸'=>'8','۹'=>'9',
                        '٠'=>'0','١'=>'1','٢'=>'2','٣'=>'3','٤'=>'4',
                        '٥'=>'5','٦'=>'6','٧'=>'7','٨'=>'8','٩'=>'9']);
    if (preg_match('/(\d{6})/', $raw, $m)) {
        return $m[1];
    }
    $digits = preg_replace('/\D/', '', $raw);
    if (strlen($digits) >= 6) {
        return substr($digits, -6);
    }
    return str_pad($digits, 6, '0', STR_PAD_LEFT);
}

if ($year === '') {
    $year = default_year($pdo);
}

// Classes of the default academic year only.
$classesStmt = $pdo->prepare(
    'SELECT id, name, grade, academic_year
       FROM classes
      WHERE academic_year = ?
      ORDER BY name'
);
$classesStmt->execute([$year]);
$classes = $classesStmt->fetchAll();

// Students of a class.  academic_year may be NULL for rows imported
// before the column existed, so those count as belonging to every year.
$studentsStmt = $pdo->prepare(
    'SELECT national_id, first_name, last_name, serial_number
       FROM students
      WHERE class_name = ?
        AND status = "active"
        AND (academic_year = ? OR academic_year IS NULL OR academic_year = "")
      ORDER BY last_name, first_name'
);

// Teachers assigned to a class, taken from the weekly schedule.
$teachersStmt = $pdo->prepare(
    'SELECT DISTINCT t.national_id, t.full_name, t.personnel_code
       FROM class_schedules cs
       JOIN teachers t ON t.id = cs.teacher_id
      WHERE cs.academic_year = ?
        AND cs.class_name = ?
        AND t.status = 1'
);

$out = [
    'academicYear' => $year,
    'classes'      => [],
];

foreach ($classes as $c) {
    $studentsStmt->execute([$c['name'], $year]);
    $students = [];
    foreach ($studentsStmt->fetchAll() as $s) {
        $nid = trim((string)$s['national_id']);
        if ($nid === '') {
            continue;
        }
        $serial = cert_serial((string)($s['serial_number'] ?? ''));
        $students[] = [
            'nationalId' => $nid,
            'firstName'  => (string)$s['first_name'],
            'lastName'   => (string)$s['last_name'],
            // Fall back to the national id, exactly as the management
            // system does when a serial is missing.
            'serial'     => $serial !== '' ? $serial : $nid,
        ];
    }

    $teachersStmt->execute([$year, $c['name']]);
    $teachers = [];
    foreach ($teachersStmt->fetchAll() as $t) {
        $nid = trim((string)$t['national_id']);
        if ($nid === '') {
            continue;
        }
        // full_name is a single field: split off the last word as the
        // family name, leaving the rest as the given name.
        $parts  = preg_split('/\s+/u', trim((string)$t['full_name'])) ?: [];
        $last   = count($parts) > 1 ? array_pop($parts) : '';
        $first  = implode(' ', $parts);
        $teachers[] = [
            'nationalId'    => $nid,
            'firstName'     => $first,
            'lastName'      => $last,
            'personnelCode' => trim((string)($t['personnel_code'] ?? '')),
        ];
    }

    $out['classes'][] = [
        'id'       => (int)$c['id'],
        'name'     => (string)$c['name'],
        'grade'    => (string)$c['grade'],
        'students' => $students,
        'teachers' => $teachers,
    ];
}

echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), "\n";
