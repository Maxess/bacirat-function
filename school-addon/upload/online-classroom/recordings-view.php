<?php
declare(strict_types=1);

// Entered from index.php only; Baci's active super-admin is the only mapping editor.
$notice = $errorMessage = null;
$configuration = [];
$selected = null;
$classes = [];
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
try {
    $year = cl_current_year($s->db);
    $admin = cl_school_user($s->db, $_SESSION ?? [], $year);
    if ($admin['role'] !== 'admin' || cl_query($s->db, 'SELECT role FROM admins WHERE id=? AND status=1', [$admin['id']])->fetchColumn() !== 'super_admin') {
        throw new ClassroomError(403, 'تخصیص گروه‌های کلاس فقط برای مدیر کل مدرسه مجاز است.');
    }
    if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
    $configuration = cl_context();
    $classes = cl_classes($s->db, $year);
    if ($method === 'POST') {
        if ((isset($_SERVER['HTTP_ORIGIN']) && $_SERVER['HTTP_ORIGIN'] !== $cfg['site_origin'])
            || !is_string($_POST['csrf'] ?? null) || !hash_equals($configuration['csrf'], $_POST['csrf'])) {
            throw new ClassroomError(403, 'نشست فرم معتبر نیست؛ صفحه را دوباره باز کن.');
        }
        $id = cl_id($_POST['class'] ?? null);
        $selected = $classes[$id] ?? null;
        $group = $_POST['group'] ?? null;
        if (!$selected || !is_string($group) || strlen($group) > 80 || ($_POST['year'] ?? null) !== $year) {
            throw new ClassroomError(409, 'کلاس یا گروه برای سال جاری معتبر نیست.');
        }
        cl_rec_set_link($s, $cfg, $selected, $group);
        header('Location: ' . cl_href(['view'=>'recordings','class'=>$id,'saved'=>'yes']), true, 303);
        return;
    }
    if ($method !== 'GET' && $method !== 'HEAD') throw new ClassroomError(405, 'نوع درخواست مجاز نیست.');
    $selected = $classes[cl_id($_GET['class'] ?? null)] ?? ($classes[array_key_first($classes)] ?? null);
    if (($_GET['saved'] ?? null) === 'yes') $notice = 'گروه کلاس ذخیره شد.';
} catch (ClassroomError $error) {
    http_response_code($error->status);
    $errorMessage = $error->getMessage();
} catch (Throwable $error) {
    http_response_code(503);
    $errorMessage = 'تنظیمات گروه و ضبط موقتاً در دسترس نیست.';
    error_log('School classroom: recording settings unavailable (' . get_class($error) . ')');
} finally {
    if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
}
header("Content-Security-Policy: default-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'");
$current = $selected ? cl_rec_link($s, $selected) : null;
$known = $current && cl_rec_bot($current['provider']) && cl_rec_inventory($s->db, $current['provider'], $current['chat']);
$labels = ['recording'=>'در حال آماده‌سازی یا ضبط','queued'=>'در صف ارسال','delivered'=>'ارسال همهٔ بخش‌ها تأیید شد',
    'discarded'=>'ارسال نشد','failed'=>'ارسال ناموفق','uncertain'=>'نتیجهٔ ارسال نامشخص؛ تکرار خودکار نمی‌شود'];
