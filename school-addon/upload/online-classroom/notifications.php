<?php
declare(strict_types=1);
require_once __DIR__ . '/store.php';
require_once __DIR__ . '/school.php';

final class ClassroomBotFailure extends RuntimeException
{
    public function __construct(public readonly string $outcome)
    {
        parent::__construct('Bot request not confirmed');
    }
}

function cl_duration(int $seconds): string
{
    return sprintf('%02d:%02d:%02d', intdiv($seconds, 3600), intdiv($seconds % 3600, 60), $seconds % 60);
}

function cl_bot_token(ClassroomStore $s, array $cfg, string $provider): ?string
{
    if (($cfg['notify_' . $provider] ?? false) !== true) return null;
    $t = $provider === 'rubika' ? ($cfg['rubika_token'] ?? '') : cl_query($s->db, 'SELECT key_value FROM settings WHERE key_name=?', [$provider . '_bot_token'])->fetchColumn();
    return is_string($t) && preg_match('/^[A-Za-z0-9_:\-]{12,180}$/D', $t) ? $t : null;
}

function cl_bot_call(string $provider, string $token, string $method, array $data): array
{
    if (!preg_match('/^[A-Za-z0-9_:\-]{12,180}$/D', $token) || !in_array($method, ['getUpdates','getChat','sendMessage'], true)) {
        throw new LogicException('Unsupported bot operation');
    }
    $url = match ($provider) {
        'bale'=>'https://tapi.bale.ai/bot' . $token, 'telegram'=>'https://api.telegram.org/bot' . $token,
        'rubika'=>'https://botapi.rubika.ir/v3/' . $token, default=>throw new LogicException('Unknown provider'),
    };
    $raw = '';
    $ch = curl_init($url . '/' . $method);
    curl_setopt_array($ch, [CURLOPT_POST=>true, CURLOPT_HTTPHEADER=>['Content-Type: application/json'],
        CURLOPT_POSTFIELDS=>json_encode($data, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE),
        CURLOPT_CONNECTTIMEOUT=>2, CURLOPT_TIMEOUT=>5, CURLOPT_FOLLOWLOCATION=>false,
        CURLOPT_WRITEFUNCTION=>static function ($ch, string $chunk) use (&$raw): int {
            if (strlen($raw) + strlen($chunk) > 1024 * 1024) return 0;
            $raw .= $chunk;
            return strlen($chunk);
        }]);
    $ok = curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    unset($ch);
    return cl_bot_parse($provider, $ok !== false, $code, $raw);
}

function cl_bot_parse(string $provider, bool $connected, int $code, string $raw): array
{
    if ($code === 429) throw new ClassroomBotFailure('retry');
    if ($code >= 400 && $code < 500) throw new ClassroomBotFailure('failed');
    if (!$connected || $code !== 200) throw new ClassroomBotFailure('uncertain');
    $reply = json_decode($raw, true, 24);
    if (!is_array($reply)) throw new ClassroomBotFailure('uncertain');
    $success = $provider === 'rubika' ? (($reply['status'] ?? '') === 'OK') : (($reply['ok'] ?? null) === true);
    if (!$success) throw new ClassroomBotFailure('failed');
    $result = $provider === 'rubika' ? ($reply['data'] ?? null) : ($reply['result'] ?? null);
    if (!is_array($result)) throw new ClassroomBotFailure('uncertain');
    return $result;
}

function cl_bot_private(string $provider, array $data, string $chat, ?string $sender = null): bool
{
    if ($provider === 'rubika') {
        $data = $data['chat'] ?? [];
        return is_array($data) && ($data['chat_type'] ?? '') === 'User' && ($data['chat_id'] ?? '') === $chat
            && ($sender === null || ($data['user_id'] ?? '') === $sender);
    }
    return ($data['type'] ?? '') === 'private' && (is_int($data['id'] ?? null) || is_string($data['id'] ?? null)) && (string)$data['id'] === $chat;
}

function cl_rubika_tag(string $token): string { return substr(hash('sha256', $token), 0, 32); }

function cl_own_rubika(ClassroomStore $s, array $cfg, array $actor, string $action, int $now): array
{
    $token = cl_bot_token($s, $cfg, 'rubika');
    if ($actor['role'] !== 'student' || !$token) throw new ClassroomError(403, 'پیوند اعلان روبیکا در دسترس نیست.');
    return cl_room_lock($s->db, $cfg['room_prefix'] . ':rubika', static function () use ($s, $actor, $token, $action, $now) {
        $id = (string)$actor['id'];
        $prior = $s->get('rubika-pair', $id);
        if ($action === 'rubika-pair' && $prior && $prior['until'] > $now + 570) throw new ClassroomError(429, 'برای گرفتن کد تازه کمی صبر کن.');
        return $s->tx(static function () use ($s, $id, $prior, $token, $action, $now) {
            if ($prior) $s->delete('rubika-code', $prior['key']);
            if ($action === 'rubika-unlink') {
                $s->delete('rubika-pair', $id);
                $s->delete('rubika-link', $id);
                return [];
            }
            $code = bin2hex(random_bytes(12));
            $hash = hash('sha256', $code);
            $s->put('rubika-code', $hash, ['student'=>$id, 'until'=>$now + 600, 'tag'=>cl_rubika_tag($token)]);
            $s->put('rubika-pair', $id, ['key'=>$hash, 'until'=>$now + 600]);
            return ['command'=>'/start ' . $code];
        });
    });
}

