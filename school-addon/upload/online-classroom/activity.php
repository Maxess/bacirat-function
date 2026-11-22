<?php
declare(strict_types=1);
require_once __DIR__ . '/lesson.php';

const CL_FILE_LIMIT = 16 * 1024 * 1024;
const CL_PDF_RANGE_LIMIT = 16;
const CL_LESSON_FILE_LIMIT = 48 * 1024 * 1024;
const CL_CHAT_LIMIT = 120;
const CL_INK_POINTS = 96;
const CL_INK_BATCH = 6;
const CL_INK_TARGETS = 32;
const CL_INK_CUE_SECONDS = 1.1; // A fresh delta can arrive on the approximately one-second idle read.
const CL_LASER_POINTS = 20;
const CL_LASER_TTL_MS = 1900;

function cl_activity(ClassroomStore $s, string $lesson): array
{
    return $s->get('activity', $lesson) ?? ['messages'=>0, 'last_chat'=>[], 'rev'=>1, 'doc'=>null, 'page'=>1, 'docs'=>[], 'ink_ops'=>0];
}

function cl_seq(int $n): string { return sprintf('%08d', max(0, $n)); }

function cl_chat(ClassroomStore $s, array $lesson, array $actor, mixed $body, int $now): void
{
    if (!is_string($body) || strlen($body) > 2400 || !preg_match('/^.{1,600}$/usD', trim($body))) {
        throw new ClassroomError(400, 'متن پیام باید بین ۱ تا ۶۰۰ نویسه باشد.');
    }
    $text = preg_replace('/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x{202A}-\x{202E}\x{2066}-\x{2069}]/u', '', trim($body));
    if (trim((string)$text) === '') throw new ClassroomError(400, 'پیام خالی است.');
    $id = $lesson['id'];
    $activity = cl_activity($s, $id);
    if (($activity['last_chat'][$actor['identity']] ?? 0) >= $now - 1) throw new ClassroomError(429, 'برای پیام بعدی کمی صبر کن.');
    $activity['last_chat'][$actor['identity']] = $now;
    $item = ++$activity['messages'];
    $s->tx(static function () use ($s, $id, $activity, $item, $actor, $text, $now) {
        $s->put('chat:' . $id, cl_seq($item), ['n'=>$item, 'by'=>cl_room_actor_name($actor), 'author'=>$actor['identity'], 'text'=>$text, 'at'=>$now]);
        if ($item > CL_CHAT_LIMIT) $s->delete('chat:' . $id, cl_seq($item - CL_CHAT_LIMIT));
        $s->put('activity', $id, $activity);
    });
}

function cl_layer_key(array $state): string
{
    return ($state['doc'] ?? 'blank') . ':' . $state['page'];
}

function cl_layer(ClassroomStore $s, string $lesson, array $state): array
{
    $key = cl_layer_key($state);
    // Layer identifiers depend only on the server-selected document and page.
    return $s->get('layers:' . $lesson, $key) ?? ['key'=>substr(hash('sha256', $key), 0, 16), 'seq'=>0];
}

function cl_activity_sync(ClassroomStore $s, array $lesson, array $actor, array $input, ?int $at = null): array
{
    $id = $lesson['id'];
    $state = cl_activity($s, $id);
    $n = cl_id($input['chat'] ?? null);
    $chat = $n !== $state['messages'] ? array_values($s->rows('chat:' . $id, 40, cl_seq($n))) : [];
    $fresh = time() - 6;
    foreach ($chat as &$row) {
        $at = $row['at'] ?? null;
        if (!is_int($at) || $at <= 0) continue;
        $row['clock'] = cl_tehran_clock($at);
        if ($at >= $fresh) $row['recent'] = true;
    }
    unset($row);
    return ['chat'=>$chat, 'chatMore'=>($chat ? $chat[array_key_last($chat)]['n'] : $n) < $state['messages'],
        'board'=>cl_board_view($s, $id, $actor, $input, $state, $lesson, $at)];
}

const CL_VIEW_FULL = ['zoom'=>100, 'crop'=>[0,0,10000,10000]];

