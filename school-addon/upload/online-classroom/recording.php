<?php
declare(strict_types=1);

// Job mutations hold the class lock (or the exclusive plugin lifecycle lock).
// The separate recorder lock serializes admission and registration of the one VPS worker.
const CL_REC_PROTOCOL = 2;
const CL_REC_RELEASE = '0.4.23';
const CL_REC_RETENTION = 86400;
// The job/lease protocol stays at 2. Only discovery and admission negotiate the paged format.
const CL_REC_PAGED_PLAN = 3;
const CL_REC_PAGE = 128;
const CL_REC_DELAY = 180; // Minutes after confirmed closure, not the timetable deadline.

function cl_rec_delay(array $cfg): int
{
    $value = $cfg['recording_delay_minutes'] ?? null;
    return is_int($value) && $value >= 1 && $value <= 720 ? $value : CL_REC_DELAY;
}
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
        foreach (cl_rec_pending($s) as $job) {
            if ($job['class'] === $class['id'] && ($job['flight']['until'] ?? 0) > time()) {
                throw new ClassroomError(409, 'یک بخش در حال ارسال است؛ پس از مشخص‌شدن نتیجه، گروه را تغییر بده.');
            }
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
    $beat = $s->get('record-worker', 'heartbeat');
    return cl_rec_enabled($cfg) && ($beat['protocol'] ?? null) === CL_REC_PROTOCOL
        && ($beat['available'] ?? false) === true && ($beat['at'] ?? 0) >= $now - 18;
}

function cl_rec_pending_page(ClassroomStore $s, string $cursor = '', int $limit = CL_REC_PAGE): array
{
    return $s->linkedRows('record-pending', 'recording', $limit, $cursor);
}

function cl_rec_pending(ClassroomStore $s): Generator
{
    $cursor = '';
    while ($rows = cl_rec_pending_page($s, $cursor)) {
        foreach ($rows as $id=>$job) if (($job['id'] ?? null) === $id) yield $id=>$job;
        $cursor = (string)array_key_last($rows);
    }
}

function cl_rec_terminal(ClassroomStore $s, array $job, string $state, string $reason = ''): void
{
    $write = static function () use ($s, $job, $state, $reason) {
        $s->put('recording', $job['id'], array_replace($job, ['state'=>$state,'reason'=>$reason,'live'=>false]));
        $s->delete('record-pending', $job['id']);
        if (($s->get('record-slot', 'current')['id'] ?? null) === $job['id']) $s->delete('record-slot', 'current');
    };
    if ($s->db->inTransaction()) $write();
    else $s->tx($write);
}

function cl_rec_history(array $class): string
{
    return 'record-list:' . cl_rec_link_key($class);
}

function cl_rec_title(ClassroomStore $s, array $lesson): array
{
    return ['school'=>cl_school_name($s->db), 'subject'=>cl_safe_label($lesson['subject'] ?? '', 100)];
}