function cl_rubika_update(ClassroomStore $s, array $cfg, string $token, array $item, int $now, callable $request): void
{
    $m = $item['new_message'] ?? null;
    $chat = $item['chat_id'] ?? null;
    if (($item['type'] ?? '') !== 'NewMessage' || !is_array($m) || ($m['sender_type'] ?? null) !== 'User'
        || !is_string($m['sender_id'] ?? null) || !is_string($chat) || !preg_match('/^[A-Za-z0-9_-]{1,120}$/D', $chat)
        || !is_string($m['text'] ?? null) || !preg_match('/^\/start ([a-f0-9]{24})$/D', trim($m['text']), $code)) return;
    $hash = hash('sha256', $code[1]);
    $pair = $s->get('rubika-code', $hash);
    if (!$pair || $pair['until'] < $now || $pair['tag'] !== cl_rubika_tag($token)) return;
    if (!cl_bot_private('rubika', $request('rubika', $token, 'getChat', ['chat_id'=>$chat]), $chat, $m['sender_id'])) return;
    cl_room_lock($s->db, $cfg['room_prefix'] . ':rubika', static function () use ($s, $pair, $hash, $chat, $now) {
        $id = $pair['student'];
        if ($s->get('rubika-code', $hash) !== $pair || ($s->get('rubika-pair', $id)['key'] ?? null) !== $hash) return;
        $year = cl_current_year($s->db);
        if (!cl_school_account($s->db, 'student', (int)$id, $year)) return;
        $s->tx(static function () use ($s, $id, $chat, $pair, $hash) {
            $s->put('rubika-link', $id, ['chat'=>$chat,'tag'=>$pair['tag']]);
            $s->delete('rubika-code', $hash);
            $s->delete('rubika-pair', $id);
        });
    });
}

function cl_rubika_poll(ClassroomStore $s, array $cfg, string $token, int $now, callable $request): void
{
    $tag = cl_rubika_tag($token);
    $previous = $s->get('rubika-offset', $tag);
    $query = ['limit'=>30];
    if (isset($previous['id'])) $query['offset_id'] = $previous['id'];
    $reply = $request('rubika', $token, 'getUpdates', $query);
    if (!is_array($reply['updates'] ?? null) || !array_is_list($reply['updates']) || count($reply['updates']) > 30) throw new ClassroomBotFailure('uncertain');
    foreach ($reply['updates'] as $item) if (is_array($item)) cl_rubika_update($s, $cfg, $token, $item, $now, $request);
    if (is_string($reply['next_offset_id'] ?? null) && strlen($reply['next_offset_id']) <= 200 && $reply['next_offset_id'] !== '') {
        $s->put('rubika-offset', $tag, ['id'=>$reply['next_offset_id']]);
    }
}

function cl_destinations(ClassroomStore $s, string $provider, string $token, int $student): array
{
    if ($provider === 'rubika') {
        $link = $s->get('rubika-link', (string)$student);
        return $link && $link['tag'] === cl_rubika_tag($token) ? [$link['chat']] : [];
    }
    [$table, $column] = match ($provider) {
        'bale'=>['bale_bot_users', 'bale_chat_id'], 'telegram'=>['telegram_bot_users', 'telegram_chat_id'],
        default=>throw new LogicException('Unknown provider'),
    };
    $rows = cl_query($s->db, "SELECT DISTINCT `$column` FROM `$table` WHERE student_id=? ORDER BY `$column` LIMIT 20", [$student])->fetchAll(PDO::FETCH_COLUMN);
    return array_values(array_filter(array_map('strval', $rows), static fn($id) => preg_match('/^[1-9][0-9]{0,31}$/D', $id)));
}

function cl_delivery_add(ClassroomStore $s, string $lesson, string $type, int $delta = 1): void
{
    $report = $s->get('delivery', $lesson) ?? [];
    $report[$type] = ($report[$type] ?? 0) + $delta;
    $s->put('delivery', $lesson, $report);
}

function cl_outbox_key(array $item, int $due): string
{
    return sprintf('%012d:', $due) . hash('sha256', json_encode([$item['lesson'],$item['student'],$item['provider'],$item['chat']], JSON_THROW_ON_ERROR));
}