function cl_require_board_control(array $lesson, array $actor, array $body, int $now): void
{
    if (!cl_controls_board($lesson, $actor, $now)) throw new ClassroomError(403, 'فایل، صفحه و زوم در اختیار ارائه‌دهندهٔ فعال تخته است.');
    $sid = $body['sid'] ?? null;
    if (!cl_match_transport($lesson, $body) || !cl_sid($sid) || ($lesson['staff'][$actor['identity']] ?? null) !== $sid) {
        throw new ClassroomError(409, 'اتصال ارائه‌دهنده عوض شده است؛ دوباره وارد کلاس شو.');
    }
}

function cl_view_input(mixed $value): array
{
    $zoom = is_array($value) ? ($value['zoom'] ?? null) : null;
    $crop = is_array($value) ? ($value['crop'] ?? null) : null;
    if (!is_int($zoom) || $zoom < 75 || $zoom > 300 || !is_array($crop) || !array_is_list($crop) || count($crop) !== 4
        || count(array_filter($crop, 'is_int')) !== 4 || min($crop) < 0 || max($crop) > 10000
        || $crop[2]-$crop[0] < 3000 || $crop[3]-$crop[1] < 3000 || ($zoom <= 100 && $crop !== CL_VIEW_FULL['crop'])) {
        throw new ClassroomError(400, 'درصد بزرگ‌نمایی یا محدودهٔ نمای تخته معتبر نیست.');
    }
    return ['zoom'=>$zoom, 'crop'=>$crop];
}

// A null actor is the read-only recorder. It gets normalized laser points, never their author or SID.
function cl_board_view(ClassroomStore $s, string $id, ?array $actor, array $input, array $state, ?array $lesson = null, ?int $at = null): array
{
    $layer = cl_layer($s, $id, $state);
    $clock = $layer['clock'] ?? $layer['seq']; // Older rows are one tick each.
    $position = cl_id($input['ink'] ?? null);
    $reset = ($input['board'] ?? null) !== $state['rev'] || $position > $clock;
    if ($reset) $position = 0;
    $cursor = $position;
    $ops = [];
    $now = microtime(true);
    $compact = ($input['compact'] ?? null) === true;
    if ($position < $clock) {
        foreach ($s->rows('ink:' . $id . ':' . $layer['key'], $compact ? 32 : 120, cl_seq($position)) as $end => $op) {
            $cursor = (int)$end;
            if ($op['tool'] === 'pen') {
                if (!isset($op['id'])) $op['id'] = 'old-' . cl_seq($cursor);
                else {
                    // A reader may already have part of the tail row. Send only its extension,
                    // with the last shared point to join the two pieces without a gap.
                    $offset = count($op['points']) - ($cursor - $position) - 1;
                    if ($offset > 0) $op['points'] = array_slice($op['points'], $offset);
                }
            }
            if (!$reset && isset($op['author'], $op['at']) && $op['at'] > $now - CL_INK_CUE_SECONDS) $op['live'] = true;
            unset($op['at']);
            $ops[] = $op;
        }
    }
    $doc = $state['doc'] === null ? null : $state['docs'][$state['doc']];
    $write = $actor && $lesson && cl_may_write($lesson, $actor);
    $frame = $state['view'] ?? null;
    $shared = $actor && $lesson && cl_controls_board($lesson, $actor, $at ?? (int)$now) ? ['navigate'=>true] : [];
    if ($lesson && $frame && $frame['epoch'] === cl_epoch($lesson)) {
        if ($frame['value'] !== CL_VIEW_FULL) $shared['view'] = $frame['value'];
        if ($frame['at'] > $now - 1.2) $shared['viewMoving'] = true;
    }
    $laser = $state['laser'] ?? null;
    $aim = null;
    if ($lesson && isset($laser['points']) && $laser['rev'] === $state['rev'] && $laser['epoch'] === cl_epoch($lesson)
        && ($lesson['staff'][$laser['by']] ?? null) === $laser['sid']) {
        $remaining = CL_LASER_TTL_MS - max(0, (int)(1000 * ($now - $laser['at'])));
        if ($remaining > 0) {
            $points = [];
            foreach ($laser['points'] as [$x, $y, $age]) {
                if ($age < $remaining) $points[] = [$x, $y, $remaining - $age];
            }
            if ($points) $aim = ['points'=>$points] + ($actor ? ['by'=>$laser['by']] : []);
        }
    }
    $moving = ($layer['changed_at'] ?? 0) > $now - 1.6;
    if ($compact && !$reset) {
        // A matching revision already implies the same page, document and file list.
        // Write permission is independent of that revision; send it even on an idle read.
        return ['rev'=>$state['rev'], 'cursor'=>$cursor, 'write'=>$write]
            + ($write ? ['owner'=>cl_ink_prefix($id, $actor['identity'])] : [])
            + ($cursor < $clock ? ['more'=>true] : []) + ($ops ? ['ops'=>$ops] : [])
            + ($moving ? ['moving'=>true] : []) + ($aim ? ['laser'=>$aim] : []) + $shared;
    }
    return ['doc'=>$doc, 'page'=>$state['page'], 'rev'=>$state['rev'], 'cursor'=>$cursor, 'more'=>$cursor < $clock,
        'reset'=>$reset, 'write'=>$write, 'owner'=>$write ? cl_ink_prefix($id, $actor['identity']) : null,
        'ops'=>$ops, 'moving'=>$moving, 'laser'=>$aim,
        'docs'=>$actor && cl_is_staff($actor) ? array_values($state['docs']) : []] + $shared;
}

