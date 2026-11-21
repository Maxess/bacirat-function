<?php
declare(strict_types=1);

use BaciClassroom\Calendar;

require __DIR__ . '/../school-addon/upload/online-classroom/school.php';

function check(bool $valid, string $label): void
{
    if (!$valid) throw new RuntimeException($label);
}

// New Year, leap and common Esfand, and the Friday/Saturday/first day of Azar.
foreach ([
    '2024-03-19'=>'سه‌شنبه ۱۴۰۲/۱۲/۲۹', '2024-03-20'=>'چهارشنبه ۱۴۰۳/۰۱/۰۱',
    '2025-03-20'=>'پنجشنبه ۱۴۰۳/۱۲/۳۰', '2025-03-21'=>'جمعه ۱۴۰۴/۰۱/۰۱',
    '2026-03-20'=>'جمعه ۱۴۰۴/۱۲/۲۹', '2026-03-21'=>'شنبه ۱۴۰۵/۰۱/۰۱',
    '2026-11-18'=>'چهارشنبه ۱۴۰۵/۰۸/۲۷', '2026-11-20'=>'جمعه ۱۴۰۵/۰۸/۲۹',
    '2026-11-21'=>'شنبه ۱۴۰۵/۰۸/۳۰', '2026-11-22'=>'یک‌شنبه ۱۴۰۵/۰۹/۰۱',
] as $iso=>$expected) {
    check(Calendar::label($iso) === $expected, 'weekday and Shamsi civil day: ' . $iso);
    check(Calendar::fromShamsi(Calendar::shamsi($iso)) === $iso, 'round trip: ' . $iso);
}
check(Calendar::fromShamsi(' ۱۴۰۵/۸/۲۹ ') === '2026-11-20', 'Persian input');
check(Calendar::fromShamsi('١٤٠٥-٠٨-٢٩') === '2026-11-20', 'Arabic-Indic input');
check(Calendar::fromShamsi('1405/8/29') === '2026-11-20', 'ASCII input');
foreach (['۱۴۰۴/۱۲/۳۰','۱۴۰۳/۱۲/۳۱','۱۴۰۵/۰۸/۳۱','۱۴۰۵/۱۳/۰۱','۱۴۰۵/۰۰/۱۱','۱۴۰۵-08/29','2026-11-20','<date>',[]] as $invalid) {
    check(Calendar::fromShamsi($invalid) === null, 'bad date must not become an adjacent day');
}
check(Calendar::label('2026-02-30') === '—', 'historical invalid data never falls back to visible ISO');

$prior = date_default_timezone_get();
date_default_timezone_set('Pacific/Honolulu');
try {
    $friday = strtotime('2026-11-19T20:30:00Z');
    $saturday = $friday + 86400;
    check(cl_today_view($friday - 1) === ['day'=>'2026-11-19','dayLabel'=>'پنجشنبه ۱۴۰۵/۰۸/۲۸'], 'Tehran Thursday 23:59');
    check(cl_today_view($friday) === ['day'=>'2026-11-20','dayLabel'=>'جمعه ۱۴۰۵/۰۸/۲۹'], 'Tehran Friday, still Thursday in UTC');
    check(cl_today_view($saturday) === ['day'=>'2026-11-21','dayLabel'=>'شنبه ۱۴۰۵/۰۸/۳۰'], 'Iranian week begins Saturday');
    check(date_default_timezone_get() === 'Pacific/Honolulu', 'do not change the host PHP timezone');
    check(cl_schedule_day('پنج‌شنبه') === 4 && cl_schedule_day('يك شنبه') === 0 && cl_schedule_day('شنبه') === 6, 'native Persian weekday spellings');
} finally {
    date_default_timezone_set($prior);
}
echo "PASS Iranian weekdays, leap-day validation, Persian digits and timezone isolation\n";