?>
<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>گروه‌های پیام‌رسان و ضبط کلاس</title><link rel="stylesheet" href="<?= cl_html(cl_asset('classroom.css')) ?>"></head>
<body><main class="school-main">
    <header class="banner"><nav><a href="<?= cl_html(cl_site_href('other-settings.php?hub_tab=plugins')) ?>">تنظیمات افزونه</a>
        <a href="<?= cl_html(cl_href(['view'=>'classes'])) ?>">کلاس‌ها</a></nav><h1>گروه‌های پیام‌رسان و ضبط کلاس</h1></header>
    <?php if ($errorMessage !== null): ?><p class="card" role="alert"><?= cl_html($errorMessage) ?></p><?php endif; ?>
    <?php if ($configuration): ?>
    <section class="card">
        <h2>گروه هر کلاس · سال <?= cl_html(cl_persian_digits($year)) ?></h2>
        <p role="status">سرویس ضبط: <?= cl_rec_health($s, $cfg, time()) ? 'آماده' : (cl_rec_enabled($cfg) ? 'متصل نیست' : 'خاموش') ?></p>
        <?php if ($notice): ?><p role="status"><?= cl_html($notice) ?></p><?php endif; ?>
        <p>برای هر کلاس یک گروه انتخاب کن. مدیر بودن ربات شرط نیست، اما برای ارسال باید اجازهٔ انتشار ویدیو داشته باشد. اگر گروه در فهرست نیست، در همان گروه ربات را خطاب کن یا گروه را از «گفتگوهای ربات» در پنل پیام‌رسان ثبت کن.</p>
        <?php if (!$selected): ?><p>برای سال جاری کلاس قابل تخصیص پیدا نشد.</p><?php else: ?>
        <form class="actions" method="get" action="school-plugins.php">
            <input type="hidden" name="plugin" value="school-classroom"><input type="hidden" name="view" value="recordings">
            <label>کلاس <select name="class"><?php foreach ($classes as $class): ?>
                <option value="<?= $class['id'] ?>"<?= $class['id'] === $selected['id'] ? ' selected' : '' ?>><?= cl_html($class['name']) ?></option>
            <?php endforeach; ?></select></label><button type="submit" class="secondary">نمایش تخصیص</button>
        </form>
        <form method="post" action="<?= cl_html(cl_href(['view'=>'recordings','class'=>$selected['id']])) ?>">
            <input type="hidden" name="csrf" value="<?= cl_html($configuration['csrf']) ?>">
            <input type="hidden" name="year" value="<?= cl_html($year) ?>"><input type="hidden" name="class" value="<?= $selected['id'] ?>">
            <p>کلاس <?= cl_html($selected['name']) ?> · گروه فعلی: <?= $current ? cl_html(CL_REC_PROVIDERS[$current['provider']] . ' — ' . $current['title']) : 'تخصیص‌نیافته' ?></p>
            <div class="actions"><label>گروه ثبت‌شده <select name="group">
                <?php if ($current && !$known): ?><option value="" selected disabled>گروه قبلی فعلاً در فهرست نیست</option><?php endif; ?>
                <option value="">بدون گروه</option>
                <?php foreach (CL_REC_PROVIDERS as $provider=>$label): if (!cl_rec_bot($provider)) continue; ?>
                    <optgroup label="<?= $label ?>">
                    <?php foreach (cl_rec_inventory($s->db, $provider) as $entry):
                        $id = (string)$entry['chat_id']; if (!cl_rec_chat($provider, $id)) continue; ?>
                        <option value="<?= cl_html($provider . ':' . $id) ?>"<?= [$provider,$id] === [$current['provider'] ?? null,$current['chat'] ?? null] ? ' selected' : '' ?>>
                            <?= cl_html((cl_safe_label((string)$entry['title'], 120) ?: 'گروه') . ' · ' . $id) ?></option>
                    <?php endforeach; ?>
                    </optgroup>
                <?php endforeach; ?>
            </select></label><button type="submit">ذخیره یا برداشتن تخصیص</button></div>
        </form>
        <?php endif; ?>
    </section>
    <?php if ($selected): ?>
    <section class="card"><h2>وضعیت ضبط کلاس انتخاب‌شده</h2>
        <?php
        $jobs = array_filter(array_map(static fn($index) => $s->get('recording', $index['id']),
            $s->rows(cl_rec_history($selected), 12, str_repeat('9', 13), true)));
        ?>
        <?php if (!$jobs): ?><p>هنوز ضبطی ثبت نشده است.</p><?php else: ?><ul>
        <?php foreach ($jobs as $job): ?>
            <li><?= cl_html(cl_display_day(cl_day($job['begin'])->format('Y-m-d')) . '، ' . cl_tehran_clock($job['begin']) . ' · ' . $labels[$job['state']]) ?>
                <?= $job['total'] ? ' (' . cl_persian_digits($job['sent'] . '/' . $job['total']) . ')' : '' ?>
                <?= ($job['reason'] ?? '') === 'destination' ? ' · گروه یا دسترسی ربات تأیید نشد' : '' ?>
                <?= ($job['reason'] ?? '') === 'capture' ? ' · ضبط کامل نشد' : '' ?>
                <?= isset($job['cleaned']) ? ' · فایل موقت VPS پاک شد' : '' ?></li>
        <?php endforeach; ?></ul><?php endif; ?>
    </section>
    <?php endif; endif; ?>
</main></body></html>
