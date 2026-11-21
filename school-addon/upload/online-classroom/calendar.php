<?php
declare(strict_types=1);

namespace BaciClassroom;

use DateTimeImmutable;
use DateTimeZone;

/**
 * Iranian civil dates for the classroom and its manager, independent of the
 * browser, PHP's default timezone, and whether Baci's header was loaded.
 * Keep existing ISO dates as keys; convert only at the human-facing boundary.
 */
final class Calendar
{
    public const WEEKDAYS = ['یک‌شنبه','دوشنبه','سه‌شنبه','چهارشنبه','پنجشنبه','جمعه','شنبه'];
    private const PERSIAN = ['۰','۱','۲','۳','۴','۵','۶','۷','۸','۹'];
    private const ARABIC = ['٠','١','٢','٣','٤','٥','٦','٧','٨','٩'];
    private const YEARS = [1200, 1599]; // Modern civil dates, not distant historical/future dates.

    public static function tehran(int $timestamp): DateTimeImmutable
    {
        return (new DateTimeImmutable('@' . $timestamp))->setTimezone(new DateTimeZone('Asia/Tehran'));
    }

    public static function clock(int $timestamp, bool $withSeconds = false): string
    {
        return self::tehran($timestamp)->format($withSeconds ? 'H:i:s' : 'H:i');
    }

    public static function digits(string $value): string
    {
        return str_replace(range(0, 9), self::PERSIAN, $value);
    }

    private static function civil(string $iso): ?DateTimeImmutable
    {
        if (!preg_match('/\A[0-9]{4}-[0-9]{2}-[0-9]{2}\z/', $iso)) return null;
        // UTC is used here only as a stable arithmetic frame for a civil day
        // already chosen in Tehran, including historical changes in DST.
        $day = DateTimeImmutable::createFromFormat('!Y-m-d', $iso, new DateTimeZone('UTC'));
        return $day && $day->format('Y-m-d') === $iso ? $day : null;
    }

    /** The first day of Farvardin. Adapted from jalaali-js 1.2.8's jalCal (MIT, below). */
    private static function newYear(int $year): DateTimeImmutable
    {
        $breaks = [-61,9,38,199,426,686,756,818,1111,1181,1210,1635,2060,2097,2192,2262,2324,2394,2456,3178];
        $gregorian = $year + 621;
        $leapJ = -14;
        $previous = $breaks[0];
        for ($i = 1; $i < count($breaks); $i++) {
            $jump = $breaks[$i] - $previous;
            if ($year < $breaks[$i]) break;
            $leapJ += intdiv($jump, 33) * 8 + intdiv($jump % 33, 4);
            $previous = $breaks[$i];
        }
        $n = $year - $previous;
        $leapJ += intdiv($n, 33) * 8 + intdiv(($n % 33) + 3, 4);
        if ($jump % 33 === 4 && $jump - $n === 4) $leapJ++;
        $leapG = intdiv($gregorian, 4) - intdiv((intdiv($gregorian, 100) + 1) * 3, 4) - 150;
        return new DateTimeImmutable(sprintf('%04d-03-%02d', $gregorian, 20 + $leapJ - $leapG), new DateTimeZone('UTC'));
    }

    private static function parts(DateTimeImmutable $day): ?array
    {
        [$min, $max] = self::YEARS;
        $year = (int)$day->format('Y') - 621;
        if ($year < $min || $year > $max + 1) return null;
        $start = self::newYear($year);
        if ($day < $start) $start = self::newYear(--$year);
        if ($year < $min || $year > $max) return null;
        $offset = (int)$start->diff($day)->days;
        return $offset < 186 ? [$year, 1 + intdiv($offset, 31), 1 + $offset % 31]
            : [$year, 7 + intdiv($offset - 186, 30), 1 + ($offset - 186) % 30];
    }

    private static function format(array $parts): string
    {
        return self::digits(sprintf('%04d/%02d/%02d', ...$parts));
    }

    /** A Persian-numeral date for an input or label. Never fall back to visible ISO. */
    public static function shamsi(string $iso): ?string
    {
        $day = self::civil($iso);
        $parts = $day ? self::parts($day) : null;
        return $parts === null ? null : self::format($parts);
    }

    public static function label(string $iso): string
    {
        $day = self::civil($iso);
        $parts = $day ? self::parts($day) : null;
        return $parts === null ? '—' : self::WEEKDAYS[(int)$day->format('w')] . ' ' . self::format($parts);
    }

    /** Accept Persian, Arabic-Indic or ASCII digits; reject rollover and invalid leap days. */
    public static function fromShamsi(mixed $input): ?string
    {
        if (!is_string($input) || strlen($input) > 32) return null;
        $input = str_replace(self::ARABIC, range(0, 9), str_replace(self::PERSIAN, range(0, 9), trim($input)));
        if (!preg_match('~\A([0-9]{4})([/-])([0-9]{1,2})\2([0-9]{1,2})\z~', $input, $m)) return null;
        [$year, $month, $day] = [(int)$m[1], (int)$m[3], (int)$m[4]];
        if ($year < self::YEARS[0] || $year > self::YEARS[1] || $month < 1 || $month > 12 || $day < 1 || $day > 31) return null;
        $offset = ($month <= 6 ? ($month - 1) * 31 : 186 + ($month - 7) * 30) + $day - 1;
        $civil = self::newYear($year)->modify('+' . $offset . ' days');
        return self::parts($civil) === [$year, $month, $day] ? $civil->format('Y-m-d') : null;
    }
}

/*
MIT License

Copyright (c) 2020 Behrang Norouzinia

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
*/
