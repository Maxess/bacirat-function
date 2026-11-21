<?php
declare(strict_types=1);

const CL_MEDIA_FEATURES = ['mic'=>'MICROPHONE', 'camera'=>'CAMERA'];
const CL_TRACK_SOURCES = [1 => 'CAMERA', 2 => 'MICROPHONE', 3 => 'SCREEN_SHARE', 4 => 'SCREEN_SHARE_AUDIO'];

function cl_media_identity(mixed $value, bool $studentsOnly = false): bool
{
    $roles = $studentsOnly ? 'student' : '(student|teacher|admin)';
    return is_string($value) && preg_match('/^' . $roles . '-[1-9]\d{0,17}$/D', $value) === 1;
}

function cl_media_bad_reply(): ClassroomError
{
    return new ClassroomError(502, 'سرور رسانه وضعیت مورد انتظار را تأیید نکرد. اگر صدا یا تصویر قطع نشد، کلاس را پایان بده.');
}

function cl_wire_value(array $data, string $snake, string $camel, mixed $default): mixed
{
    if (array_key_exists($snake, $data)) {
        if ($snake !== $camel && array_key_exists($camel, $data) && $data[$snake] !== $data[$camel]) throw cl_media_bad_reply();
        return $data[$snake];
    }
    return array_key_exists($camel, $data) ? $data[$camel] : $default;
}

function cl_track_source(mixed $value): ?string
{
    if (is_int($value)) return CL_TRACK_SOURCES[$value] ?? null;
    return in_array($value, CL_TRACK_SOURCES, true) ? $value : null;
}

function cl_media_permission(array $participant, string $identity): array
{
    $wire = $participant['permission'] ?? null;
    if (($participant['identity'] ?? null) !== $identity || !is_array($wire)) throw cl_media_bad_reply();
    // LiveKit Twirp defaults to original proto names, e.g. can_publish.
    // No data, metadata, share-audio, or agent permissions. Video sources are explicitly listed.
    $fields = [
        'canSubscribe' => 'can_subscribe', 'canPublish' => 'can_publish', 'canPublishData' => 'can_publish_data',
        'canUpdateMetadata' => 'can_update_metadata', 'hidden' => 'hidden', 'recorder' => 'recorder',
        'agent' => 'agent', 'canSubscribeMetrics' => 'can_subscribe_metrics', 'canManageAgentSession' => 'can_manage_agent_session',
    ];
    $permission = [];
    foreach ($fields as $camel => $snake) {
        // Never infer "off" from a missing publish/subscribe flag in a confirmation.
        $default = in_array($camel, ['canPublish', 'canSubscribe'], true) ? null : false;
        $value = cl_wire_value($wire, $snake, $camel, $default);
        if (!is_bool($value)) throw cl_media_bad_reply();
        $permission[$camel] = $value;
    }
    $sources = cl_wire_value($wire, 'can_publish_sources', 'canPublishSources', []);
    if (!is_array($sources) || !array_is_list($sources)) throw cl_media_bad_reply();
    $allow = [];
    if ($permission['canPublish']) {
        // Empty with canPublish=true is unrestricted in LiveKit, not listener-only.
        if ($sources === []) $sources = array_values(CL_TRACK_SOURCES);
        foreach ($sources as $item) {
            $source = cl_track_source($item);
            if ($source === null) throw cl_media_bad_reply();
            $allow[] = $source;
        }
    }
    $permission['canPublishSources'] = array_values(array_intersect(CL_TRACK_SOURCES, $allow));
    return $permission;
}

/** Null identity reads the room default, not the policy of a particular participant. */
function cl_access_enabled(array $lesson, string $feature, ?string $identity = null): bool
{
    $rule = $lesson['access'][$feature] ?? [];
    if ($identity !== null && isset($rule['members'][$identity])) return $rule['members'][$identity] === true;
    return ($rule['all'] ?? false) === true;
}

function cl_camera_codec(array $room): ?string
{
    $codecs = $room['enabled_codecs'] ?? $room['enabledCodecs'] ?? [];
    if (!is_array($codecs)) return null;
    $names = [];
    foreach ($codecs as $codec) if (is_array($codec) && is_string($codec['mime'] ?? null)) $names[] = strtolower($codec['mime']);
    return in_array('video/vp8', $names, true) ? 'vp8' : (in_array('video/h264', $names, true) ? 'h264' : null);
}

