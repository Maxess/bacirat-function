<?php
declare(strict_types=1);
require_once __DIR__ . '/activity.php';
require_once __DIR__ . '/reports.php';

cl_headers();
header('Content-Type: text/html; charset=UTF-8');
$actor = $errorMessage = $class = $reports = $friday = null;
$classes = $choices = [];
$connect = $year = $schoolName = $classLabel = '';
$now = time();
$view = $_GET['view'] ?? 'classes';
$lite = $view === 'lite';
$snapshot = null;
$bellDate = cl_day($now)->format('Y-m-d');
try {
    $cfg = cl_config();
    $s = cl_store();
    if ($view === 'recordings') {
        require __DIR__ . '/recordings-view.php';
        return;
    }
    if ($view === 'reports') {
        try { $year = cl_current_year($s->db); } catch (ClassroomError $ignored) {} // Historical reports remain readable by authorized staff.
        $actor = cl_school_user($s->db, $_SESSION ?? [], $year);
        $reports = cl_reports($s, $actor);
    } else {
        $year = cl_current_year($s->db);
        $actor = cl_school_user($s->db, $_SESSION ?? [], $year);
        $friday = cl_is_staff($actor) && cl_friday_window($cfg, $now) !== null;
        $classes = array_filter(cl_classes($s->db, $year), static fn($c) => cl_access_class($s->db, $c, $actor, $friday));
        if (isset($_GET['class'])) $class = $classes[cl_id($_GET['class'])] ?? throw new ClassroomError(403, 'این کلاس برای حساب تو در دسترس نیست.');
        elseif (!isset($_GET['view']) && count($classes) === 1) {
            // Use the real class URL as the canonical address of the waiting page.
            header('Location: ' . cl_href(['class'=>array_key_first($classes)]), true, 303);
            return;
        }
        if ($class !== null && !$lite && cl_is_staff($actor)) {
            $choices = cl_staff_schedule($s, $cfg, $class, $actor, $now)['choices'];
        }
    }
    if ($class !== null) {
        $schoolName = cl_school_name($s->db);
        $classLabel = cl_class_label($class['name']);
        if ($lite) {
            require_once __DIR__ . '/lite.php';
            if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
            $snapshot = cl_lite_snapshot($s, $cfg, $class, $actor);
        } else $connect = $cfg['api_url'] . ' ' . $cfg['signal_url'];
    }
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
if ($lite) header("Content-Security-Policy: default-src 'none'; style-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'");
else header("Content-Security-Policy: default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' blob: data:; font-src 'self' blob: data:; worker-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; connect-src 'self' " . $connect);
$staff = $actor !== null && cl_is_staff($actor);
$actorLabel = match ($actor['office'] ?? $actor['role'] ?? '') {
    'student'=>'دانش‌آموز', 'teacher'=>'دبیر', 'deputy'=>'معاون / ناظم', 'admin'=>'مدیر', default=>'',
};
$panel = match ($actor['office'] ?? $actor['role'] ?? '') {
    'student'=>'student-panel.php', 'teacher'=>'teacher-panel.php', 'deputy'=>'deputy-panel.php', 'admin'=>'index.php?view=dashboard', default=>'index.php?view=login',
};
$roomPage = $class !== null && $errorMessage === null;
if ($roomPage && !$lite) cl_headers(true, ($staff && ($cfg['screen_share'] ?? false) === true) || cl_pupil_screen_enabled($cfg), ($cfg['camera_share'] ?? false) === true);
$pageTitle = $roomPage ? trim($schoolName . ' ' . $classLabel)
    : ($reports !== null ? 'حضور و غیاب کلاس مجازی' : 'کلاس مجازی');
?>
<!doctype html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title><?= cl_html($pageTitle) ?><?= $roomPage ? '' : ' | مدرسه' ?></title>
    <link rel="stylesheet" href="<?= cl_html(cl_asset($lite ? 'classroom-lite.css' : 'classroom.css')) ?>">
    <?php if ($roomPage && !$lite): ?>
    <script defer src="<?= cl_html(cl_asset('classroom-entry.js')) ?>"></script>
    <script defer src="<?= cl_html(cl_asset('board.js')) ?>"></script>
    <script defer src="<?= cl_html(cl_asset('room-attention.js')) ?>"></script>
    <script defer src="<?= cl_html(cl_asset('classroom.js')) ?>"></script>
    <?php endif; ?>
</head>
<body>
<main class="school-main<?= $roomPage ? ' room-main' : '' ?>">
    <?php if (!$roomPage): ?>
    <header class="banner">
        <nav aria-label="مسیرهای مدرسه">
            <a href="<?= cl_html(cl_site_href($panel)) ?>">بازگشت به پنل</a>
            <?php if ($actor): ?><a href="<?= cl_html(cl_href(['view'=>'classes'])) ?>">کلاس مجازی</a><?php endif; ?>
            <?php if ($staff): ?><a href="<?= cl_html(cl_href(['view'=>'reports'])) ?>">حضور و غیاب کلاس مجازی</a><?php endif; ?>
        </nav>
        <p class="eyebrow">مدرسه · یادگیری آنلاین</p>
        <h1><?= cl_html($pageTitle) ?></h1>
        <?php if ($actor): ?><p class="byline"><?= cl_html($actor['name'] . ' · ' . $actorLabel . ($year !== '' ? ' · سال ' . cl_persian_digits($year) : '')) ?></p><?php endif; ?>
    </header>
    <?php endif; ?>
    <?php if ($errorMessage !== null): ?>
        <section class="card"><p role="alert"><?= cl_html($errorMessage) ?></p>
        <?php if ($actor === null): ?><a href="<?= cl_html(cl_site_href('admin-login.php?tab=teacher')) ?>">ورود دبیر یا مدیر</a><?php endif; ?></section>
    <?php elseif ($reports !== null): cl_render_reports($reports); ?>
    <?php elseif ($class === null): ?>
        <section class="card">
            <h2>کلاس‌های سال جاری مدرسه</h2>
            <p class="meta"><time datetime="<?= cl_html($bellDate) ?>"><?= cl_html(cl_display_day($bellDate)) ?></time> · وقت تهران</p>
            <?php if ($friday): ?><p role="status">آزمایش جمعه تا نیمه‌شب تهران فعال است. کلاس را انتخاب کن؛ <?= (cl_friday_window($cfg, $now)['students'] ?? false) ? 'دانش‌آموزانِ همان کلاس پس از اتصال دبیر اجازهٔ ورود دارند.' : 'جلسه فقط برای دبیران و مسئولان مجاز است.' ?> آزمایش گزارش رسمی یا اعلان ندارد.</p><?php endif; ?>
            <?php if (!$classes): ?><p>برای این حساب در سال جاری کلاس قابل دسترسی نیست. عضویت یا برنامهٔ هفتگی را بررسی کن.</p><?php endif; ?>
            <div class="class-grid">
            <?php foreach ($classes as $c): ?><a class="class-link" href="<?= cl_html(cl_href(['class'=>$c['id']])) ?>"><strong><?= cl_html($c['name']) ?></strong><small><?= $staff ? 'برنامه و اتاق کلاس' : 'ورود به اتاق انتظار' ?></small></a><?php endforeach; ?>
            </div>
        </section>
    <?php elseif ($lite): ?>
        <?php require __DIR__ . '/lite-view.php'; ?>
    <?php else: ?>
        <?php require __DIR__ . '/room-view.php'; ?>
    <?php endif; ?>
</main>
</body>
</html>