function cl_rec_start(ClassroomStore $s, array $cfg, array $class, array $lesson, array $actor, int $now): array
{
    if (!cl_rec_enabled($cfg) || !cl_rec_operator($lesson, $actor)) {
        throw new ClassroomError(403, 'شروع ضبط فقط برای دبیر همین زنگ، دبیرِ آغازکنندهٔ آزمایش یا مدیر مجاز امکان‌پذیر است.');
    }
    $friday = cl_is_friday_test($lesson);
    // The grant can cover all Friday, but the Recorder's input/retention budgets cannot.
    $deadline = $friday ? min($lesson['deadline'], $now + CL_MAX_LESSON_SECONDS) : $lesson['deadline'];
    if (cl_lesson_expired($cfg, $lesson, $now) || $now + 5 >= $deadline) throw new ClassroomError(409, 'برای شروع ضبط زمان کافی باقی نمانده است.');
    if ($deadline - $now > CL_MAX_LESSON_SECONDS) throw new ClassroomError(409, 'ضبط برای زنگِ حداکثر دو ساعته است.');
    if (isset($lesson['recorder'])) return cl_rec_status($s, $cfg, $lesson, $actor);
    $link = cl_rec_link($s, $class);
    if (!$link || (cl_rec_bot($link['provider'])['revision'] ?? null) !== $link['bot']
        || !cl_rec_inventory($s->db, $link['provider'], $link['chat'])) {
        throw new ClassroomError(409, 'مدیر مدرسه باید برای این کلاس یک گروه معتبر پیام‌رسان ثبت کند.');
    }
    cl_room_lock($s->db, $cfg['room_prefix'] . ':recorder', static function () use ($s, $cfg, $class, $lesson, $link, $now, $deadline, $friday) {
        $beat = $s->get('record-worker', 'heartbeat');
        if (($beat['protocol'] ?? null) !== CL_REC_PROTOCOL || ($beat['at'] ?? 0) < $now - 18) {
            throw new ClassroomError(503, 'سرویس ضبط VPS آماده نیست. مدیر مدرسه وضعیت سرویس را بررسی کند.');
        }
        if (($beat['plan_format'] ?? null) !== CL_REC_PAGED_PLAN) {
            throw new ClassroomError(503, 'برای شروع ضبط تازه، Recorder روی VPS را هم به نسخهٔ هماهنگ به‌روزرسانی کنید.');
        }
        if (($beat['capture_space'] ?? null) === false) {
            throw new ClassroomError(503, 'فضای موقت VPS برای شروع ضبط تازه کافی نیست.');
        }
        if (($beat['available'] ?? false) !== true) throw new ClassroomError(503, 'سرویس ضبط VPS آماده نیست. مدیر مدرسه وضعیت سرویس را بررسی کند.');
        // Pin the displayed names once so every part and reconnect of this recording agrees.
        $title = cl_rec_title($s, $lesson);
        $worker = $beat['worker'];
        $s->tx(static function () use ($s, $class, $lesson, $link, $now, $title, $worker, $deadline, $friday) {
            $id = $lesson['id'];
            $s->put('recording', $id, ['id'=>$id,'class'=>$lesson['class_id'],'year'=>$lesson['year'], 'target'=>$link,
                'teacher'=>$lesson['teacher'],'title'=>$title,'begin'=>$now + 5, 'deadline'=>$deadline,'expires'=>$deadline + CL_REC_RETENTION,
                'state'=>'recording','live'=>false,'sent'=>0,'total'=>0,'worker'=>$worker,'protocol'=>CL_REC_PROTOCOL,
                'plan_format'=>CL_REC_PAGED_PLAN]
                + ($friday ? ['friday'=>true] : []));
            $s->put('lesson', $id, $lesson + ['recorder'=>'recorder-' . $id]);
            $s->put('record-pending', $id, ['id'=>$id]);
            $s->put(cl_rec_history($class), sprintf('%012d:%s', $now, $id), ['id'=>$id]);
        });
    });
    return cl_rec_status($s, $cfg, $s->get('lesson', $lesson['id']), $actor);
}

function cl_rec_source_prefs(array $job, array $lesson): array
{
    $epoch = cl_epoch($lesson);
    return isset($job['source_prefs']['epoch']) && $job['source_prefs']['epoch'] === $epoch
        ? $job['source_prefs'] : ['epoch'=>$epoch];
}

function cl_rec_camera_eligible(array $cfg, array $lesson, array $choice): bool
{
    $id = $choice['identity'];
    return cl_media_identity($id) && cl_sid($choice['sid']) && cl_sid($choice['track']) && str_starts_with($choice['track'], 'TR_')
        && ($lesson['seen'][$id] ?? null) === $choice['sid'] && cl_webcam_codec($cfg, $lesson, $id) !== null
        && cl_access_enabled($lesson, 'camera', $id);
}

function cl_rec_camera_published(array $lesson, array $choice, callable $rpc): bool
{
    // Only an explicit selection queries the SFU. Board/worker polling stays unchanged.
    $room = $lesson['room'];
    $peers = cl_live_media_peers($rpc('ListParticipants', $room, ['room'=>$room]), $lesson['recorder']);
    $peer = $peers[$choice['identity']] ?? [];
    if (($peer['sid'] ?? null) !== $choice['sid'] || !is_array($peer['tracks'] ?? null)) return false;
    foreach ($peer['tracks'] as $track) {
        if (is_array($track) && ($track['sid'] ?? null) === $choice['track']
            && cl_track_source($track['source'] ?? null) === 'CAMERA' && ($track['muted'] ?? false) === false) return true;
    }
    return false;
}

