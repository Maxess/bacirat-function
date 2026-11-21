<?php
declare(strict_types=1);

// Private recording state. Only the class lock (or the manager's exclusive lifecycle lock)
// may mutate a job. The singleton index limits this VPS pilot to one job at a time.
const CL_REC_PROTOCOL = 1;
const CL_REC_RETENTION = 86400;
const CL_REC_MAX_PARTS = 32;
const CL_REC_PROVIDERS = ['bale'=>'بله', 'rubika'=>'روبیکا', 'telegram'=>'تلگرام'];

function cl_rec_enabled(array $cfg): bool
{
    return ($cfg['record_lessons'] ?? null) === true
        && is_string($cfg['recording_secret'] ?? null) && preg_match('/^[A-Za-z0-9_-]{32,128}$/D', $cfg['recording_secret'])
        && !hash_equals((string)($cfg['api_secret'] ?? ''), $cfg['recording_secret']);
}

function cl_rec_id(mixed $value): bool
{
    return is_string($value) && preg_match('/^[a-f0-9]{32}$/D', $value);
}

function cl_rec_chat(string $provider, string $chat): bool
{
    return isset(CL_REC_PROVIDERS[$provider]) && (bool)preg_match($provider === 'rubika'
        ? '/^[A-Za-z0-9_-]{2,64}$/D' : '/^-?[1-9][0-9]{0,19}$/D', $chat);
}

function cl_rec_bot(string $provider): ?array
{
    $adapter = cl_manager_context()['recording_bot'] ?? null;
    return is_callable($adapter) && isset(CL_REC_PROVIDERS[$provider]) ? $adapter($provider) : null;
}

function cl_rec_inventory(PDO $db, string $provider, ?string $chat = null): array
{
    // Older Rubika updates don't include chat_type, so Baci can store a group GUID as 'private'.
    // These are candidates only. Never authorize a destination from a webhook row alone.
    try {
        return cl_query($db, "SELECT chat_id,title FROM bot_chats WHERE platform=? AND status='member'
            AND (chat_type IN ('group','supergroup') OR (platform='rubika' AND chat_id LIKE 'g0%'))"
            . ($chat === null ? ' ORDER BY title,chat_id' : ' AND chat_id=?'), $chat === null ? [$provider] : [$provider, $chat])->fetchAll(PDO::FETCH_ASSOC);
    } catch (PDOException $error) {
        if (in_array((string)$error->getCode(), ['42S02','HY000'], true)) return []; // No bot inventory yet.
        throw $error;
    }
}

function cl_rec_provider_request(string $url, array $data): array
{
    $raw = '';
    $curl = curl_init($url);
    curl_setopt_array($curl, [CURLOPT_POST=>true, CURLOPT_POSTFIELDS=>json_encode((object)$data, JSON_THROW_ON_ERROR),
        CURLOPT_HTTPHEADER=>['Content-Type: application/json'], CURLOPT_PROTOCOLS=>CURLPROTO_HTTPS, CURLOPT_FOLLOWLOCATION=>false,
        CURLOPT_CONNECTTIMEOUT_MS=>1800, CURLOPT_TIMEOUT_MS=>3500, CURLOPT_WRITEFUNCTION=>static function ($unused, $bytes) use (&$raw) {
            if (strlen($raw) + strlen($bytes) > 131072) return 0;
            $raw .= $bytes;
            return strlen($bytes);
        }]);
    $ok = curl_exec($curl);
    $code = curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
    // Never include provider responses, URLs or tokens in an error.
    $reply = $ok && $code === 200 ? json_decode($raw, true, 24, JSON_BIGINT_AS_STRING) : null;
    if (!is_array($reply)) throw new ClassroomError(502, 'پیام‌رسان ارتباط ربات با این گروه را تأیید نکرد.');
    return $reply;
}

function cl_rec_scalar_id(mixed $value): string
{
    return is_string($value) || is_int($value) ? (string)$value : '';
}

