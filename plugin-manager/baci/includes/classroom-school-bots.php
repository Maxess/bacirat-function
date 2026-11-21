<?php
declare(strict_types=1);

// Adapter for the existing Baci bots; no second bot, linking or schema setup.
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
    // Baci falls back to Bale for unsupported platforms. Never pass one to that helper.
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
        // The currently registered webhook must belong to this exact school site.
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
                // In Baci, bot_send_message() enqueues. This plugin already has an outbox
                // and must confirm one private send now, not schedule a second unchecked delivery.
                $result = bot_api_request($provider, 'sendMessage', ['chat_id'=>$chat,'text'=>$text]);
                $sent = isset($result['result']['message_id']);
            } catch (Throwable $error) { $sent = false; }
            // Do not copy provider bodies/errors (which may contain secrets or PII) to school logs.
            bot_log_send($provider, $chat, $student, 'classroom_attendance', $text, $sent ? 'sent' : 'failed', $sent ? 'ok' : 'not_confirmed');
            return $sent;
        },
    ];
}

/** Recording delivery is separate from the private attendance adapter and its switches. */
function mpm_school_recording_bot(PDO $db, string $provider): ?array
{
    if (!in_array($provider, ['bale','rubika','telegram'], true) || !is_file(__DIR__ . '/bot_helpers.php')) return null;
    // Read the current settings, not a cached webhook identity or an old upload job's token.
    $token = mpm_query($db, 'SELECT key_value FROM settings WHERE key_name=?', [$provider . '_bot_token'])->fetchColumn();
    if (!is_string($token) || !preg_match('/^[A-Za-z0-9_:\-]{12,180}$/D', $token)) return null;
    $prefix = match ($provider) {
        'bale' => "https://tapi.bale.ai/bot$token/",
        'rubika' => "https://botapi.rubika.ir/v3/$token/",
        'telegram' => "https://api.telegram.org/bot$token/",
    };
    if ($provider === 'telegram') {
        $base = rtrim(trim((string)mpm_query($db, 'SELECT key_value FROM settings WHERE key_name=?', ['telegram_api_base'])->fetchColumn()), '/');
        if ($base !== '') {
            if (!str_contains($base, '://')) $base = 'https://' . $base;
            $parts = parse_url($base);
            if (!is_array($parts) || !mpm_hostname($parts['host'] ?? '') || ($parts['scheme'] ?? '') !== 'https'
                || array_intersect(['user','pass','port','query','fragment'], array_keys($parts))
                || !preg_match('~^(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63})*$~D', $parts['path'] ?? '')) return null;
            $prefix = "$base/bot$token/";
        }
    }
    return ['prefix'=>$prefix,'revision'=>hash('sha256', $prefix)];
}