function cl_rec_layout(array $cfg, array $job, array $lesson, bool $worker = false): array
{
    $prefs = cl_rec_source_prefs($job, $lesson);
    $owner = cl_screen_state($cfg, $lesson);
    $screen = $prefs['screen'] ?? null;
    $camera = $prefs['camera'] ?? null;
    return ['board'=>($prefs['board'] ?? false) === true, 'screen'=>$screen && $screen['owner'] === $owner ? $screen['track'] : null]
        + (($job['camera_layout'] ?? false) === true
            ? ['camera'=>$camera && cl_rec_camera_eligible($cfg, $lesson, $camera) ? $camera : null] : [])
        + ($worker ? ['screenOwner'=>$owner] : []);
}

// Called only with a confirmed participant, a checked transport and the class lock held.
function cl_rec_set_source(ClassroomStore $s, array $cfg, array $lesson, array $actor, array $body, int $now, ?callable $rpc = null): array
{
    cl_require_board_control($lesson, $actor, $body, $now);
    $job = isset($lesson['recorder']) ? $s->get('recording', $lesson['id']) : null;
    if (!cl_rec_enabled($cfg) || ($job['state'] ?? null) !== 'recording' || $job['deadline'] <= $now
        || ($job['layout_capable'] ?? false) !== true) {
        throw new ClassroomError(409, 'کنترل نمای فیلم فقط در ضبط فعال و با نسخهٔ سازگار در دسترس است.');
    }
    $source = $body['source'] ?? null;
    $enabled = $body['enabled'] ?? null;
    if (!in_array($source, ['board','screen','camera'], true) || !is_bool($enabled)) {
        throw new ClassroomError(400, 'منبع یا وضعیت نمایش در ضبط معتبر نیست.');
    }
    $prefs = cl_rec_source_prefs($job, $lesson);
    if ($source === 'board') $prefs['board'] = $enabled;
    elseif ($source === 'screen') {
        $owner = cl_screen_state($cfg, $lesson);
        $track = $body['track'] ?? null;
        if (!$owner || $owner['sid'] !== ($body['publisherSid'] ?? null) || !cl_sid($track) || !str_starts_with($track, 'TR_')) {
            throw new ClassroomError(409, 'صفحهٔ اشتراکی عوض شده است؛ نمای کلاس را دوباره بررسی کن.');
        }
        // A new publication (even by the same account) must not inherit a prior click.
        // The recorder compares this SID to its actual subscribed LiveKit screen track.
        $choice = ['owner'=>$owner,'track'=>$track];
        if ($enabled) $prefs['screen'] = $choice;
        elseif (($prefs['screen'] ?? null) === $choice) unset($prefs['screen']);
    } else {
        if (($job['camera_layout'] ?? false) !== true) {
            throw new ClassroomError(409, 'نسخهٔ سازندهٔ فیلم از نمایش بزرگ وب‌کم پشتیبانی نمی‌کند.');
        }
        $choice = ['identity'=>$body['identity'] ?? null,'sid'=>$body['publisherSid'] ?? null,'track'=>$body['track'] ?? null];
        if ($enabled) {
            if (!$rpc || !cl_rec_camera_eligible($cfg, $lesson, $choice) || !cl_rec_camera_published($lesson, $choice, $rpc)) {
                throw new ClassroomError(409, 'وب‌کم خاموش شده، عوض شده یا مجاز نیست؛ دوباره بررسی کن.');
            }
            $prefs['camera'] = $choice;
        } elseif (($prefs['camera'] ?? null) === $choice) unset($prefs['camera']);
    }
    $job['source_prefs'] = $prefs;
    $s->put('recording', $job['id'], $job);
    return ['archive'=>cl_rec_layout($cfg, $job, $lesson)];
}