function cl_rec_verify_group(string $provider, array $bot, string $id, ?callable $request = null): string
{
    $request ??= 'cl_rec_provider_request';
    $reply = $request($bot['prefix'] . 'getChat', ['chat_id'=>$id]);
    $info = ($reply['ok'] ?? null) === true ? ($reply['result'] ?? null) : null;
    if ($provider === 'rubika') $info = ($reply['status'] ?? null) === 'OK' ? ($reply['data']['chat'] ?? null) : null;
    $type = is_array($info) ? ($info[$provider === 'rubika' ? 'chat_type' : 'type'] ?? '') : '';
    if (!is_string($type) || !in_array(strtolower($type), ['group','supergroup'], true)
        || cl_rec_scalar_id($info[$provider === 'rubika' ? 'chat_id' : 'id'] ?? null) !== $id) {
        throw new ClassroomError(409, 'نشانی گروه یا دسترسی ربات به آن تأیید نشد.');
    }
    // Telegram supports checking the bot itself even as an ordinary member. Bale documents
    // getChatMember as admin-only; Rubika has no equivalent. Neither is an admin prerequisite.
    if ($provider === 'telegram') {
        $self = $request($bot['prefix'] . 'getMe', []);
        $user = ($self['ok'] ?? null) === true ? cl_rec_scalar_id($self['result']['id'] ?? null) : '';
        if (!preg_match('/^[1-9][0-9]{0,19}$/D', $user)) throw new ClassroomError(409, 'هویت ربات تأیید نشد.');
        $member = $request($bot['prefix'] . 'getChatMember', ['chat_id'=>$id,'user_id'=>$user]);
        $record = ($member['ok'] ?? null) === true ? ($member['result'] ?? []) : [];
        $role = $record['status'] ?? null;
        if (cl_rec_scalar_id($record['user']['id'] ?? null) !== $user || (!in_array($role, ['member','administrator','creator'], true)
            && !($role === 'restricted' && ($record['is_member'] ?? null) === true))) {
            throw new ClassroomError(409, 'عضویت ربات در این گروه تأیید نشد.');
        }
    }
    return cl_safe_label(is_string($info['title'] ?? null) ? $info['title'] : '', 120) ?: $id;
}

function cl_rec_link_key(array $class): string
{
    return $class['year'] . ':' . $class['id'];
}

function cl_rec_link(ClassroomStore $s, array $class): ?array
{
    $link = $s->get('record-links', cl_rec_link_key($class));
    return ($link['class_key'] ?? null) === $class['key'] ? $link : null;
}

function cl_rec_set_link(ClassroomStore $s, array $cfg, array $class, string $choice, ?callable $request = null): void
{
    $link = null;
    if ($choice !== '') {
        [$provider,$id] = array_pad(explode(':', $choice, 2), 2, '');
        if (!cl_rec_chat($provider, $id) || !cl_rec_inventory($s->db, $provider, $id) || !($bot = cl_rec_bot($provider))) {
            throw new ClassroomError(409, 'یکی از گروه‌های ثبت‌شدهٔ ربات‌های همین مدرسه را انتخاب کن.');
        }
        $link = ['provider'=>$provider,'chat'=>$id,'title'=>cl_rec_verify_group($provider, $bot, $id, $request),
            'bot'=>$bot['revision'],'class_key'=>$class['key'],'rev'=>bin2hex(random_bytes(16))];
    }
    cl_class_lock($s, $cfg, $class['id'], static function () use ($s, $class, $link) {
        $year = cl_current_year($s->db);
        if ($class !== (cl_classes($s->db, $year)[$class['id']] ?? null)) throw new ClassroomError(409, 'کلاس یا سال جاری تغییر کرده است.');
        if ($link !== null && ((!cl_rec_inventory($s->db, $link['provider'], $link['chat']))
            || (cl_rec_bot($link['provider'])['revision'] ?? null) !== $link['bot'])) {
            throw new ClassroomError(409, 'ربات یا فهرست گروه‌ها تغییر کرده است؛ دوباره بررسی کن.');
        }
        $slot = $s->get('record-slot', 'current');
        $job = $slot ? $s->get('recording', $slot['id']) : null;
        if (($job['class'] ?? null) === $class['id'] && ($job['flight']['until'] ?? 0) > time()) {
            throw new ClassroomError(409, 'یک بخش در حال ارسال است؛ پس از مشخص‌شدن نتیجه، گروه را تغییر بده.');
        }
        if ($link === null) $s->delete('record-links', cl_rec_link_key($class));
        else {
            $previous = cl_rec_link($s, $class);
            $fields = array_flip(['provider','chat','title','bot','class_key']);
            if (!$previous || array_intersect_key($previous, $fields) !== array_intersect_key($link, $fields)) {
                $s->put('record-links', cl_rec_link_key($class), $link);
            }
        }
    });
}

