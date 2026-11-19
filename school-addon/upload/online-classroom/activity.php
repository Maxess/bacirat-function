<?php
declare(strict_types=1);
require_once __DIR__ . '/lesson.php';

const CL_FILE_LIMIT = 16 * 1024 * 1024;
const CL_LESSON_FILE_LIMIT = 48 * 1024 * 1024;
const CL_CHAT_LIMIT = 120;

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
        $s->put('chat:' . $id, cl_seq($item), ['n'=>$item, 'by'=>$actor['name'], 'text'=>$text, 'at'=>$now]);
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
    // Fixed bucket per lesson; layer IDs change on erase and are not user inputs.
    return $s->get('layers:' . $lesson, $key) ?? ['key'=>substr(hash('sha256', $key), 0, 16), 'seq'=>0];
}

function cl_activity_sync(ClassroomStore $s, array $lesson, array $actor, array $input): array
{
    $id = $lesson['id'];
    $state = cl_activity($s, $id);
    $n = cl_id($input['chat'] ?? null);
    $chat = $n !== $state['messages'] ? array_values($s->rows('chat:' . $id, 40, cl_seq($n))) : [];
    $layer = cl_layer($s, $id, $state);
    $position = cl_id($input['ink'] ?? null);
    $reset = ($input['board'] ?? null) !== $state['rev'] || $position > $layer['seq'];
    if ($reset) $position = 0;
    $ops = $position < $layer['seq'] ? array_values($s->rows('ink:' . $id . ':' . $layer['key'], 120, cl_seq($position))) : [];
    $cursor = $position + count($ops);
    $doc = $state['doc'] === null ? null : $state['docs'][$state['doc']];
    return ['chat'=>$chat, 'chatMore'=>($chat ? $chat[array_key_last($chat)]['n'] : $n) < $state['messages'],
        'board'=>['doc'=>$doc, 'page'=>$state['page'], 'rev'=>$state['rev'], 'cursor'=>$cursor, 'more'=>$cursor < $layer['seq'],
            'reset'=>$reset, 'ops'=>$ops, 'docs'=>cl_is_staff($actor) ? array_values($state['docs']) : []]];
}

function cl_board_action(ClassroomStore $s, array $lesson, array $input, string $action): void
{
    $id = $lesson['id'];
    $state = cl_activity($s, $id);
    if (($input['board'] ?? null) !== $state['rev']) throw new ClassroomError(409, 'صفحهٔ تخته تغییر کرده است؛ دوباره امتحان کن.');
    if ($action === 'board-page') {
        $doc = $input['doc'] ?? null;
        $page = cl_id($input['page'] ?? null);
        if (!$page || ($doc !== null && (!is_string($doc) || !isset($state['docs'][$doc])))
            || $page > ($doc === null ? 1 : $state['docs'][$doc]['pages'])) throw new ClassroomError(400, 'صفحه یا فایل تخته معتبر نیست.');
        if ($doc === $state['doc'] && $page === $state['page']) return;
        $state['doc'] = $doc;
        $state['page'] = $page;
        $state['rev']++;
        $s->put('activity', $id, $state);
        return;
    }
    $layer = cl_layer($s, $id, $state);
    if ($action === 'board-clear') {
        $s->tx(static function () use ($s, $id, $state, $layer) {
            $s->delete('ink:' . $id . ':' . $layer['key']);
            $s->delete('layers:' . $id, cl_layer_key($state));
            $state['rev']++;
            $state['ink_ops'] -= $layer['seq'];
            $s->put('activity', $id, $state);
        });
        return;
    }
    $items = $input['strokes'] ?? null;
    if (!is_array($items) || !array_is_list($items) || !$items || count($items) > 6) throw new ClassroomError(400, 'مسیر قلم معتبر نیست.');
    if ($layer['seq'] + count($items) > 1600 || $state['ink_ops'] + count($items) > 12000) {
        throw new ClassroomError(409, 'فضای خطوط تخته پر شده است؛ لایهٔ این صفحه را پاک کن یا صفحه را عوض کن.');
    }
    $strokes = [];
    foreach ($items as $item) {
        if (!is_array($item)) throw new ClassroomError(400, 'مسیر قلم معتبر نیست.');
        $tool = $item['tool'] ?? null; $color = $item['color'] ?? null; $size = $item['size'] ?? null; $points = $item['points'] ?? null;
        if (!in_array($tool, ['pen','erase'], true) || !is_string($color) || !preg_match('/^#[0-9a-f]{6}$/iD', $color)
            || !is_int($size) || $size < 1 || $size > 36 || !is_array($points) || !array_is_list($points)
            || count($points) < 1 || count($points) > 96) throw new ClassroomError(400, 'تنظیم قلم یا مسیر آن معتبر نیست.');
        foreach ($points as $pt) {
            if (!is_array($pt) || !array_is_list($pt) || count($pt) !== 2 || !is_int($pt[0]) || !is_int($pt[1])
                || min($pt) < 0 || max($pt) > 10000) throw new ClassroomError(400, 'مختصات قلم بیرون از تخته است.');
        }
        $strokes[] = ['tool'=>$tool, 'size'=>$size, 'color'=>$color, 'points'=>$points];
    }
    $s->tx(static function () use ($s, $id, $state, $layer, $strokes) {
        foreach ($strokes as $stroke) $s->put('ink:' . $id . ':' . $layer['key'], cl_seq(++$layer['seq']), $stroke);
        $s->put('layers:' . $id, cl_layer_key($state), $layer);
        $state['ink_ops'] += count($strokes);
        $s->put('activity', $id, $state);
    });
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

function cl_upload(ClassroomStore $s, array $lesson, array $input, array $file): void
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
    $state['doc'] = $doc;
    $state['page'] = 1;
    $state['rev']++;
    try { $s->put('activity', $lesson['id'], $state); }
    catch (Throwable $error) { @unlink($target); throw $error; }
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