function cl_rec_status(ClassroomStore $s, array $cfg, array $lesson, array $actor, bool $archiveControl = false): array
{
    $job = isset($lesson['recorder']) ? $s->get('recording', $lesson['id']) : null;
    $on = ($job['state'] ?? null) === 'recording' && $lesson['phase'] !== 'closing';
    $staff = cl_is_staff($actor);
    $operator = cl_rec_operator($lesson, $actor);
    $out = $on ? ['notice'=>($job['live'] ?? false) && $lesson['phase'] === 'live' ? 'live' : 'preparing'] : [];
    if ($staff && $job) $out += ['id'=>$job['id'],'confirm'=>$on,'state'=>$job['state'], 'canSend'=>$operator,
        'group'=>$job['target']['title'],'messenger'=>CL_REC_PROVIDERS[$job['target']['provider']], 'delayMinutes'=>cl_rec_delay($cfg)];
    if ($archiveControl && $on && cl_rec_enabled($cfg) && ($job['layout_capable'] ?? false) === true) {
        $out['archive'] = cl_rec_layout($cfg, $job, $lesson);
    }
    if ($staff && ($job['state'] ?? null) === 'discarded' && ($job['reason'] ?? '') !== '') $out['notice'] = 'stopped';
    if (!$job && $operator && cl_rec_enabled($cfg)) $out['start'] = true;
    return ['recording'=>$out ?: null];
}

/** Save consent before attempting room retirement, but never deliver before the existing close commits. */
function cl_rec_close_choice(ClassroomStore $s, array $cfg, array $lesson, array $actor, array $body, int $now): ?string
{
    $job = isset($lesson['recorder']) ? $s->get('recording', $lesson['id']) : null;
    if (($job['state'] ?? null) !== 'recording') return null;
    if ($lesson['phase'] === 'closing') return ($lesson['recording_consent'] ?? false) ? 'queued' : 'discarded';
    if (cl_lesson_expired($cfg, $lesson, $now) || $now >= $job['deadline']) return 'discarded';
    if (($body['recording'] ?? null) !== $job['id'] || !is_bool($body['sendRecording'] ?? null)) {
        throw new ClassroomError(409, 'برای بستن کلاس، تکلیف ارسال فیلم را با «بله» یا «خیر» مشخص کن.');
    }
    if ($body['sendRecording'] && !cl_rec_operator($lesson, $actor)) throw new ClassroomError(403, 'فقط دبیر همین زنگ یا مدیر مجاز می‌تواند ارسال فیلم را تأیید کند.');
    // Check the bound destination both now and when dispatching each part. No automatic rerouting.
    return $body['sendRecording'] && cl_rec_enabled($cfg) && cl_rec_destination($s, $job) !== null ? 'queued' : 'discarded';
}

function cl_rec_caption(array $lesson): string
{
    $title = cl_is_friday_test($lesson) ? 'فیلم ' . CL_FRIDAY_TITLE . ' · ' . $lesson['class_name']
        : 'فیلم کلاس ' . $lesson['class_name'] . ' · ' . $lesson['subject'];
    return cl_safe_label($title . ' · ' . cl_display_day($lesson['date']), 180);
}

function cl_rec_closed_caption(ClassroomStore $s, array $job): ?string
{
    if (!isset($job['closed_at'])) return null;
    // Practice sessions have no finished lesson row. The caption is committed in
    // the same transaction as confirmed closure; no roster/attendance is retained.
    if (($job['friday'] ?? false) === true) return $job['caption'] ?? null;
    $lesson = $s->get('lesson', $job['id']);
    return ($lesson['phase'] ?? null) === 'finished' ? cl_rec_caption($lesson) : null;
}

