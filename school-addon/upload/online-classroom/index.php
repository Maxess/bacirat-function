<?php
declare(strict_types=1);
require_once __DIR__ . '/activity.php';
require_once __DIR__ . '/reports.php';

cl_headers();
header('Content-Type: text/html; charset=UTF-8');
$actor = $errorMessage = $class = $reports = null;
$classes = $choices = [];
$connect = $year = '';
$now = time();
try {
    $cfg = cl_config();
    $s = cl_store();
    $view = $_GET['view'] ?? 'classes';
    if ($view === 'reports') {
        try { $year = cl_current_year($s->db); } catch (ClassroomError $ignored) {} // Historical reports remain readable by authorized staff.
        $actor = cl_school_user($s->db, $_SESSION ?? [], $year);
        $reports = cl_reports($s, $actor);
    } else {
        $year = cl_current_year($s->db);
        $actor = cl_school_user($s->db, $_SESSION ?? [], $year);
        $classes = array_filter(cl_classes($s->db, $year), static fn($c) => cl_access_class($s->db, $c, $actor));
        if (isset($_GET['class'])) $class = $classes[cl_id($_GET['class'])] ?? throw new ClassroomError(403, 'این کلاس برای حساب تو در دسترس نیست.');
        elseif (!isset($_GET['view']) && count($classes) === 1) {
            // Use the real class URL as the canonical address of the waiting page.
            header('Location: ' . cl_href(['class'=>array_key_first($classes)]), true, 303);
            return;
        }
        if ($class !== null && cl_is_staff($actor)) {
            $choices = cl_staff_schedule($s, $cfg, $class, $actor, $now)['choices'];
        }
    }
    if ($class !== null) $connect = $cfg['api_url'] . ' ' . $cfg['signal_url'];
} catch (ClassroomError $error) {
    http_response_code($error->status);
    $errorMessage = $error->getMessage();
} catch (Throwable $error) {
    http_response_code(503);
    error_log('School classroom: page failed (' . get_class($error) . ')');
    $errorMessage = 'ساختار سال تحصیلی، برنامهٔ هفتگی یا تنظیمات هاست در دسترس نیست.';
} finally {
    if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
}
header("Content-Security-Policy: default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' blob: data:; font-src 'self' blob: data:; worker-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; connect-src 'self' " . $connect);
$staff = $actor !== null && cl_is_staff($actor);
$actorLabel = match ($actor['office'] ?? $actor['role'] ?? '') {
    'student'=>'دانش‌آموز', 'teacher'=>'دبیر', 'deputy'=>'معاون / ناظم', 'admin'=>'مدیر', default=>'',
};
$panel = match ($actor['office'] ?? $actor['role'] ?? '') {
    'student'=>'student-panel.php', 'teacher'=>'teacher-panel.php', 'deputy'=>'deputy-panel.php', 'admin'=>'index.php?view=dashboard', default=>'index.php?view=login',
};
$pageTitle = $reports !== null ? 'حضور و غیاب کلاس مجازی' : ($class === null ? 'کلاس مجازی' : $class['name']);
?>
<!doctype html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title><?= cl_html($pageTitle) ?> | مدرسه</title>
    <link rel="stylesheet" href="<?= cl_html(cl_asset('classroom.css')) ?>">
    <?php if ($class !== null && $errorMessage === null): ?>
    <script defer src="<?= cl_html(cl_asset('livekit-client.umd.js')) ?>"></script>
    <script defer src="<?= cl_html(cl_asset('board.js')) ?>"></script>
    <?php endif; ?>
    <?php if ($class !== null && $errorMessage === null): ?><script defer src="<?= cl_html(cl_asset('classroom.js')) ?>"></script><?php endif; ?>