/** The codec advertised by the SFU is also used by screen share. Its presence alone does not enable webcams. */
function cl_webcam_codec(array $cfg, array $lesson): ?string
{
    return ($cfg['camera_share'] ?? false) === true ? ($lesson['camera_codec'] ?? null) : null;
}

function cl_live_media_peers(array $reply): array
{
    $people = array_key_exists('participants', $reply) ? $reply['participants'] : [];
    if (!is_array($people) || !array_is_list($people) || count($people) > 60) throw cl_media_bad_reply();
    $peers = [];
    foreach ($people as $person) {
        if (!is_array($person)) throw cl_media_bad_reply();
        if (!in_array($person['state'] ?? null, ['ACTIVE', 2], true)) continue;
        $id = $person['identity'] ?? null;
        if (!cl_media_identity($id) || !cl_sid($person['sid'] ?? null) || isset($peers[$id])) throw cl_media_bad_reply();
        $peers[$id] = $person;
    }
    return $peers;
}

function cl_media_matches(array $peer, string $identity, array $wanted): bool
{
    try { $actual = cl_media_permission($peer, $identity); }
    catch (ClassroomError $error) { return false; }
    foreach ($actual as $key=>$value) {
        if ($value !== ($wanted[$key] ?? false)) return false;
    }
    // Twirp returns an explicit tracks list. An incomplete result is not a confirmed revocation.
    $tracks = $peer['tracks'] ?? null;
    if (!is_array($tracks) || !array_is_list($tracks)) return false;
    foreach ($tracks as $track) {
        if (!is_array($track) || !in_array(cl_track_source($track['source'] ?? null), $wanted['canPublishSources'], true)) return false;
    }
    return true;
}

/** Apply the lesson policy to real SFU participants. Listener tickets never carry a reusable publish grant. */
function cl_sync_media(array $cfg, array $lesson, array $peers, callable $rpc, bool $strict = false): bool
{
    if (!isset($lesson['access']['mic']) && !isset($lesson['access']['camera']) && empty($lesson['screen_used'])) return true;
    $room = $lesson['room'];
    $camera = cl_webcam_codec($cfg, $lesson) !== null;
    $failed = false;
    $until = microtime(true) + 1.25; // Bound lock time; subsequent presence samples retry mismatches.
    foreach ($peers as $id=>$peer) {
        $sources = [];
        if (($lesson['seen'][$id] ?? null) === $peer['sid']) {
            foreach (CL_MEDIA_FEATURES as $feature=>$source) {
                if (($feature !== 'camera' || $camera) && cl_access_enabled($lesson, $feature, $id)) $sources[] = $source;
            }
            if (($lesson['screen']['identity'] ?? null) === $id && ($lesson['screen']['sid'] ?? null) === $peer['sid']
                && ($lesson['staff'][$id] ?? null) === $peer['sid']) $sources[] = 'SCREEN_SHARE';
        }
        // Canonical order also makes a comparison independent of the SFU's enum order.
        $permission = ['canSubscribe'=>true, 'canPublish'=>count($sources)>0, 'canPublishData'=>false,
            'canPublishSources'=>array_values(array_intersect(CL_TRACK_SOURCES, $sources))];
        if (cl_media_matches($peer, $id, $permission)) continue;
        if (microtime(true) >= $until) return false;
        try {
            $reply = $rpc('UpdateParticipant', $room, ['room'=>$room,'identity'=>$id,'permission'=>$permission]);
            if (($reply['sid'] ?? null) !== $peer['sid']) {
                // UpdateParticipant addresses an identity, not a SID. A replacement racing this RPC
                // must return to listeners until its own admission sample can apply the lesson policy.
                if ($permission['canPublish']) {
                    $rpc('UpdateParticipant', $room, ['room'=>$room,'identity'=>$id,'permission'=>[
                        'canSubscribe'=>true,'canPublish'=>false,'canPublishData'=>false,'canPublishSources'=>[],
                    ]]);
                }
                $failed = true;
            } elseif (!cl_media_matches($reply, $id, $permission)) $failed = true;
        } catch (ClassroomError $error) {
            // A member who just left needs no update; a new SID will be sampled on re-entry.
            if ($strict || $error->status !== 409) $failed = true;
        }
    }
    return !$failed;
}