function cl_rec_finish(ClassroomStore $s, array $cfg, array $lesson, int $now): void
{
    if (!isset($lesson['recorder']) || !($job = $s->get('recording', $lesson['id'])) || $job['state'] !== 'recording') return;
    if (($lesson['recording_consent'] ?? false) === true && $lesson['started'] !== null && cl_rec_enabled($cfg)
        && ($job['protocol'] ?? null) === CL_REC_PROTOCOL) {
        // Pin the setting at successful close. A later setting change affects only later closures.
        $s->put('recording', $job['id'], array_replace($job, ['state'=>'queued','live'=>false,
            'closed_at'=>$now,'not_before'=>$now + 60 * cl_rec_delay($cfg),'expires'=>min($now, $job['deadline']) + CL_REC_RETENTION]
            + (cl_is_friday_test($lesson) ? ['caption'=>cl_rec_caption($lesson)] : [])));
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
    return $job['state'] === 'recording' && $now >= $job['begin'] && $now < $job['deadline'] && $lesson && $lesson['id'] === $job['id']
        && !cl_lesson_expired($cfg, $lesson, $now) && is_string($lesson['room']) && cl_room_class($cfg, $lesson['room']) === $job['class']
        && cl_lesson_live($lesson, $now);
}

function cl_rec_worker_auth(array $cfg, mixed $authorization): void
{
    if (!cl_rec_enabled($cfg) || !is_string($authorization) || !hash_equals('Bearer ' . $cfg['recording_secret'], $authorization)) {
        throw new ClassroomError(403, 'درخواست سرویس ضبط معتبر نیست.');
    }
}

// An open bell (even a room without recording) has priority. An abandoned, expired
// bell cannot block the queue forever; the ordinary room cleanup still applies.
function cl_rec_offpeak(ClassroomStore $s, array $cfg, int $now): bool
{
    foreach ($s->scan('active') as $row) {
        $lesson = $s->get('lesson', $row['id']);
        if ($lesson && !cl_lesson_expired($cfg, $lesson, $now)) return false;
    }
    return true;
}

function cl_rec_eligible(array $job, int $now): bool
{
    return $job['state'] === 'queued' && isset($job['not_before']) && $job['not_before'] <= $now
        && $job['expires'] > $now && !isset($job['flight']) && ($job['retry_at'] ?? 0) <= $now;
}

function cl_rec_needs_settlement(array $job, int $now): bool
{
    return $job['state'] !== 'recording' && ($job['expires'] <= $now || ($job['flight']['until'] ?? PHP_INT_MAX) <= $now);
}

function cl_rec_discovery(array $rows, string $worker, int $now): array
{
    $capture = $settle = $pending = [];
    foreach ($rows as $id=>$job) {
        if (($job['id'] ?? null) !== $id || ($job['worker'] ?? null) !== $worker) continue;
        $pending[] = $id;
        if ($job['state'] === 'recording') $capture[] = $id;
        elseif (cl_rec_needs_settlement($job, $now)) $settle[] = $id;
    }
    return ['capture'=>$capture,'pending'=>$pending,'settle'=>$settle];
}

function cl_rec_plan_page(ClassroomStore $s, string $worker, int $now, string $cursor = ''): array
{
    $rows = cl_rec_pending_page($s, $cursor);
    // This is a response-size bound, not a queue or capture bound. If the page is full,
    // visit the next one even if this page contained only other/missing records.
    return cl_rec_discovery($rows, $worker, $now) + ['plan_format'=>CL_REC_PAGED_PLAN,
        'next'=>count($rows) === CL_REC_PAGE ? (string)array_key_last($rows) : null];
}

function cl_rec_work(ClassroomStore $s, string $worker, int $now, ?array $current): ?array
{
    if ($current && cl_rec_eligible($current, $now)) return $current;
    $work = null;
    foreach (cl_rec_pending($s) as $job) {
        if (($job['worker'] ?? null) === $worker && cl_rec_eligible($job, $now)
            && ($work === null || [$job['closed_at'],$job['id']] < [$work['closed_at'],$work['id']])) $work = $job;
    }
    return $work;
}

function cl_rec_plan(ClassroomStore $s, array $cfg, array $input, string $worker, int $now): array
{
    $format = $input['plan_format'] ?? 2;
    $currentId = $input['current'] ?? null;
    if (!in_array($format, [2, CL_REC_PAGED_PLAN], true) || ($currentId !== null && !cl_rec_id($currentId))) {
        throw new ClassroomError(400, 'نسخه یا شناسهٔ برنامهٔ ضبط معتبر نیست.');
    }
    $legacy = cl_room_lock($s->db, $cfg['room_prefix'] . ':recorder', static function () use ($s, $input, $worker, $now, $format) {
        $beat = $s->get('record-worker', 'heartbeat');
        if (isset($beat['worker']) && $worker !== $beat['worker']
            && ($beat['at'] >= $now - 18 || $s->rows('record-pending', 1) || $s->get('record-slot', 'current'))) {
            throw new ClassroomError(409, 'این مدرسه به سرویس ضبط دیگری متصل است.');
        }
        $old = null;
        if ($format === 2) {
            // Let a stock Recorder drain its previous jobs during a staged upgrade.
            // Never send it a truncated list or silently switch it over with paged jobs pending.
            $rows = cl_rec_pending_page($s, '', 25);
            $old = cl_rec_discovery($rows, $worker, $now);
            if (count($rows) > 24 || count($old['capture']) > 3 || array_filter($rows, static fn($job) =>
                ($job['plan_format'] ?? null) === CL_REC_PAGED_PLAN)) {
                throw new ClassroomError(409, 'کارهای ضبط به نسخهٔ تازهٔ Recorder نیاز دارند.');
            }
        }
        $s->put('record-worker', 'heartbeat', ['at'=>$now,'worker'=>$worker,'protocol'=>CL_REC_PROTOCOL,'plan_format'=>$format,
            'available'=>($input['available'] ?? false) === true,
            'capture_space'=>is_bool($input['capture_space'] ?? null) ? $input['capture_space'] : null,
            'native_release'=>($input['native_release'] ?? null) === CL_REC_RELEASE ? CL_REC_RELEASE : null]);
        return $old;
    });
    // The global lock protects admission and identity, not a scan of all recorded classes.
    $current = $currentId !== null && $s->get('record-pending', $currentId) ? $s->get('recording', $currentId) : null;
    if (($current['worker'] ?? null) !== $worker) $current = null;
    $work = cl_rec_offpeak($s, $cfg, $now) ? cl_rec_work($s, $worker, $now, $current) : null;
    $permit = ['work'=>$work['id'] ?? null,'lease'=>$work ? min($now + 6, $work['expires']) : 0];
    if ($format === 2) {
        // A closure may have happened after the legacy snapshot. Don't grant work outside that complete plan.
        return $legacy + ($work && !in_array($work['id'], array_merge($legacy['capture'], $legacy['settle']), true)
            && in_array($work['id'], $legacy['pending'], true) ? $permit : ['work'=>null,'lease'=>0]);
    }
    return $permit + ['current_ok'=>$current !== null && $current['state'] === 'queued' && !cl_rec_needs_settlement($current, $now)]
        + cl_rec_plan_page($s, $worker, $now);
}

function cl_rec_worker(ClassroomStore $s, array $cfg, array $input, int $now, callable $rpc, ?callable $request = null): array
{
    $action = $input['action'] ?? null;
    $worker = $input['worker'] ?? null;
    $id = $input['id'] ?? null;
    if (($input['protocol'] ?? null) !== CL_REC_PROTOCOL || !cl_rec_id($worker) || ($id !== null && !cl_rec_id($id))) {
        throw new ClassroomError(400, 'نسخه یا شناسهٔ سرویس ضبط معتبر نیست.');
    }
    if ($action === 'maintenance' && $id === null) {
        // Read-only preflight; never creates a heartbeat, render lease or delivery claim.
        $beat = $s->get('record-worker', 'heartbeat');
        $recent = ($beat['at'] ?? 0) >= $now - 18;
        $same = ($beat['worker'] ?? null) === $worker;
        return ['release'=>CL_REC_RELEASE,
            'idle'=>cl_rec_offpeak($s, $cfg, $now) && !$s->rows('record-pending', 1) && !$s->get('record-slot', 'current'),
            'worker_ok'=>!$recent || $same,
            'native_ready'=>$recent && $same && ($beat['plan_format'] ?? null) === CL_REC_PAGED_PLAN
                && ($beat['native_release'] ?? null) === CL_REC_RELEASE && cl_rec_health($s, $cfg, $now)];
    }
    if ($action === 'plan' && $id === null) return cl_rec_plan($s, $cfg, $input, $worker, $now);
    if ($action === 'plan-page' && $id === null) {
        $cursor = $input['after'] ?? null;
        $beat = $s->get('record-worker', 'heartbeat');
        if (!cl_rec_id($cursor) || ($beat['plan_format'] ?? null) !== CL_REC_PAGED_PLAN
            || ($beat['worker'] ?? null) !== $worker || ($beat['at'] ?? 0) < $now - 18) {
            throw new ClassroomError(409, 'برنامهٔ صفحه‌بندی‌شدهٔ ضبط تأیید نشده است.');
        }
        return cl_rec_plan_page($s, $worker, $now, $cursor);
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
        if (($record['worker'] ?? null) !== $worker || !cl_rec_claimable($record, $input, $now) || !cl_rec_offpeak($s, $cfg, $now)) {
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
        // Never treat an existing v1 MP4 as deferred inputs or permit it to send after upgrade.
        if (($job['protocol'] ?? null) !== CL_REC_PROTOCOL && in_array($job['state'], ['recording','queued'], true)) {
            if ($action !== 'poll' || ($job['worker'] ?? $worker) !== $worker) throw new ClassroomError(409, 'نسخهٔ ضبط سازگار نیست.');
            $job['worker'] = $worker;
            cl_rec_terminal($s, $job, isset($job['flight']) ? 'uncertain' : 'discarded', 'upgrade');
            return ['command'=>isset($job['flight']) ? 'retain' : 'purge','id'=>$id];
        }
        if (($job['worker'] ?? null) !== $worker) throw new ClassroomError(409, 'ضبط به سرویس دیگری اختصاص دارد.');
        if ($action === 'result') return cl_rec_result($s, $job, $input, $now);
        if ($action === 'cleaned') {
            if (!in_array($job['state'], ['discarded','delivered','failed','uncertain'], true)) throw new ClassroomError(409, 'ضبط هنوز پایان نیافته است.');
            if (!isset($job['cleaned'])) $s->put('recording', $id, $job + ['cleaned'=>$now]);
            return [];
        }
        if ($action === 'fault' && in_array($job['state'], ['recording','queued'], true) && !isset($job['flight'])) {
            cl_rec_terminal($s, $job, $job['state'] === 'recording' ? 'discarded' : 'failed',
                ($input['reason'] ?? '') === 'space' ? 'space' : 'capture');
            return [];
        }
        if ($action === 'claim') {
            if (!cl_rec_offpeak($s, $cfg, $now)) return ['command'=>'hold'];
            return cl_rec_claim($s, $job, $input, $now, $bot, $verified);
        }
        if ($action === 'rendered') {
            $total = $input['total'] ?? null;
            if (!cl_rec_eligible($job, $now) || !cl_rec_offpeak($s, $cfg, $now) || cl_rec_closed_caption($s, $job) === null
                || !is_int($total) || $total < 1 || $total > CL_REC_MAX_PARTS || !in_array($job['total'], [0,$total], true)) {
                throw new ClassroomError(409, 'ساخت این فیلم هنوز مجاز نیست یا تعداد بخش‌ها معتبر نیست.');
            }
            if (cl_rec_destination($s, $job) === null) {
                cl_rec_terminal($s, $job, 'discarded', 'destination');
                return ['command'=>'purge'];
            }
            $s->put('recording', $id, array_replace($job, ['total'=>$total, 'rendered_at'=>$job['rendered_at'] ?? $now]));
            return ['command'=>'deliver'];
        }
        $lesson = cl_active($s, $record['class']);
        if ($action === 'board' || $action === 'document') {
            if (($input['room'] ?? null) !== ($lesson['room'] ?? null) || !cl_rec_capture_room($cfg, $job, $lesson, $now)) {
                throw new ClassroomError(409, 'اتاق ضبط فعال نیست.');
            }
            $state = cl_activity($s, $id);
            if ($action === 'board') {
                $view = cl_board_view($s, $id, null, $input, $state, $lesson);
                foreach ($view['ops'] ?? [] as $i=>$op) unset($view['ops'][$i]['author']);
                return ['board'=>$view,'archive'=>cl_rec_layout($cfg, $job, $lesson, true)];
            }
            $doc = $input['doc'] ?? null;
            // Copy a displayed attachment even if the presenter flips to a different file before the fetch.
            if (!cl_rec_id($doc) || !isset($state['docs'][$doc])) throw new ClassroomError(404, 'فایل تختهٔ این زنگ پیدا نشد.');
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
            } elseif ($job['deadline'] <= $now) {
                // A Friday room can continue; the unapproved video inputs cannot.
                cl_rec_terminal($s, $job, 'discarded', 'limit');
                $job['state'] = 'discarded';
            } elseif (!$lesson || $lesson['id'] !== $id || cl_rec_destination($s, $job) === null) {
                cl_rec_terminal($s, $job, 'discarded', 'destination');
                $job['state'] = 'discarded';
            } elseif (cl_rec_capture_room($cfg, $job, $lesson, $now)) {
                $running = ($input['running'] ?? null) === $lesson['room'];
                $layout = true; // Protocol 2 always captures the layout and laser timeline.
                $camera = ($input['archiveCamera'] ?? false) === true;
                if ($running !== $job['live'] || $layout !== ($job['layout_capable'] ?? false) || $camera !== ($job['camera_layout'] ?? false)) {
                    $job = array_replace($job, ['live'=>$running, 'layout_capable'=>$layout, 'camera_layout'=>$camera]);
                    $s->put('recording', $id, $job);
                }
                $cmd = ['command'=>'record','id'=>$id,'room'=>$lesson['room'],'expires'=>$job['expires'],
                    'lease'=>min($now + 8, $lesson['deadline'], $job['deadline'])];
                if (!$running) {
                    $cmd['title'] = $job['title'] ?? cl_rec_title($s, $lesson);
                    if ($layout) {
                        $teacher = $job['teacher'] ?? $lesson['teacher'] ?? 0;
                        $cmd['teacher'] = $teacher > 0 ? 'teacher-' . $teacher : null;
                        $cmd['mediaMode'] = cl_media_mode($lesson);
                        $cmd['archive'] = cl_rec_layout($cfg, $job, $lesson, true);
                    }
                    $cmd['token'] = cl_jwt($cfg, ['roomJoin'=>true, 'room'=>$lesson['room'], 'canSubscribe'=>true,
                        'canPublish'=>false,'canPublishData'=>false,'canUpdateOwnMetadata'=>false,'hidden'=>true,'recorder'=>true],
                        25, ['identity'=>'recorder-' . $id, 'name'=>'Recorder'], $now);
                }
                return $cmd;
            } elseif ($job['live']) $s->put('recording', $id, array_replace($job, ['live'=>false]));
        }
        return cl_rec_command($s, $cfg, $job, $now);
    });
}

