<?php
declare(strict_types=1);

// Adapter for the existing mtagbot bots. No polling, tokens, linking or schema setup here.
function mpm_school_bot_adapter(PDO $db, string $provider, string $siteBase): ?array
{
    if ($provider === 'rubika') {
        // An optional site-owned bridge must use existing Rubika configuration and student links.
        // revision: opaque config fingerprint, active(): bool, links(int): array of chat IDs,
        // private(string): bool, send(int student, string chat, string text): bool (confirmed only).
        $file = __DIR__ . '/classroom-rubika-bridge.php';
        if (is_file($file) && realpath($file) === $file) require_once $file;
        if (!function_exists('school_classroom_rubika_adapter')) return null;
        $adapter = school_classroom_rubika_adapter($db, $siteBase);
        if (!is_array($adapter) || !is_string($adapter['revision'] ?? null) || $adapter['revision'] === '') return null;
        foreach (['active','links','private','send'] as $method) if (!is_callable($adapter[$method] ?? null)) return null;
        return $adapter;
    }
    if (!in_array($provider, ['bale','telegram'], true) || !is_file(__DIR__ . '/bot_helpers.php')) return null;
    require_once __DIR__ . '/bot_helpers.php';
    // bot_valid_platform() in the pinned host falls back to Bale. Never pass unrecognized platforms to it.
    if (bot_valid_platform($provider) !== $provider) return null;
    $table = bot_user_table($provider);
    if ($table !== $provider . '_bot_users' || !mpm_table_exists($db, $table)) return null;
    $key = bot_token_key($provider);
    $token = get_setting($key, '');
    if (!is_string($token) || !preg_match('/^[A-Za-z0-9_:\-]{12,180}$/D', $token)) return null;
    $column = $provider . '_chat_id';
    $expected = $siteBase . '/' . $provider . '-webhook.php';
    return [
        'revision'=>hash('sha256', $token),
        // The pinned host has no independent bot-enable flag. Its currently registered site webhook is the active instance.
        'active'=>static function () use ($provider, $expected): bool {
            $info = bot_api_request($provider, 'getWebhookInfo');
            return ($info['result']['url'] ?? null) === $expected;
        },
        'links'=>static function (int $student) use ($db, $table, $column): array {
            // Equivalent student targeting as bot_target_recipients, without its ensure_bot_schema() writes.
            return mpm_query($db, "SELECT DISTINCT b.`$column` FROM `$table` b JOIN students s ON s.id=b.student_id
                WHERE s.status='active' AND b.student_id=? ORDER BY b.`$column`", [$student])->fetchAll(PDO::FETCH_COLUMN);
        },
        'private'=>static function (string $chat) use ($provider): bool {
            if (!preg_match('/^[1-9][0-9]{0,31}$/D', $chat)) return false;
            $info = bot_api_request($provider, 'getChat', ['chat_id'=>$chat])['result'] ?? null;
            return is_array($info) && ($info['type'] ?? null) === 'private'
                && (is_string($info['id'] ?? null) || is_int($info['id'] ?? null)) && (string)$info['id'] === $chat;
        },
        'send'=>static function (int $student, string $chat, string $text) use ($provider, $key, $token): bool {
            if (!hash_equals($token, (string)get_setting($key, ''))) return false;
            try {
                $result = bot_send_message($provider, $chat, $text);
                $sent = isset($result['result']['message_id']);
            } catch (Throwable $error) { $sent = false; }
            // Do not copy provider bodies/errors (which may contain secrets or PII) to school logs.
            bot_log_send($provider, $chat, $student, 'classroom_attendance', $text, $sent ? 'sent' : 'failed', $sent ? 'ok' : 'not_confirmed');
            return $sent;
        },
    ];
}