</head>
<body>
<main class="school-main">
    <header class="banner">
        <nav aria-label="مسیرهای مدرسه">
            <a href="<?= cl_html(cl_site_href($panel)) ?>">بازگشت به پنل</a>
            <?php if ($actor): ?><a href="<?= cl_html(cl_href(['view'=>'classes'])) ?>">کلاس مجازی</a><?php endif; ?>
            <?php if ($staff): ?><a href="<?= cl_html(cl_href(['view'=>'reports'])) ?>">حضور و غیاب کلاس مجازی</a><?php endif; ?>
        </nav>
        <p class="eyebrow">مدرسه · یادگیری آنلاین</p>
        <h1><?= cl_html($pageTitle) ?></h1>
        <?php if ($actor): ?><p class="byline"><?= cl_html($actor['name'] . ' · ' . $actorLabel . ($year !== '' ? ' · سال ' . $year : '')) ?></p><?php endif; ?>
    </header>
    <?php if ($errorMessage !== null): ?>
        <section class="card"><p role="alert"><?= cl_html($errorMessage) ?></p>
        <?php if ($actor === null): ?><a href="<?= cl_html(cl_site_href('admin-login.php?tab=teacher')) ?>">ورود دبیر یا مدیر</a><?php endif; ?></section>
    <?php elseif ($reports !== null): cl_render_reports($reports); ?>
    <?php elseif ($class === null): ?>
        <section class="card">
            <h2>کلاس‌های سال پیش‌فرض</h2>
            <?php if (!$classes): ?><p>برای این حساب در سال جاری کلاس قابل دسترسی نیست. عضویت یا برنامهٔ هفتگی را بررسی کن.</p><?php endif; ?>
            <div class="class-grid">
            <?php foreach ($classes as $c): ?><a class="class-link" href="<?= cl_html(cl_href(['class'=>$c['id']])) ?>"><strong><?= cl_html($c['name']) ?></strong><small><?= $staff ? 'برنامه و اتاق کلاس' : 'ورود به اتاق انتظار' ?></small></a><?php endforeach; ?>
            </div>
        </section>
    <?php else: ?>
        <section id="classroom" data-api="<?= cl_html(cl_href(['route'=>'api'])) ?>" data-csrf="<?= cl_html(cl_context()['csrf']) ?>"
          data-class="<?= $class['id'] ?>" data-bell-date="<?= cl_day($now)->format('Y-m-d') ?>" data-moderator="<?= $staff ? 'yes' : 'no' ?>"
          data-content="<?= cl_html(cl_href(['route'=>'content','class'=>$class['id']])) ?>"
          data-pdf="<?= cl_html(cl_asset('pdf.min.mjs')) ?>" data-pdf-worker="<?= cl_html(cl_asset('pdf.worker.min.mjs')) ?>"
          data-pdf-data="<?= cl_html(cl_href(['route'=>'pdf']) . '&name=') ?>">
            <section class="card lecture-bar">
                <div><p class="eyebrow" id="status" role="status">اتاق انتظار</p><h2 id="lecture-title">کلاس <?= cl_html($class['name']) ?></h2></div>
                <?php if ($staff): ?>
                <div class="staff-actions">
                    <label for="slot">زنگ‌های امروز · وقت تهران</label>
                    <div class="actions">
                    <select id="slot"><option value="">انتخاب زنگ</option>
                        <?php foreach ($choices as $choice): ?><option value="<?= $choice['id'] ?>"><?= cl_html($choice['label']) ?></option><?php endforeach; ?>
                    </select>
                    <button type="button" id="open" disabled>شروع زنگ</button><button type="button" id="close" class="danger" hidden>پایان کلاس برای همه</button>
                    </div>
                </div>
                <?php endif; ?>
            </section>
            <p id="notice" role="status" class="notice" hidden></p>
            <section class="card waiting" id="waiting">
                <span aria-hidden="true" class="waiting-icon">◷</span>
                <h2 id="wait-title"><?= $staff ? 'در انتظار شروع زنگ' : 'در انتظار ورود دبیر یا مسئول مدرسه' ?></h2>
                <p id="wait-message"><?= $staff ? 'در حال بررسی زنگ‌های امروز…' : 'شما وارد اتاق انتظار شده‌اید. نیازی به اقدامی نیست؛ لطفاً تا ورود دبیر، مدیر یا ناظم منتظر بمانید.' ?></p>
                <p class="meta">زمان انتظار در حضور کلاس حساب نمی‌شود.</p>
                <?php if (!$staff): ?><button type="button" id="join" hidden>بازگشت به اتاق انتظار</button><?php endif; ?>
            </section>
            <div class="actions waiting-actions"><button type="button" id="leave" class="secondary" hidden>خروج</button></div>
            <div id="live-panel" hidden>
                <section class="card sound" aria-label="کنترل صدا">
                    <div class="actions">
                        <button type="button" id="microphone" disabled>روشن‌کردن میکروفون</button>
                        <button type="button" id="enable-audio" class="secondary" hidden>فعال‌کردن پخش صدا</button>
                        <?php if ($staff): ?><button type="button" id="clear-floor" class="secondary">گرفتن نوبت از همه</button><?php endif; ?>
                    </div>
                    <p id="mic-feedback" role="status" hidden></p>
                    <p class="meta">دادن نوبت میکروفون کسی را باز نمی‌کند؛ گوینده خودش آن را روشن می‌کند. دوربین و اشتراک صفحه فعال نیستند.</p>
                </section>
                <div class="room-grid">
                    <section class="card board-card" id="board" aria-label="تختهٔ کلاس">
                        <div class="section-top"><h2>تختهٔ کلاس</h2><div class="actions"><label>بزرگ‌نمایی <select id="board-zoom" aria-label="بزرگ‌نمایی تخته"><option value="1">۱۰۰٪</option><option value="1.4">۱۴۰٪</option><option value="2">۲۰۰٪</option></select></label><button type="button" id="board-expand" class="secondary" aria-pressed="false">بزرگ‌کردن تخته</button></div></div>
                        <?php if ($staff): ?>
                        <div class="board-tools">
                            <div class="actions" role="group" aria-label="ابزار نوشتن">
                                <button type="button" class="secondary" id="pen" aria-pressed="true">قلم</button>
                                <button type="button" class="secondary" id="eraser" aria-pressed="false">پاک‌کن</button>
                                <button type="button" class="secondary" id="pan" aria-pressed="false">پیمایش</button>
                                <label>رنگ <input type="color" id="ink-color" value="#2346a7"></label>
                                <label>ضخامت <input type="range" id="ink-size" min="1" max="36" value="4"></label>
                                <button type="button" class="secondary" id="board-clear">پاک‌کردن نوشته‌های این صفحه</button>
                            </div>
                            <form id="board-upload" class="actions"><label>تصویر یا PDF <input type="file" id="board-file" accept="image/png,image/jpeg,image/webp,application/pdf" required></label><button type="submit">افزودن به تخته</button></form>
                            <div class="actions" aria-label="صفحه‌های فایل">
                                <label>زمینه <select id="board-doc"><option value="">تختهٔ خالی</option></select></label>
                                <button type="button" class="secondary" id="page-prev" disabled>قبلی</button>
                                <label>صفحه <input type="number" id="page-number" min="1" value="1" dir="ltr" disabled></label>
                                <button type="button" class="secondary" id="page-go" disabled>برو</button>
                                <button type="button" class="secondary" id="page-next" disabled>بعدی</button>
                            </div>
                        </div>
                        <?php endif; ?>
                        <p id="board-caption" class="meta"></p><p id="board-note" role="status" hidden></p>
                        <div id="board-scroll" class="board-scroll" tabindex="0" aria-label="نمای تخته؛ برای دیدن قسمت‌های بزرگ‌نمایی‌شده پیمایش کن">
                            <div id="board-stage" class="board-stage<?= $staff ? ' editable' : '' ?>">
                                <canvas id="board-paper" aria-label="صفحهٔ فایل یا تصویر تخته"></canvas>
                                <canvas id="board-ink" aria-label="نوشته‌های روی تخته"></canvas>
                            </div>
                        </div>
                    </section>
                    <aside class="room-side">
                        <section class="card"><h2>حاضران</h2><ul id="people" class="people"></ul></section>
                        <section class="card"><h2>گفت‌وگوی کلاس</h2><ol id="chat-log" class="chat-log" role="log" aria-label="پیام‌های کلاس"></ol>
                            <form id="chat-form"><label for="chat-text" class="sr-only">متن پیام</label><textarea id="chat-text" maxlength="600" rows="2" placeholder="پیامی برای این کلاس…" required></textarea><button type="submit">ارسال</button></form>
                        </section>
                    </aside>
                </div>
            </div>
            <div id="audio-tracks" aria-hidden="true"></div>
        </section>
    <?php endif; ?>
</main>
</body>
</html>