function cl_rec_operator(array $lesson, array $actor): bool
{
    // cl_school_user only marks active admins with classroom access as office=admin.
    return match ($actor['role'] ?? null) {
        'admin' => ($actor['office'] ?? null) === 'admin',
        'teacher' => $actor['id'] === $lesson['teacher'],
        default => false,
    };
}

function cl_rec_health(ClassroomStore $s, array $cfg, int $now): bool
{
    return cl_rec_enabled($cfg) && ($s->get('record-worker', 'heartbeat')['at'] ?? 0) >= $now - 18;
}

function cl_rec_terminal(ClassroomStore $s, array $job, string $state, string $reason = ''): void
{
    $write = static function () use ($s, $job, $state, $reason) {
        $s->put('recording', $job['id'], array_replace($job, ['state'=>$state,'reason'=>$reason]));
        if (($s->get('record-slot', 'current')['id'] ?? null) === $job['id']) $s->delete('record-slot', 'current');
    };
    if ($s->db->inTransaction()) $write();
    else $s->tx($write);
}

function cl_rec_history(array $class): string
{
    return 'record-list:' . cl_rec_link_key($class);
}

function cl_rec_start(ClassroomStore $s, array $cfg, array $class, array $lesson, array $actor, int $now): array
{
    if (!cl_rec_enabled($cfg) || cl_is_friday_test($lesson) || !cl_rec_operator($lesson, $actor)) {
        throw new ClassroomError(403, 'شروع ضبط فقط در زنگ عادی و با فعال‌سازی مدرسه برای دبیر همان زنگ یا مدیر مجاز امکان‌پذیر است.');
    }
    if ($lesson['deadline'] - $now > CL_MAX_LESSON_SECONDS) throw new ClassroomError(409, 'ضبط آزمایشی برای زنگِ حداکثر دو ساعته است.');
    if (isset($lesson['recorder'])) return cl_rec_status($s, $cfg, $lesson, $actor);
    $link = cl_rec_link($s, $class);
    if (!$link || (cl_rec_bot($link['provider'])['revision'] ?? null) !== $link['bot']
        || !cl_rec_inventory($s->db, $link['provider'], $link['chat'])) {
        throw new ClassroomError(409, 'مدیر مدرسه باید برای این کلاس یک گروه معتبر پیام‌رسان ثبت کند.');
    }
    cl_room_lock($s->db, $cfg['room_prefix'] . ':recorder', static function () use ($s, $cfg, $class, $lesson, $link, $now) {
        if ($s->get('record-slot', 'current')) throw new ClassroomError(409, 'سرویس ضبط در حال ضبط یا ارسال زنگ دیگری است.');
        if (!cl_rec_health($s, $cfg, $now)) throw new ClassroomError(503, 'سرویس ضبط VPS آماده نیست. مدیر مدرسه وضعیت سرویس را بررسی کند.');
        $s->tx(static function () use ($s, $class, $lesson, $link, $now) {
            $id = $lesson['id'];
            $s->put('recording', $id, ['id'=>$id,'class'=>$lesson['class_id'],'year'=>$lesson['year'], 'target'=>$link,
                'teacher'=>$lesson['teacher'],'begin'=>$now + 5, 'deadline'=>$lesson['deadline'],'expires'=>$lesson['deadline'] + CL_REC_RETENTION,
                'state'=>'recording','live'=>false,'sent'=>0,'total'=>0]);
            $s->put('lesson', $id, $lesson + ['recorder'=>'recorder-' . $id]);
            $s->put('record-slot', 'current', ['id'=>$id]);
            $s->put(cl_rec_history($class), sprintf('%012d:%s', $now, $id), ['id'=>$id]);
        });
    });
    return cl_rec_status($s, $cfg, $s->get('lesson', $lesson['id']), $actor);
}

