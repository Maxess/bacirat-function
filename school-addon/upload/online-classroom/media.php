<?php
declare(strict_types=1);

const CL_MEDIA_ACTIONS = ['give-mic', 'cut-mic', 'cut-video', 'cut-all-mics'];
const CL_TRACK_SOURCES = [1 => 'CAMERA', 2 => 'MICROPHONE', 3 => 'SCREEN_SHARE', 4 => 'SCREEN_SHARE_AUDIO'];
const CL_VIDEO_SOURCES = ['CAMERA', 'SCREEN_SHARE']; // SCREEN_SHARE_AUDIO, if present, is separate.

function cl_media_identity(mixed $value, bool $studentsOnly = false): bool
{
    $roles = $studentsOnly ? 'student' : '(student|teacher|admin)';
    return is_string($value) && preg_match('/^' . $roles . '-[1-9]\d{0,17}$/D', $value) === 1;
}

function cl_media_target(string $action, mixed $target): ?string
{
    if (!in_array($action, CL_MEDIA_ACTIONS, true)) throw new LogicException('Unknown moderation action');
    if ($action === 'cut-all-mics' ? $target !== null : !cl_media_identity($target, $action !== 'give-mic')) {
        throw new ClassroomError(400, 'شناسهٔ شرکت‌کننده برای این عملیات معتبر نیست.');
    }
    return $target;
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
    // Preserve the other flags when changing just one source policy.
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

function cl_change_media_permission(string $room, string $identity, string $action, callable $rpc): void
{
    $scope = ['room' => $room, 'identity' => $identity];
    // UpdateParticipant without permission or metadata fields returns the live state unchanged.
    // It reads the real participant rather than relying on the ListParticipants store snapshot.
    $reply = $rpc('UpdateParticipant', $scope);
    $permission = $previous = cl_media_permission($reply, $identity);
    $sources = $previous['canPublishSources'];
    $next = match ($action) {
        'give-mic' => [...$sources, 'MICROPHONE'],
        'cut-mic', 'cut-all-mics' => array_diff($sources, ['MICROPHONE']),
        'cut-video' => array_diff($sources, CL_VIDEO_SOURCES),
        default => throw new LogicException('Unknown participant moderation action'),
    };
    $permission['canPublishSources'] = array_values(array_intersect(CL_TRACK_SOURCES, $next));
    $permission['canPublish'] = $permission['canPublishSources'] !== [];
    if ($permission !== $previous) {
        $reply = $rpc('UpdateParticipant', $scope + ['permission' => $permission]);
        if (cl_media_permission($reply, $identity) !== $permission) throw cl_media_bad_reply();
    }
    if ($action === 'give-mic') return;
    // Pinned LiveKit emits even an empty tracks list. Do not treat a truncated reply as a confirmed cut.
    $tracks = $reply['tracks'] ?? null;
    if (!is_array($tracks) || !array_is_list($tracks)) throw cl_media_bad_reply();
    foreach ($tracks as $track) {
        if (!is_array($track) || !in_array(cl_track_source($track['source'] ?? null), $permission['canPublishSources'], true)) {
            throw cl_media_bad_reply();
        }
    }
}

function cl_control_media(string $room, string $action, ?string $target, callable $rpc): void
{
    cl_media_target($action, $target);
    if ($target !== null) {
        cl_change_media_permission($room, $target, $action, $rpc);
        return; // Grant/cut affects exactly this participant, never a different speaker.
    }
    $participants = $rpc('ListParticipants', ['room' => $room])['participants'] ?? null;
    if (!is_array($participants) || !array_is_list($participants)) throw cl_media_bad_reply();
    $seen = [];
    $failed = false;
    foreach ($participants as $person) {
        $identity = is_array($person) ? ($person['identity'] ?? null) : null;
        if (!cl_media_identity($identity)) {
            $failed = true;
            continue;
        }
        if (isset($seen[$identity])) continue;
        $seen[$identity] = true;
        try {
            cl_change_media_permission($room, $identity, $action, $rpc);
        } catch (ClassroomError) {
            $failed = true; // Still cut the other participants, but never signal full success.
        }
    }
    if ($failed) throw cl_media_bad_reply();
}