function cl_may_write(array $lesson, array $actor): bool
{
    return cl_is_staff($actor) || cl_access_enabled($lesson, 'board', $actor['identity']);
}

// Prefix assigned by PHP on every new pen stroke, never accepted from a browser.
// The remaining 64 bits distinguish gestures; no extra ownership table or lookup is needed.
function cl_ink_prefix(string $lesson, string $identity): string
{
    return substr(hash('sha256', 'ink-owner:' . $lesson . ':' . $identity), 0, 16);
}

function cl_ink_input(array $lesson, array $actor, array $body): array
{
    if (!cl_may_write($lesson, $actor)) throw new ClassroomError(403, 'نوشتن روی تخته هنوز برای تو فعال نیست.');
    $items = $body['strokes'] ?? null;
    if (!is_array($items) || !array_is_list($items) || !$items || count($items) > CL_INK_BATCH) throw new ClassroomError(400, 'مسیر قلم معتبر نیست.');
    $prefix = cl_ink_prefix($lesson['id'], $actor['identity']);
    $staff = cl_is_staff($actor);
    foreach ($items as &$stroke) {
        if (!is_array($stroke)) throw new ClassroomError(400, 'مسیر قلم معتبر نیست.');
        $tool = $stroke['tool'] ?? null;
        if ($tool === 'pen') {
            if (!is_string($stroke['id'] ?? null) || !preg_match('/^[a-f0-9]{32}$/D', $stroke['id'])) {
                throw new ClassroomError(400, 'شناسهٔ خط معتبر نیست.');
            }
            $stroke['id'] = $prefix . substr($stroke['id'], 16);
        } elseif (!$staff) {
            if ($tool !== 'erase-stroke') throw new ClassroomError(403, 'دانش‌آموز فقط می‌تواند خط خودش را پاک کند.');
            $targets = $stroke['targets'] ?? null;
            if (!is_array($targets) || !array_is_list($targets) || !$targets || count($targets) > CL_INK_TARGETS) {
                throw new ClassroomError(400, 'فهرست خطوط پاک‌کن معتبر نیست.');
            }
            foreach ($targets as $id) {
                if (!is_string($id) || !preg_match('/^[a-f0-9]{32}$/D', $id) || !str_starts_with($id, $prefix)) {
                    throw new ClassroomError(403, 'دانش‌آموز فقط می‌تواند خط خودش را پاک کند.');
                }
            }
        }
    }
    unset($stroke);
    $body['strokes'] = $items;
    $body['author'] = $actor['identity'];
    return $body;
}