function cl_rec_status(ClassroomStore $s, array $cfg, array $lesson, array $actor): array
{
    $job = isset($lesson['recorder']) ? $s->get('recording', $lesson['id']) : null;
    $on = ($job['state'] ?? null) === 'recording' && $lesson['phase'] !== 'closing';
    $staff = cl_is_staff($actor);
    $operator = cl_rec_operator($lesson, $actor);
    $out = $on ? ['notice'=>($job['live'] ?? false) && $lesson['phase'] === 'live' ? 'live' : 'preparing'] : [];
    if ($staff && $job) $out += ['id'=>$job['id'],'confirm'=>$on,'state'=>$job['state'], 'canSend'=>$operator,
        'group'=>$job['target']['title'],'messenger'=>CL_REC_PROVIDERS[$job['target']['provider']]];
    if ($staff && ($job['state'] ?? null) === 'discarded' && ($job['reason'] ?? '') !== '') $out['notice'] = 'stopped';
    if (!$job && $operator && !cl_is_friday_test($lesson) && cl_rec_enabled($cfg)) $out['start'] = true;
    return ['recording'=>$out ?: null];
}

/** Save consent before attempting room retirement, but never deliver before the existing close commits. */
function cl_rec_close_choice(ClassroomStore $s, array $cfg, array $lesson, array $actor, array $body, int $now): ?string
{
    $job = isset($lesson['recorder']) ? $s->get('recording', $lesson['id']) : null;
    if (($job['state'] ?? null) !== 'recording') return null;
    if ($lesson['phase'] === 'closing') return ($lesson['recording_consent'] ?? false) ? 'queued' : 'discarded';
    if (cl_lesson_expired($cfg, $lesson, $now)) return 'discarded';
    if (($body['recording'] ?? null) !== $job['id'] || !is_bool($body['sendRecording'] ?? null)) {
        throw new ClassroomError(409, 'برای بستن کلاس، تکلیف ارسال فیلم را با «بله» یا «خیر» مشخص کن.');
    }
    if ($body['sendRecording'] && !cl_rec_operator($lesson, $actor)) throw new ClassroomError(403, 'فقط دبیر همین زنگ یا مدیر مجاز می‌تواند ارسال فیلم را تأیید کند.');
    // Check the bound destination both now and when dispatching each part. No automatic rerouting.
    return $body['sendRecording'] && cl_rec_enabled($cfg) && cl_rec_destination($s, $job) !== null ? 'queued' : 'discarded';
}

function cl_rec_finish(ClassroomStore $s, array $cfg, array $lesson): void
{
    if (!isset($lesson['recorder']) || !($job = $s->get('recording', $lesson['id'])) || $job['state'] !== 'recording') return;
    if (($lesson['recording_consent'] ?? false) && cl_rec_enabled($cfg)) {
        $s->put('recording', $job['id'], array_replace($job, ['state'=>'queued','live'=>false]));
    } else cl_rec_terminal($s, $job, 'discarded');
}

function cl_rec_destination(ClassroomStore $s, array $job): ?array
{
    try { $year = cl_current_year($s->db); } catch (ClassroomError $error) { return null; }
    $class = $year === $job['year'] ? (cl_classes($s->db, $year)[$job['class']] ?? null) : null;
    $link = $class ? cl_rec_link($s, $class) : null;
    if ($link === null || $link !== $job['target'] || !cl_rec_inventory($s->db, $link['provider'], $link['chat'])) return null;
    $bot = cl_rec_bot($link['provider']);
    return ($bot['revision'] ?? null) === $link['bot'] ? $bot : null;
}

