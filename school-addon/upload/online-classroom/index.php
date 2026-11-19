<?php
declare(strict_types=1);
require_once __DIR__ . '/core.php';

cl_headers();
header('Content-Type: text/html; charset=UTF-8');
$actor = null;
$errorMessage = null;
try {
    $cfg = cl_config();
    $pdo = cl_load_site($cfg);
    $class = cl_class($pdo, $cfg);
    $actor = cl_actor($pdo, $_SESSION, $class);
    $csrf = $_SESSION['school_classroom_csrf'] ??= bin2hex(random_bytes(32));
    session_write_close();
    $connect = $cfg['api_url'] . ' ' . $cfg['signal_url'];
} catch (ClassroomError $error) {
    http_response_code($error->status);
    $errorMessage = $error->getMessage();
} catch (Throwable $error) {
    http_response_code(503);
    error_log('School classroom pilot: page failed (' . get_class($error) . ')');
    $errorMessage = 'تنظیمات سایت آزمایشی یا عضویت کلاس را بررسی کن.';
}
if (session_status() === PHP_SESSION_ACTIVE) {
    session_write_close();
}
header("Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; connect-src 'self' " . ($connect ?? ''));
function cl_html(string $value): string {
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}
$roles = ['student' => 'دانش‌آموز', 'teacher' => 'دبیر', 'admin' => 'مدیر'];
$panel = ['student' => '../student-panel.php', 'teacher' => '../teacher-panel.php', 'admin' => '../index.php?view=dashboard'];
?>
<!doctype html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>کلاس آنلاین مدرسه</title>
    <link rel="stylesheet" href="assets/classroom.css">
    <?php if ($actor !== null): ?>
    <script defer src="assets/livekit-client.umd.js"></script>
    <script defer src="assets/classroom.js"></script>
    <?php endif; ?>
</head>
<body>
<main>
    <header>
        <p class="label">پایلوت · فقط صدا</p>
        <h1>کلاس آنلاین <?= $actor ? '· ' . cl_html($class['name']) : '' ?></h1>
    </header>
    <?php if ($actor === null): ?>
        <p role="alert"><?= cl_html((string)$errorMessage) ?></p>
        <nav><a href="../index.php?view=login">ورود دانش‌آموز</a> · <a href="../admin-login.php?tab=teacher">ورود دبیر یا مدیر</a></nav>
    <?php else: ?>
        <p><?= cl_html($actor['name']) ?> · <?= cl_html($roles[$actor['role']]) ?> · <?= cl_html($class['year']) ?></p>
        <section id="classroom" data-csrf="<?= cl_html($csrf) ?>" data-moderator="<?= $actor['moderator'] ? 'yes' : 'no' ?>">
            <p id="status" role="status" aria-live="polite">برای شروع یا ورود دکمهٔ زیر را بزن.</p>
            <div class="actions" aria-label="کنترل‌های کلاس">
                <?php if ($actor['moderator']): ?>
                <button type="button" id="open">شروع و ورود</button>
                <button type="button" id="close" hidden>پایان کلاس برای همه</button>
                <button type="button" id="clear-floor" hidden>گرفتن نوبت از همه</button>
                <?php else: ?>
                <button type="button" id="join">ورود به کلاس</button>
                <?php endif; ?>
                <button type="button" id="microphone" hidden disabled>روشن‌کردن میکروفون</button>
                <button type="button" id="enable-audio" hidden>فعال‌کردن پخش صدا</button>
                <button type="button" id="leave" hidden>خروج از کلاس</button>
            </div>
            <section id="people-panel" hidden>
                <h2>حاضران</h2>
                <ul id="people"></ul>
            </section>
            <div id="audio-tracks" aria-hidden="true"></div>
        </section>
        <p><a href="<?= cl_html($panel[$actor['role']]) ?>">بازگشت به پنل</a></p>
    <?php endif; ?>
</main>
</body>
</html>