/** One bounded snapshot of the current sweep, not an append-only history. */
function cl_board_read(ClassroomStore $s, array $lesson, array $actor, array $body, ?float $receivedAt = null, ?int $at = null): array
{
    $id = $lesson['id'];
    $state = cl_activity($s, $id);
    $now = microtime(true);
    $at ??= (int)$now;
    $changed = false;
    if (array_key_exists('view', $body)) {
        cl_require_board_control($lesson, $actor, $body, $at);
        $view = cl_view_input($body['view']);
        $prior = $state['view'] ?? null;
        $epoch = cl_epoch($lesson);
        // Do not apply requests from the previous page. Reply with the current board so
        // a delayed read can recover without a second request. One snapshot, not a log.
        if (($body['board'] ?? null) === $state['rev'] && (($prior['epoch'] ?? null) !== $epoch
            || ($prior['value'] !== $view && $prior['at'] <= $now - .18))) {
            $state['view'] = ['epoch'=>$epoch, 'value'=>$view, 'at'=>$now];
            $changed = true;
        }
    }
    if (array_key_exists('aim', $body)) {
        if (!cl_is_staff($actor)) throw new ClassroomError(403, 'نشانگر ارائه فقط برای دبیر یا مسئول مجاز است.');
        $points = $body['aim'];
        if (!is_array($points) || !array_is_list($points) || !$points || count($points) > CL_LASER_POINTS) {
            throw new ClassroomError(400, 'مسیر نشانگر معتبر نیست.');
        }
        $older = CL_LASER_TTL_MS - 1;
        foreach ($points as $point) {
            if (!is_array($point) || !array_is_list($point) || count($point) !== 3
                || !is_int($point[0]) || !is_int($point[1]) || !is_int($point[2])
                || min($point) < 0 || max($point[0], $point[1]) > 10000 || $point[2] > $older) {
                throw new ClassroomError(400, 'مختصات یا زمان مسیر نشانگر معتبر نیست.');
            }
            $older = $point[2];
        }
        $sid = $body['sid'] ?? null;
        if (!cl_sid($sid) || ($lesson['staff'][$actor['identity']] ?? null) !== $sid) {
            throw new ClassroomError(409, 'اتصال ارائه‌دهنده عوض شده است.');
        }
        // A request waiting for the classroom lock does not get a fresh pointer lifetime.
        $delay = max(0, (int)ceil(($now - ($receivedAt ?? $now)) * 1000));
        if (($body['board'] ?? null) === $state['rev'] && $older + $delay < CL_LASER_TTL_MS
            && ($state['laser']['at'] ?? 0) <= $now - .18) {
            $points = array_map(static fn(array $point): array => [$point[0], $point[1], $point[2] + $delay], $points);
            $state['laser'] = ['by'=>$actor['identity'], 'sid'=>$sid, 'epoch'=>cl_epoch($lesson),
                'rev'=>$state['rev'], 'points'=>$points, 'at'=>$now];
            $changed = true;
        }
    }
    if ($changed) $s->put('activity', $id, $state);
    return cl_board_view($s, $id, $actor, $body, $state, $lesson, $at);
}