function cl_rec_capture_room(array $cfg, array $job, ?array $lesson, int $now): bool
{
    return $job['state'] === 'recording' && $now >= $job['begin'] && $lesson && $lesson['id'] === $job['id']
        && !cl_is_friday_test($lesson) && is_string($lesson['room']) && cl_room_class($cfg, $lesson['room']) === $job['class']
        && cl_lesson_live($lesson, $now);
}

function cl_rec_worker_auth(array $cfg, mixed $authorization): void
{
    if (!cl_rec_enabled($cfg) || !is_string($authorization) || !hash_equals('Bearer ' . $cfg['recording_secret'], $authorization)) {
        throw new ClassroomError(403, 'درخواست سرویس ضبط معتبر نیست.');
    }
}

function cl_rec_worker(ClassroomStore $s, array $cfg, array $input, int $now, callable $rpc, ?callable $request = null): array
{
    $action = $input['action'] ?? null;
    $worker = $input['worker'] ?? null;
    $id = $input['id'] ?? null;
    if (($input['protocol'] ?? null) !== CL_REC_PROTOCOL || !cl_rec_id($worker) || ($id !== null && !cl_rec_id($id))) {
        throw new ClassroomError(400, 'نسخه یا شناسهٔ سرویس ضبط معتبر نیست.');
    }
    if ($action === 'poll') {
        if (($input['available'] ?? false) === true) $s->put('record-worker', 'heartbeat', ['at'=>$now]);
        else $s->delete('record-worker', 'heartbeat');
        $id ??= $s->get('record-slot', 'current')['id'] ?? null;
    }
    $record = $id !== null ? $s->get('recording', $id) : null;
    if (!$record) {
        if ($action !== 'poll') throw new ClassroomError(410, 'ضبط در دسترس نیست.');
        return ['command'=>$id === null ? 'idle' : 'purge','id'=>$id];
    }
    $bot = null;
    $verified = false;
    // No provider network calls under the classroom's media/attendance lock.
    if ($action === 'claim') {
        if (($record['worker'] ?? null) !== $worker || !cl_rec_claimable($record, $input, $now)) {
            throw new ClassroomError(409, 'مجوز ارسال این بخش معتبر نیست.');
        }
        $bot = cl_rec_destination($s, $record);
        if ($bot) {
            try {
                cl_rec_verify_group($record['target']['provider'], $bot, $record['target']['chat'], $request);
                $verified = true;
            } catch (ClassroomError $error) { /* A safe, bounded retry; no media was sent. */ }
        }
    }
    return cl_class_lock($s, $cfg, $record['class'], static function () use ($s, $cfg, $record, $input, $now, $rpc, $bot, $verified, $action, $worker, $id) {
        $job = $s->get('recording', $id) ?? throw new ClassroomError(404, 'ضبط در دسترس نیست.');
        if ($action === 'poll' && !isset($job['worker'])) {
            $job['worker'] = $worker;
            $s->put('recording', $id, $job);
        }
        if (($job['worker'] ?? null) !== $worker) throw new ClassroomError(409, 'ضبط به سرویس دیگری اختصاص دارد.');
        if ($action === 'result') return cl_rec_result($s, $job, $input, $now);
        if ($action === 'cleaned') {
            if (!in_array($job['state'], ['discarded','delivered','failed','uncertain'], true)) throw new ClassroomError(409, 'ضبط هنوز پایان نیافته است.');
            if (!isset($job['cleaned'])) $s->put('recording', $id, $job + ['cleaned'=>$now]);
            return [];
        }
        if ($action === 'fault' && in_array($job['state'], ['recording','queued'], true) && !isset($job['flight'])) {
            cl_rec_terminal($s, $job, $job['state'] === 'recording' ? 'discarded' : 'failed', 'capture');
            return [];
        }
        if ($action === 'claim') return cl_rec_claim($s, $job, $input, $now, $bot, $verified);
        $lesson = cl_active($s, $record['class']);
        if ($action === 'board' || $action === 'document') {
            if (($input['room'] ?? null) !== ($lesson['room'] ?? null) || !cl_rec_capture_room($cfg, $job, $lesson, $now)) {
                throw new ClassroomError(409, 'اتاق ضبط فعال نیست.');
            }
            $state = cl_activity($s, $id);
            if ($action === 'board') {
                $view = cl_board_view($s, $id, ['role'=>'student','identity'=>'recorder-' . $id], $input, $state);
                foreach ($view['ops'] ?? [] as $i=>$op) unset($view['ops'][$i]['author']);
                return ['board'=>$view];
            }
            $doc = $input['doc'] ?? null;
            if (!cl_rec_id($doc) || $state['doc'] !== $doc) throw new ClassroomError(404, 'فایل فعلی تخته پیدا نشد.');
            return ['file'=>cl_board_file($lesson, $state, $doc)];
        }
        if ($action !== 'poll') throw new ClassroomError(400, 'عملیات سرویس ضبط معتبر نیست.');
        if ($job['state'] === 'recording') {
            if ($lesson && $lesson['id'] === $id && ($lesson['phase'] === 'closing' || cl_lesson_expired($cfg, $lesson, $now))) {
                if ($now - ($lesson['last_attempt'] ?? 0) >= CL_SAMPLE_INTERVAL) {
                    try { cl_close_lesson($s, $cfg, $lesson, $now, $rpc); }
                    catch (ClassroomError $error) { return ['command'=>'hold','id'=>$id]; }
                }
                $job = $s->get('recording', $id);
            } elseif (!$lesson || $lesson['id'] !== $id || cl_rec_destination($s, $job) === null) {
                cl_rec_terminal($s, $job, 'discarded', 'destination');
                $job['state'] = 'discarded';
            } elseif (cl_rec_capture_room($cfg, $job, $lesson, $now)) {
                $running = ($input['running'] ?? null) === $lesson['room'];
                if ($running !== $job['live']) $s->put('recording', $id, array_replace($job, ['live'=>$running]));
                $cmd = ['command'=>'record','id'=>$id,'room'=>$lesson['room'],'expires'=>$job['expires'],
                    'lease'=>min($now + 8, $lesson['deadline'])];
                if (!$running) $cmd['token'] = cl_jwt($cfg, ['roomJoin'=>true, 'room'=>$lesson['room'], 'canSubscribe'=>true,
                    'canPublish'=>false,'canPublishData'=>false,'canUpdateOwnMetadata'=>false,'hidden'=>true,'recorder'=>true],
                    25, ['identity'=>'recorder-' . $id, 'name'=>'Recorder'], $now);
                return $cmd;
            } elseif ($job['live']) $s->put('recording', $id, array_replace($job, ['live'=>false]));
        }
        return cl_rec_command($s, $job, $now);
    });
}