function cl_expand_outbox(ClassroomStore $s, array $tokens, string $id, array $pending, int $now): void
{
    $lesson = $s->get('lesson', $id);
    $s->tx(static function () use ($s, $tokens, $id, $pending, $now, $lesson) {
        if ($lesson && $lesson['ended'] + 172800 > $now) {
            foreach ($pending['providers'] as $provider) {
                if (!isset($tokens[$provider])) { cl_delivery_add($s, $id, 'skipped'); continue; }
                $token = $tokens[$provider];
                foreach ($lesson['roster'] as $identity=>$unused) {
                    $student = (int)substr($identity, 8);
                    try { $linked = cl_destinations($s, $provider, $token, $student); }
                    catch (PDOException $error) {
                        if ($error->getCode() !== '42S02') throw $error;
                        cl_delivery_add($s, $id, 'skipped'); // Host bot isn't set up; do not create host tables.
                        break;
                    }
                    foreach ($linked as $chat) {
                        $item = ['lesson'=>$id,'student'=>$student,'provider'=>$provider,'chat'=>$chat,'attempt'=>0];
                        $s->put('outbox', cl_outbox_key($item, $now), $item);
                    }
                }
            }
        }
        $s->delete('notify-pending', $id);
    });
}

function cl_attendance_text(array $lesson, int $student): string
{
    $id = 'student-' . $student;
    return 'حضور در کلاس مجازی — ' . $lesson['roster'][$id] . "\n" . $lesson['class_name'] . ' · ' . $lesson['subject'] . ' · ' . $lesson['period']
        . "\nتاریخ (تهران): " . $lesson['date'] . "\nحضور: " . cl_duration($lesson['seconds'][$id] ?? 0)
        . ' از ' . cl_duration($lesson['active_seconds']) . "\nزمان انتظار و وقفهٔ کلاس در این مدت منظور نشده است.";
}

function cl_deliver_one(ClassroomStore $s, string $key, array $item, array $tokens, int $now, callable $request): void
{
    ['lesson'=>$id, 'student'=>$student, 'provider'=>$provider, 'chat'=>$chat] = $item;
    $lesson = $s->get('lesson', $id);
    $token = $tokens[$provider] ?? null;
    $outcome = 'skipped';
    $retry = static function () use ($s, $item, $now): string {
        if ($item['attempt'] >= 3) return 'failed';
        $item['attempt']++;
        $s->put('outbox', cl_outbox_key($item, $now + 60 * $item['attempt']), $item);
        return 'retry';
    };
    $started = false;
    try {
        if ($token && $lesson && $lesson['ended'] + 172800 > $now
            && cl_school_account($s->db, 'student', $student, $lesson['year'])
            && in_array($chat, cl_destinations($s, $provider, $token, $student), true)
            && cl_bot_private($provider, $request($provider, $token, 'getChat', ['chat_id'=>$chat]), $chat)) {
            // Consume before send. If the provider times out after receiving it, do not duplicate sensitive messages.
            $s->tx(static function () use ($s, $key, $id) {
                $s->delete('outbox', $key);
                cl_delivery_add($s, $id, 'uncertain');
            });
            $started = true;
            $reply = $request($provider, $token, 'sendMessage', ['chat_id'=>$chat, 'text'=>cl_attendance_text($lesson, $student)]);
            $outcome = isset($reply['message_id']) ? 'sent' : 'uncertain';
        }
    } catch (ClassroomBotFailure $error) {
        $outcome = $error->outcome === 'retry' || (!$started && $error->outcome === 'uncertain') ? $retry() : $error->outcome;
    } catch (PDOException $error) {
        if ($error->getCode() !== '42S02') throw $error;
    }
    $s->tx(static function () use ($s, $key, $id, $outcome, $started) {
        $s->delete('outbox', $key);
        if ($started) cl_delivery_add($s, $id, 'uncertain', -1);
        if ($outcome !== 'retry') cl_delivery_add($s, $id, $outcome);
    });
}

function cl_delivery_job(ClassroomStore $s, array $cfg, ?callable $request = null): void
{
    $request ??= 'cl_bot_call';
    cl_room_lock($s->db, $cfg['room_prefix'] . ':delivery', static function () use ($s, $cfg, $request) {
        $tokens = [];
        $start = time();
        foreach (['bale','telegram','rubika'] as $p) if ($t = cl_bot_token($s, $cfg, $p)) $tokens[$p] = $t;
        if (isset($tokens['rubika'])) {
            try { cl_rubika_poll($s, $cfg, $tokens['rubika'], $start, $request); }
            catch (ClassroomBotFailure $ignored) { error_log('School classroom: Rubika registration temporarily unavailable'); }
        }
        foreach ($s->rows('notify-pending', 4) as $id=>$pending) cl_expand_outbox($s, $tokens, $id, $pending, $start);
        foreach ($s->rows('outbox', 55) as $key=>$item) {
            if (time() - $start > 38 || (int)substr($key, 0, 12) > time()) break;
            cl_deliver_one($s, $key, $item, $tokens, time(), $request);
        }
    });
}