function cl_rec_command(ClassroomStore $s, array $cfg, array $job, int $now): array
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
        } elseif ($job['closed_at'] > $now || !cl_rec_eligible($job, $now) || !cl_rec_offpeak($s, $cfg, $now)) {
            return ['command'=>'hold'] + $out;
        } elseif (cl_rec_closed_caption($s, $job) === null || cl_rec_destination($s, $job) === null) {
            cl_rec_terminal($s, $job, 'discarded', 'destination');
            return ['command'=>'purge'] + array_replace($out, ['state'=>'discarded']);
        } else return ['command'=>isset($job['rendered_at']) ? 'deliver' : 'render'] + $out;
    }
    return ['command'=>in_array($job['state'], ['delivered','discarded'], true) || in_array($job['reason'] ?? '', ['capture','space'], true) || $job['expires'] <= $now ? 'purge' : 'retain'] + $out;
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
    return cl_rec_eligible($job, $now) && isset($job['rendered_at'])
        && $part === $job['sent'] + 1 && is_int($total) && $total >= $part && $job['total'] === $total;
}

function cl_rec_claim(ClassroomStore $s, array $job, array $input, int $now, ?array $bot, bool $verified): array
{
    if (!cl_rec_claimable($job, $input, $now)) throw new ClassroomError(409, 'مجوز ارسال این بخش معتبر نیست.');
    $part = $input['part'];
    $total = $input['total'];
    $caption = cl_rec_closed_caption($s, $job);
    if ($caption === null || !$bot || cl_rec_destination($s, $job) !== $bot) {
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
        $job['receipts'][(string)$part] = $message;
        unset($job['tries'], $job['retry_at']);
        if ($part === $job['total']) cl_rec_terminal($s, $job, 'delivered');
        else $s->put('recording', $job['id'], $job);
    } elseif ($terminal) $s->put('recording', $job['id'], $job);
    elseif ($result === 'retry') cl_rec_retry($s, $job, $now, 'provider');
    else cl_rec_terminal($s, $job, $result === 'uncertain' ? 'uncertain' : 'failed', 'provider');
    return [];
}