function cl_rec_command(ClassroomStore $s, array $job, int $now): array
{
    $out = ['id'=>$job['id'],'expires'=>$job['expires'],'sent'=>$job['sent'],'state'=>$job['state']];
    if ($job['state'] === 'recording') return ['command'=>'hold'] + $out;
    if ($job['state'] === 'queued') {
        if (isset($job['flight']) && $job['flight']['until'] <= $now) {
            cl_rec_terminal($s, $job, 'uncertain', 'receipt');
            $job['state'] = $out['state'] = 'uncertain';
        } elseif ($job['expires'] <= $now) {
            cl_rec_terminal($s, $job, 'failed', 'expired');
            $job['state'] = $out['state'] = 'failed';
        } else return ['command'=>isset($job['flight']) || ($job['retry_at'] ?? 0) > $now ? 'hold' : 'deliver'] + $out;
    }
    return ['command'=>in_array($job['state'], ['delivered','discarded'], true) || $job['expires'] <= $now ? 'purge' : 'retain'] + $out;
}

function cl_rec_retry(ClassroomStore $s, array $job, int $now, string $reason): void
{
    $job['tries'] = ($job['tries'] ?? 0) + 1;
    if ($job['tries'] >= 3) cl_rec_terminal($s, $job, 'failed', $reason);
    else $s->put('recording', $job['id'], array_replace($job, ['retry_at'=>$now + 20 * $job['tries']]));
}

