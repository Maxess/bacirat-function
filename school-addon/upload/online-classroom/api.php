<?php
declare(strict_types=1);
require_once __DIR__ . '/activity.php';

cl_json_reply(static function (): array {
    cl_require_post();
    $cfg = cl_config();
    $context = cl_context();
    if ((isset($_SERVER['HTTP_ORIGIN']) && $_SERVER['HTTP_ORIGIN'] !== $cfg['site_origin'])
        || !hash_equals($context['csrf'], (string)($_SERVER['HTTP_X_CLASSROOM_CSRF'] ?? ''))) {
        throw new ClassroomError(403, 'نشست صفحه معتبر نیست؛ صفحه را دوباره باز کن.');
    }
    $session = $_SESSION ?? [];
    if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
    $isUpload = str_starts_with(strtolower((string)($_SERVER['CONTENT_TYPE'] ?? '')), 'multipart/form-data;');
    $body = $isUpload ? ['action'=>'upload'] + $_POST : cl_json_body(32768)[1];
    $action = is_array($body) ? ($body['action'] ?? null) : null;
    if (!is_string($action)) throw new ClassroomError(400, 'عملیات شناخته نشد.');
    $s = cl_store();
    $year = cl_current_year($s->db);
    $actor = cl_school_user($s->db, $session, $year);
    $class = cl_classes($s->db, $year)[cl_id($body['class'] ?? null)] ?? throw new ClassroomError(403, 'این کلاس برای حساب تو در دسترس نیست.');
    $rpc = static fn(string $method, string $room, array $data): array => cl_rpc($cfg, $room, $method, $data);
    return cl_class_lock($s, $cfg, $class['id'], static function () use ($s, $context, $cfg, $class, $actor, $body, $action, $rpc, $isUpload) {
        $now = time();
        $lesson = cl_active($s, $class['id']);
        $expiredTest = $lesson && cl_is_friday_test($lesson) && cl_lesson_expired($cfg, $lesson, $now);
        if (!cl_access_class($s->db, $class, $actor, cl_friday_window($cfg, $now) !== null)) {
            // A teacher in a demo-only class can trigger retirement at midnight, but never read or rejoin it.
            if ($action === 'status' && $expiredTest && cl_is_staff($actor) && cl_match_transport($lesson, $body)) {
                try { cl_reconcile($s, $cfg, $class, $lesson, $now, $rpc); }
                catch (ClassroomError $error) { error_log('School classroom: expired demo close not confirmed'); }
            }
            throw new ClassroomError(403, 'این کلاس برای حساب تو در دسترس نیست.');
        }
        if ($expiredTest && !in_array($action, ['status','board-sync'], true)) {
            cl_close_lesson($s, $cfg, $lesson, $now, $rpc);
            $lesson = null;
            if ($action !== 'open') throw new ClassroomError(409, 'مهلت این جلسهٔ آزمایشی پایان یافته است.');
        }
        if ($action === 'status') {
            $idle = static function (bool $ready) use ($s, $cfg, $class, $actor): array {
                $result = ['phase'=>'idle', 'ready'=>$ready];
                if (cl_is_staff($actor)) $result['schedule'] = cl_staff_schedule($s, $cfg, $class, $actor, time());
                elseif (cl_friday_window($cfg, time())['students'] ?? false) $result['practiceAvailable'] = true;
                return $result;
            };
            if (!$lesson) return $idle(true);
            $isTest = cl_is_friday_test($lesson);
            $allowedRole = $lesson['year'] === $class['year'] && cl_can_lesson($lesson, $actor);
            if ($allowedRole && !$expiredTest) $lesson = cl_touch($s, $lesson, $actor, $body, $now);
            $ready = true;
            try { $lesson = cl_reconcile($s, $cfg, $class, $lesson, $now, $rpc); }
            catch (ClassroomError $error) {
                if ($error->status < 500) throw $error;
                $ready = false; $lesson = cl_active($s, $class['id']);
            }
            if (!$lesson) {
                if (!$isTest && function_exists('mpm_classroom_delivery_after_response')) mpm_classroom_delivery_after_response();
                return $idle($ready);
            }
            if (!$allowedRole) return ['phase'=>'reserved','ready'=>$ready];
            $now = time(); // A slow SFU call must not extend a previously captured sample's validity.
            $expired = cl_lesson_expired($cfg, $lesson, $now);
            $live = $ready && !$expired && cl_lesson_live($lesson, $now);
            $phase = $expired ? 'closing' : ($live ? 'live' : ($lesson['phase'] === 'live' ? 'paused' : $lesson['phase']));
            $allowed = $live && cl_match_transport($lesson, $body) && cl_entered($lesson, $actor, $body['sid'] ?? null, $now);
            $result = ['phase'=>$phase,'ready'=>$ready, 'lesson'=>$lesson['id'], 'epoch'=>cl_epoch($lesson),
                'callbackMissing'=>$lesson['phase'] === 'paused' && empty($lesson['event_received']),
                'period'=>$lesson['period'], 'subject'=>$lesson['subject'], 'slot'=>$lesson['slot'], 'bell'=>$lesson['bell'] ?? null, 'admitted'=>$allowed,
                'joinable'=>$ready && !$expired && cl_joinable($lesson, $actor, $now),
                'resumable'=>$ready && !$expired && $lesson['phase'] === 'paused' && $lesson['room'] === null];
            // A paused or reconnecting pupil can still see their own last verified total, not the room activity.
            if ($actor['role'] === 'student') $result['attendance'] = $lesson['seconds'][$actor['identity']] ?? 0;
            if ($allowed) $result += ['seconds'=>$lesson['seconds']] + cl_activity_sync($s, $lesson, $actor, $body);
            return $result;
        }
        if ($lesson && ($lesson['year'] !== $class['year'] || !cl_can_lesson($lesson, $actor))) {
            throw new ClassroomError(403, 'این زنگ برای این حساب مجاز نیست.');
        }
        if ($action === 'open') {
            $lesson = cl_open_lesson($s, $context, $cfg, $class, $actor, $body, $now, $rpc);
            return cl_ticket($cfg, $lesson, $actor, ['lesson'=>$lesson['id'],'epoch'=>cl_epoch($lesson)], time());
        }
        if ($action === 'ticket') {
            if ($lesson && cl_match_transport($lesson, $body)) $lesson = cl_reconcile($s, $cfg, $class, $lesson, $now, $rpc);
            return cl_ticket($cfg, $lesson, $actor, $body, time());
        }
        if ($action === 'leave') {
            if ($lesson) {
                cl_leave_lesson($s, $lesson, $actor, $body, $now);
                $updated = cl_active($s, $class['id']);
                if ($updated && $updated['phase'] === 'paused' && $updated['room'] !== null) cl_retire_transport($s, $cfg, $updated, $now, $rpc);
            }
            return [];
        }
        if ($action === 'close') {
            if (!cl_is_staff($actor)) throw new ClassroomError(403, 'پایان کلاس فقط برای دبیر یا مسئول مجاز است.');
            if (!$lesson || ($body['lesson'] ?? null) !== $lesson['id']) throw new ClassroomError(409, 'این زنگ دیگر باز نیست.');
            cl_close_lesson($s, $cfg, $lesson, $now, $rpc);
            if (!cl_is_friday_test($lesson) && function_exists('mpm_classroom_delivery_after_response')) mpm_classroom_delivery_after_response();
            return [];
        }
        $lesson = cl_check_live($cfg, $lesson, $actor, $body, $now);
        if ($action === 'board-sync') {
            // Drawing latency is independent of the slower presence/attendance poll.
            return ['board'=>cl_board_view($s, $lesson['id'], $actor, $body, cl_activity($s, $lesson['id']))];
        }
        if ($action === 'chat') {
            cl_chat($s, $lesson, $actor, $body['text'] ?? null, $now);
            return [];
        }
        if (!cl_is_staff($actor)) throw new ClassroomError(403, 'این عملیات فقط برای دبیر یا مسئول مجاز است.');
        if (in_array($action, CL_MEDIA_ACTIONS, true)) {
            $target = cl_media_target($action, $body['identity'] ?? null);
            if ($target !== null && !isset($lesson['seen'][$target])) throw new ClassroomError(409, 'ورود شرکت‌کننده هنوز تأیید نشده است.');
            cl_control_media($lesson['room'], $action, $target, static fn($method, $data) => $rpc($method, $lesson['room'], $data));
        } elseif ($action === 'upload' && $isUpload) {
            return cl_upload($s, $lesson, $body, $_FILES['file'] ?? []);
        } elseif (in_array($action, ['board-page','board-clear','board-ink'], true)) {
            return cl_board_action($s, $lesson, $body, $action);
        } else {
            throw new ClassroomError(400, 'عملیات شناخته نشد.');
        }
        return [];
    });
});
