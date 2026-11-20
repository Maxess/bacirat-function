<?php
declare(strict_types=1);
require_once __DIR__ . '/activity.php';

function cl_event_json(string $part): mixed
{
    if (!preg_match('/^[A-Za-z0-9_-]+$/D', $part)) return null;
    $raw = base64_decode(strtr($part, '-_', '+/'), true);
    return $raw === false ? null : json_decode($raw, true, 24);
}

/** LiveKit's native callback uses a raw HS256 JWT with the base64 SHA-256 of the exact body. */
function cl_event_verify(array $cfg, string $raw, mixed $authorization, int $now): array
{
    if (!is_string($authorization) || strlen($authorization) > 4096 || count($parts = explode('.', $authorization)) !== 3) {
        throw new ClassroomError(403, 'امضای رویداد رسانه معتبر نیست.');
    }
    [$head, $body, $sig] = $parts;
    $wanted = cl_b64url(hash_hmac('sha256', "$head.$body", $cfg['api_secret'], true));
    $claims = cl_event_json($body);
    if (!hash_equals($wanted, $sig) || (cl_event_json($head)['alg'] ?? '') !== 'HS256'
        || ($claims['iss'] ?? null) !== $cfg['api_key'] || !is_int($claims['exp'] ?? null) || $claims['exp'] <= $now
        || $claims['exp'] > $now + 330 || !is_int($claims['nbf'] ?? 0) || ($claims['nbf'] ?? 0) > $now + 5
        || !is_string($claims['sha256'] ?? null) || !hash_equals(base64_encode(hash('sha256', $raw, true)), $claims['sha256'])) {
        throw new ClassroomError(403, 'امضای رویداد رسانه معتبر نیست.');
    }
    $event = json_decode($raw, true, 24);
    if (!is_array($event) || !is_string($event['event'] ?? null)) throw new ClassroomError(400, 'رویداد رسانه معتبر نیست.');
    return $event;
}

function cl_event_apply(ClassroomStore $s, array $cfg, array $event, int $now, callable $rpc): bool
{
    if (!in_array($event['event'] ?? null, ['room_started','participant_joined','participant_left','participant_connection_aborted','room_finished'], true)) return false;
    $room = $event['room']['name'] ?? null;
    $id = is_string($room) ? cl_room_class($cfg, $room) : 0;
    if (!$id) return false;
    return cl_class_lock($s, $cfg, $id, static function () use ($s, $cfg, $event, $now, $rpc, $id, $room) {
        $lesson = cl_active($s, $id);
        if (!$lesson || $lesson['room'] !== $room) return false; // Delayed/repeated events cannot affect a resumed lesson.
        if ($lesson['phase'] === 'closing' || cl_lesson_expired($cfg, $lesson, $now)) {
            cl_pause($lesson, 'closing');
            cl_retire_transport($s, $cfg, $lesson, $now, $rpc, $event['event'] === 'room_finished');
            return true;
        }
        if (in_array($event['event'], ['room_started','participant_joined'], true)) {
            if ($lesson['phase'] === 'starting' && empty($lesson['event_received'])) {
                $lesson['event_received'] = true;
                $s->put('lesson', $lesson['id'], $lesson);
            }
            return false;
        }
        if ($event['event'] === 'room_finished') {
            cl_pause($lesson);
            cl_retire_transport($s, $cfg, $lesson, $now, $rpc, true);
            return false;
        }
        $who = $event['participant']['identity'] ?? null;
        $sid = $event['participant']['sid'] ?? null;
        if (!cl_media_identity($who) || !cl_sid($sid)) return false;
        if ($lesson['phase'] === 'paused' && $lesson['room'] !== null) {
            cl_retire_transport($s, $cfg, $lesson, $now, $rpc); // Retry an interrupted DeleteRoom after a repeated signed event.
            return false;
        }
        if (($lesson['pulses'][$who]['sid'] ?? null) !== $sid) return false;
        unset($lesson['pulses'][$who], $lesson['seen'][$who], $lesson['staff'][$who]);
        if ($lesson['phase'] === 'live' && !cl_has_staff($lesson, $now)) {
            cl_pause($lesson);
            cl_retire_transport($s, $cfg, $lesson, $now, $rpc);
        } else $s->put('lesson', $lesson['id'], $lesson);
        return false;
    });
}

// The audited event route uses no cookies. Defining the functions alone is safe in offline tests.
if (cl_manager_context() !== null) cl_json_reply(static function (): array {
    cl_require_post();
    $content = strtolower(trim(explode(';', (string)($_SERVER['CONTENT_TYPE'] ?? ''))[0]));
    if (!in_array($content, ['application/webhook+json','application/json'], true)) throw new ClassroomError(415, 'نوع رویداد معتبر نیست.');
    $raw = file_get_contents('php://input', false, null, 0, 262145);
    if (!is_string($raw) || strlen($raw) > 262144) throw new ClassroomError(413, 'رویداد بیش از اندازه بزرگ است.');
    $cfg = cl_config();
    $now = time();
    $event = cl_event_verify($cfg, $raw, $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? null, $now);
    $rpc = static fn(string $method, string $room, array $data): array => cl_rpc($cfg, $room, $method, $data);
    try { cl_event_apply(cl_store(), $cfg, $event, $now, $rpc); }
    catch (ClassroomError $error) {
        // LiveKit retries 5xx, not a client-side 409 when a room mutation currently holds the lock.
        if ($error->status === 409) throw new ClassroomError(503, 'کلاس در حال تغییر است.');
        throw $error;
    }
    return [];
});