function cl_rec_claimable(array $job, array $input, int $now): bool
{
    $part = $input['part'] ?? null;
    $total = $input['total'] ?? null;
    return $job['state'] === 'queued' && !isset($job['flight']) && ($job['retry_at'] ?? 0) <= $now && $job['expires'] > $now
        && $part === $job['sent'] + 1 && is_int($total) && $total >= $part && $total <= CL_REC_MAX_PARTS
        && ($job['total'] === 0 || $job['total'] === $total);
}

function cl_rec_claim(ClassroomStore $s, array $job, array $input, int $now, ?array $bot, bool $verified): array
{
    if (!cl_rec_claimable($job, $input, $now)) throw new ClassroomError(409, 'مجوز ارسال این بخش معتبر نیست.');
    $part = $input['part'];
    $total = $input['total'];
    $lesson = $s->get('lesson', $job['id']);
    if (($lesson['phase'] ?? null) !== 'finished' || !$bot || cl_rec_destination($s, $job) !== $bot) {
        cl_rec_terminal($s, $job, 'discarded', 'destination');
        return ['command'=>'purge'];
    }
    if (!$verified) {
        cl_rec_retry($s, $job, $now, 'destination');
        return ['command'=>'hold'];
    }
    $target = $job['target'];
    $attempt = bin2hex(random_bytes(16));
    $job['total'] = $total;
    unset($job['retry_at']);
    // Reserve before handing out any credential. A lost response cannot cause a second send.
    $job['flight'] = ['part'=>$part,'attempt'=>$attempt,'until'=>$now + 240];
    $s->put('recording', $job['id'], $job);
    $caption = cl_safe_label('فیلم کلاس ' . $lesson['class_name'] . ' · ' . $lesson['subject'] . ' · ' . cl_display_day($lesson['date']), 180);
    if ($total > 1) $caption .= " · بخش $part از $total";
    return ['command'=>'send','attempt'=>$attempt,'provider'=>$target['provider'],'chat'=>$target['chat'],'prefix'=>$bot['prefix'],'caption'=>$caption];
}

function cl_rec_result(ClassroomStore $s, array $job, array $input, int $now): array
{
    $attempt = $input['attempt'] ?? null;
    $part = $input['part'] ?? null;
    $result = $input['result'] ?? null;
    $message = $input['message'] ?? '';
    if (!cl_rec_id($attempt) || !is_int($part) || !in_array($result, ['accepted','rejected','retry','uncertain'], true)
        || !is_string($message) || ($result === 'accepted' && !preg_match('/^[A-Za-z0-9_-]{1,96}$/D', $message))) {
        throw new ClassroomError(400, 'رسید ارسال معتبر نیست.');
    }
    $receipt = ['attempt'=>$attempt,'part'=>$part,'result'=>$result,'message'=>$result === 'accepted' ? $message : ''];
    if (($job['last'] ?? null) === $receipt) return [];
    if (!in_array($job['state'], ['queued','uncertain'], true) || ($job['flight']['attempt'] ?? null) !== $attempt
        || $part !== $job['flight']['part'] || $part !== $job['sent'] + 1) throw new ClassroomError(410, 'این رسید برای ارسال جاری نیست.');
    // A persisted receipt can settle a late part but cannot restart a terminal job.
    $terminal = $job['state'] === 'uncertain';
    if ($terminal && $now >= $job['expires']) throw new ClassroomError(410, 'مهلت تسویهٔ این ارسال پایان یافته است.');
    unset($job['flight']);
    $job['last'] = $receipt;
    if ($result === 'accepted') {
        $job['sent'] = $part;
        unset($job['tries'], $job['retry_at']);
        if ($part === $job['total']) cl_rec_terminal($s, $job, 'delivered');
        else $s->put('recording', $job['id'], $job);
    } elseif ($terminal) $s->put('recording', $job['id'], $job);
    elseif ($result === 'retry') cl_rec_retry($s, $job, $now, 'provider');
    else cl_rec_terminal($s, $job, $result === 'uncertain' ? 'uncertain' : 'failed', 'provider');
    return [];
}