function cl_board_action(ClassroomStore $s, array $lesson, array $input, string $action): array
{
    $id = $lesson['id'];
    $state = cl_activity($s, $id);
    if (($input['board'] ?? null) !== $state['rev']) throw new ClassroomError(409, 'صفحهٔ تخته تغییر کرده است؛ دوباره امتحان کن.');
    if ($action === 'board-page') {
        $doc = $input['doc'] ?? null;
        $page = cl_id($input['page'] ?? null);
        if (!$page || ($doc !== null && (!is_string($doc) || !isset($state['docs'][$doc])))
            || $page > ($doc === null ? 1 : $state['docs'][$doc]['pages'])) throw new ClassroomError(400, 'صفحه یا فایل تخته معتبر نیست.');
        if ($doc !== $state['doc'] || $page !== $state['page']) {
            $state['doc'] = $doc;
            $state['page'] = $page;
            $state['rev']++;
            unset($state['laser'], $state['view']);
            $s->put('activity', $id, $state);
        }
        return ['boardRev'=>$state['rev']];
    }
    $layer = cl_layer($s, $id, $state);
    if ($action === 'board-clear') {
        $state['rev']++;
        unset($state['laser']);
        $s->tx(static function () use ($s, $id, $state, $layer) {
            $s->delete('ink:' . $id . ':' . $layer['key']);
            $s->delete('layers:' . $id, cl_layer_key($state));
            $state['ink_ops'] -= $layer['seq'];
            $s->put('activity', $id, $state);
        });
        return ['boardRev'=>$state['rev']];
    }
    $items = $input['strokes'] ?? null;
    if (!is_array($items) || !array_is_list($items) || !$items || count($items) > CL_INK_BATCH) throw new ClassroomError(400, 'مسیر قلم معتبر نیست.');
    $strokes = [];
    $author = $input['author'] ?? null; // Assigned by cl_ink_input at the authenticated entry point.
    $stamp = microtime(true);
    foreach ($items as $item) {
        if (!is_array($item)) throw new ClassroomError(400, 'مسیر قلم معتبر نیست.');
        $tool = $item['tool'] ?? null;
        if ($tool === 'erase-stroke') {
            $targets = $item['targets'] ?? null;
            if (!is_array($targets) || !array_is_list($targets) || !$targets || count($targets) > CL_INK_TARGETS) {
                throw new ClassroomError(400, 'فهرست خطوط پاک‌کن معتبر نیست.');
            }
            foreach ($targets as $target) {
                if (!is_string($target) || !preg_match('/^(?:[0-9a-f]{32}|old-[0-9]{8})$/D', $target)) {
                    throw new ClassroomError(400, 'شناسهٔ خط معتبر نیست.');
                }
            }
            $strokes[] = ['tool'=>$tool, 'targets'=>array_values(array_unique($targets))];
            continue;
        }
        $color = $item['color'] ?? null; $size = $item['size'] ?? null; $points = $item['points'] ?? null;
        if (!in_array($tool, ['pen','erase'], true) || !is_string($color) || !preg_match('/^#[0-9a-f]{6}$/iD', $color)
            || !is_int($size) || $size < 1 || $size > 36 || !is_array($points) || !array_is_list($points)
            || count($points) < 1 || count($points) > CL_INK_POINTS) throw new ClassroomError(400, 'تنظیم قلم یا مسیر آن معتبر نیست.');
        foreach ($points as $pt) {
            if (!is_array($pt) || !array_is_list($pt) || count($pt) !== 2 || !is_int($pt[0]) || !is_int($pt[1])
                || min($pt) < 0 || max($pt) > 10000) throw new ClassroomError(400, 'مختصات قلم بیرون از تخته است.');
        }
        $op = ['tool'=>$tool, 'size'=>$size, 'color'=>$color, 'points'=>$points];
        if (array_key_exists('id', $item)) {
            if ($tool !== 'pen' || !is_string($item['id']) || !preg_match('/^[0-9a-f]{32}$/D', $item['id'])) {
                throw new ClassroomError(400, 'شناسهٔ خط معتبر نیست.');
            }
            $op['id'] = $item['id'];
        }
        if ($tool === 'pen' && cl_media_identity($author)) $op += ['author'=>$author, 'at'=>$stamp];
        $strokes[] = $op;
    }
    return $s->tx(static function () use ($s, $id, $state, $layer, $strokes) {
        $bucket = 'ink:' . $id . ':' . $layer['key'];
        $clock = $layer['clock'] ?? $layer['seq'];
        $tail = $clock && isset($strokes[0]['id']) ? $s->get($bucket, cl_seq($clock)) : null;
        $added = 0;
        foreach ($strokes as $op) {
            $ticks = isset($op['id']) ? count($op['points']) : 1;
            // Coalesce time-based packets into bounded rows, but never across another
            // gesture or an erasure. Small, slow packets do not exhaust the page budget.
            if (isset($op['id'], $tail['id']) && $op['id'] === $tail['id'] && ($op['author'] ?? null) === ($tail['author'] ?? null) && $op['color'] === $tail['color'] && $op['size'] === $tail['size']
                && $tail['points'][array_key_last($tail['points'])] === $op['points'][0]
                && count($tail['points']) + $ticks - 1 <= CL_INK_POINTS) {
                if ($ticks === 1) continue;
                $s->delete($bucket, cl_seq($clock));
                $op['points'] = array_merge($tail['points'], array_slice($op['points'], 1));
                $ticks--;
            } else {
                $added++;
                if ($layer['seq'] + $added > 1600 || $state['ink_ops'] + $added > 12000) {
                    throw new ClassroomError(409, 'فضای خطوط تخته پر شده است؛ نوشته‌های این صفحه را پاک کن یا صفحه را عوض کن.');
                }
            }
            $clock += $ticks;
            $s->put($bucket, cl_seq($clock), $op);
            $tail = $op;
        }
        $layer['clock'] = $clock;
        $layer['seq'] += $added;
        $layer['changed_at'] = microtime(true);
        $state['ink_ops'] += $added;
        $s->put('layers:' . $id, cl_layer_key($state), $layer);
        if ($added) $s->put('activity', $id, $state);
        return ['ink'=>$clock];
    });
}

/** Inclusive byte spans. Reject overlapping, excessive, or out-of-bounds requests. */
function cl_pdf_ranges(string $header, int $size): array
{
    if (!str_starts_with($header, 'bytes=') || strlen($header) > 400) return [];
    $specs = explode(',', substr($header, 6));
    if (count($specs) > CL_PDF_RANGE_LIMIT) return [];
    $spans = [];
    $last = -1;
    foreach ($specs as $spec) {
        if (!preg_match('/^(\d{1,10})-(\d{0,10})$/D', trim($spec), $m)) return [];
        $start = (int)$m[1];
        $end = $m[2] === '' ? $size - 1 : min($size - 1, (int)$m[2]);
        if ($start <= $last || $end < $start) return [];
        $spans[] = [$start,$end];
        $last = $end;
    }
    return $spans;
}

function cl_file_span($file, int $start, int $bytes): void
{
    if (fseek($file, $start) !== 0) throw new ClassroomError(503, '');
    while ($bytes > 0 && !feof($file) && !connection_aborted()) {
        $chunk = fread($file, min($bytes, 65536));
        if ($chunk === false || $chunk === '') break;
        echo $chunk;
        $bytes -= strlen($chunk);
    }
}

function cl_lesson_dir(string $id, bool $create): ?string
{
    if (!preg_match('/^[a-f0-9]{32}$/D', $id)) throw new LogicException('Invalid lesson document directory');
    $path = cl_manager_context()['plugin_dir'] ?? null;
    if ($path === null && !$create) return null;
    if (!is_string($path) || realpath($path) !== $path) throw new ClassroomError(503, 'فضای خصوصی تخته در دسترس نیست.');
    foreach (['documents', $id] as $part) {
        $path .= '/' . $part;
        if (!file_exists($path) && !is_link($path)) {
            if (!$create) return null;
            if (!@mkdir($path, 0700) && !is_dir($path)) throw new ClassroomError(503, 'ساخت پوشهٔ خصوصی فایل ممکن نشد.');
        }
        if (is_link($path) || !is_dir($path) || realpath($path) !== $path) throw new ClassroomError(503, 'مسیر خصوصی فایل قابل اعتماد نیست.');
    }
    return $path;
}

function cl_upload(ClassroomStore $s, array $lesson, array $input, array $file): array
{
    $state = cl_activity($s, $lesson['id']);
    if (cl_id($input['board'] ?? null) !== $state['rev']) throw new ClassroomError(409, 'صفحهٔ تخته تغییر کرده است؛ دوباره امتحان کن.');
    $tmp = $file['tmp_name'] ?? null;
    if (($file['error'] ?? -1) !== UPLOAD_ERR_OK || !is_string($tmp) || !is_uploaded_file($tmp)) {
        throw new ClassroomError(413, 'فایل دریافت نشد؛ حجم فایل و حد آپلود PHP را بررسی کن.');
    }
    $bytes = filesize($tmp);
    if (!$bytes || $bytes > CL_FILE_LIMIT || count($state['docs']) >= 12 || array_sum(array_column($state['docs'], 'bytes')) + $bytes > CL_LESSON_FILE_LIMIT) {
        throw new ClassroomError(413, 'حد هر فایل ۱۶ مگابایت و مجموع فایل‌های هر زنگ ۴۸ مگابایت (تا ۱۲ فایل) است.');
    }
    if (!class_exists('finfo')) throw new ClassroomError(503, 'برای بررسی فایل تخته، PHP Fileinfo روی هاست لازم است.');
    $mime = (new finfo(FILEINFO_MIME_TYPE))->file($tmp);
    if ($mime === 'application/pdf') {
        $pages = cl_id($input['pages'] ?? null);
        if (!$pages || !preg_match('~^%PDF-[12]\.\d~', (string)file_get_contents($tmp, false, null, 0, 12))) {
            throw new ClassroomError(400, 'فایل PDF خوانا نیست.');
        }
    } elseif (in_array($mime, ['image/png','image/jpeg','image/webp'], true)) {
        $dimensions = @getimagesize($tmp);
        if (!$dimensions || $dimensions['mime'] !== $mime || min($dimensions[0], $dimensions[1]) < 1 || $dimensions[0] * $dimensions[1] > 24_000_000) {
            throw new ClassroomError(400, 'ابعاد تصویر معتبر نیست یا بیش از حد بزرگ است.');
        }
        $pages = 1;
    } else {
        throw new ClassroomError(415, 'فقط PDF، PNG، JPEG و WebP برای تخته مجاز است.');
    }
    $doc = bin2hex(random_bytes(16));
    $target = cl_lesson_dir($lesson['id'], true) . '/' . $doc;
    if (!move_uploaded_file($tmp, $target)) throw new ClassroomError(503, 'ذخیرهٔ فایل خصوصی تخته ممکن نشد.');
    @chmod($target, 0600);
    $name = cl_safe_label(basename(str_replace('\\', '/', (string)($file['name'] ?? ''))), 110) ?: 'فایل تخته';
    $state['docs'][$doc] = ['id'=>$doc, 'name'=>$name, 'mime'=>$mime, 'pages'=>$pages, 'bytes'=>$bytes];
    if (isset($dimensions)) $state['docs'][$doc] += ['width'=>$dimensions[0], 'height'=>$dimensions[1]];
    $state['doc'] = $doc;
    $state['page'] = 1;
    $state['rev']++;
    unset($state['laser'], $state['view']);
    try { $s->put('activity', $lesson['id'], $state); }
    catch (Throwable $error) { @unlink($target); throw $error; }
    return ['boardRev'=>$state['rev']];
}

function cl_clear_activity(ClassroomStore $s, string $id): void
{
    // More than one layer may exist for a multi-page document. The prefix is an internally generated hex id.
    $s->eraseInk($id);
    $s->delete('layers:' . $id);
    $s->delete('chat:' . $id);
    $s->delete('activity', $id);
}

function cl_clear_files(string $id): void
{
    try {
        $path = cl_lesson_dir($id, false);
        if ($path !== null) {
            foreach (new DirectoryIterator($path) as $file) {
                if (!$file->isDot() && preg_match('/^[a-f0-9]{32}$/D', $file->getFilename()) && !$file->isDir()) @unlink($file->getPathname());
            }
            @rmdir($path);
        }
    } catch (Throwable $ignored) {
        error_log('School classroom: private attachment cleanup not confirmed');
    }
}


/** Open the server-selected attachment while the caller still holds the class lock. */
function cl_board_file(array $lesson, array $state, string $key): array
{
    if (!preg_match('/^[a-f0-9]{32}$/D', $key) || !isset($state['docs'][$key])) throw new ClassroomError(404, '');
    $dir = cl_lesson_dir($lesson['id'], false);
    $path = $dir ? $dir . '/' . $key : null;
    if (!$path || is_link($path) || !is_file($path) || realpath($path) !== $path || !($fp = @fopen($path, 'rb'))) throw new ClassroomError(404, '');
    return [$state['docs'][$key], $fp];
}

function cl_reply_board_file(array $doc, $file): void
{
    $size = $doc['bytes'];
    $pdf = $doc['mime'] === 'application/pdf';
    $range = $_SERVER['HTTP_RANGE'] ?? null;
    $spans = [[0, $size-1]];
    if ($pdf) {
        // Preserve the representation and offsets, including on hosts that gzip PHP output.
        ini_set('zlib.output_compression', '0');
        header('Cache-Control: no-store, private, no-transform');
        header('Accept-Ranges: bytes');
        if ($range !== null) {
            $spans = cl_pdf_ranges($range, $size);
            if (!$spans) {
                header('Content-Range: bytes */' . $size);
                throw new ClassroomError(416, '');
            }
            http_response_code(206);
        }
    }
    header('Content-Disposition: attachment; filename="' . ($pdf ? 'board.pdf' : 'board-image') . '"');
    if (count($spans) === 1) {
        [$start,$end] = $spans[0];
        $length = $end - $start + 1;
        header('Content-Type: ' . $doc['mime']);
        if ($pdf && $range !== null) header("Content-Range: bytes $start-$end/$size");
        header('Content-Length: ' . $length);
        cl_file_span($file, $start, $length);
    } else {
        $boundary = 'baci-' . bin2hex(random_bytes(12));
        $tail = "--$boundary--\r\n";
        $length = strlen($tail);
        $parts = [];
        foreach ($spans as [$start,$end]) {
            $head = "--$boundary\r\nContent-Type: application/pdf\r\nContent-Range: bytes $start-$end/$size\r\n\r\n";
            $bytes = $end - $start + 1;
            $parts[] = [$head,$start,$bytes];
            $length += strlen($head) + $bytes + 2;
        }
        header('Content-Type: multipart/byteranges; boundary=' . $boundary);
        header('Content-Length: ' . $length);
        foreach ($parts as [$head,$start,$bytes]) {
            if (connection_aborted()) return;
            echo $head;
            cl_file_span($file, $start, $bytes);
            echo "\r\n";
        }
        echo $tail;
    }
}
