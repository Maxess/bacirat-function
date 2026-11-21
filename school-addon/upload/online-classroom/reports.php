<?php
declare(strict_types=1);
require_once __DIR__ . '/notifications.php';

function cl_report_bucket(array $actor): string
{
    if (!cl_is_staff($actor)) throw new ClassroomError(403, 'گزارش حضور فقط برای مسئولان مجاز مدرسه است.');
    return $actor['office'] !== null ? 'reports' : 'reports:teacher-' . $actor['id'];
}

function cl_report_detail(ClassroomStore $s, array $actor, string $id): ?array
{
    cl_report_bucket($actor);
    $row = $s->get('lesson', $id);
    if (!$row || $row['phase'] !== 'finished' || ($actor['office'] === null && $row['teacher'] !== $actor['id'])) return null;
    return $row;
}

function cl_reports(ClassroomStore $s, array $actor): array
{
    $bucket = cl_report_bucket($actor);
    $id = $_GET['lesson'] ?? null;
    if ($id !== null) {
        $report = is_string($id) && preg_match('/^[a-f0-9]{32}$/D', $id) ? cl_report_detail($s, $actor, $id) : null;
        if (!$report) throw new ClassroomError(404, 'کارنامهٔ حضور این زنگ در دسترس نیست.');
        return ['report'=>$report, 'delivery'=>$actor['office'] !== null ? $s->get('delivery', $id) : null];
    }
    $before = $_GET['before'] ?? null;
    $cursor = is_string($before) && preg_match('/^[0-9]{12}:[a-f0-9]{32}$/D', $before) ? $before : '999999999999:';
    $rows = $s->rows($bucket, 31, $cursor, true);
    $more = count($rows) > 30;
    if ($more) array_pop($rows);
    return ['reports'=>$rows, 'next'=>$more ? array_key_last($rows) : null];
}

function cl_render_reports(array $data): void
{
    if (isset($data['report'])) {
        $r = $data['report'];
        $times = cl_lesson_times($r); ?>
        <section class="card report-summary">
            <a href="<?= cl_html(cl_href(['view'=>'reports'])) ?>">← فهرست زنگ‌های برگزارشده</a>
            <h2><?= cl_html($r['class_name'] . ' · ' . $r['subject'] . (isset($r['repeat_of']) ? ' · برگزاری مجدد' : '')) ?></h2>
            <p><?= cl_html(cl_persian_digits($r['year']) . ' | ' . cl_display_day($r['date']) . ' | ' . cl_persian_digits($r['period']) . ' | ' . $r['teacher_name']) ?></p>
            <?php if ($times !== ''): ?><p>ساعت برگزاری (وقت تهران): <?= cl_html($times) ?></p><?php endif; ?>
            <p>مدت برگزاری فعال: <strong><bdi dir="ltr"><?= cl_persian_digits(cl_duration($r['active_seconds'])) ?></bdi></strong> <span class="meta">(بدون زمان انتظار یا وقفه)</span></p>
            <?php if (!empty($data['delivery'])): $d = $data['delivery']; ?>
            <p class="meta">نتیجهٔ اعلان‌ها: <?= (int)($d['sent'] ?? 0) ?> ارسال شده · <?= (int)($d['failed'] ?? 0) ?> خطا · <?= (int)($d['uncertain'] ?? 0) ?> نامشخص · <?= (int)($d['skipped'] ?? 0) ?> رد شده</p>
            <?php endif; ?>
        </section>
        <section class="card table-wrap">
            <table><caption>حضور دانش‌آموزان در این زنگ</caption><thead><tr><th scope="col">دانش‌آموز</th><th scope="col">مدت حضور</th><th scope="col">درصد از زمان فعال</th></tr></thead>
            <tbody><?php foreach ($r['roster'] as $who=>$name): $seconds = $r['seconds'][$who] ?? 0; ?>
            <tr><th scope="row"><?= cl_html($name) ?></th><td><bdi dir="ltr"><?= cl_persian_digits(cl_duration($seconds)) ?></bdi></td>
                <td><?= $r['active_seconds'] ? ((int)round(100 * $seconds / $r['active_seconds']) . '٪') : '—' ?></td></tr>
            <?php endforeach; ?></tbody></table>
            <?php if (!$r['roster']): ?><p>برای این زنگ دانش‌آموزی در فهرست کلاس ثبت نشده بود.</p><?php endif; ?>
        </section>
        <?php return;
    } ?>
    <section class="card">
        <h2>زنگ‌های برگزارشده</h2>
        <p class="meta">حضور هر دانش‌آموز نسبت به مدت واقعی برگزاری محاسبه می‌شود، نه مدت باز بودن صفحه یا اتاق انتظار.</p>
        <?php if (!$data['reports']): ?><p>هنوز زنگ پایان‌یافته‌ای برای این حساب ثبت نشده است.</p><?php endif; ?>
        <ul class="report-list">
        <?php foreach ($data['reports'] as $r): $times = cl_lesson_times($r); ?>
            <li><a href="<?= cl_html(cl_href(['view'=>'reports','lesson'=>$r['id']])) ?>"><strong><?= cl_html($r['class_name'] . ' · ' . $r['subject'] . (isset($r['repeat_of']) ? ' · برگزاری مجدد' : '')) ?></strong>
                <span><?= cl_html(cl_display_day($r['date']) . ($times !== '' ? ' · ' . $times : '') . ' | ' . cl_persian_digits($r['period']) . ' | ' . $r['teacher_name'] . ' | ' . cl_persian_digits($r['year'])) ?></span></a></li>
        <?php endforeach; ?>
        </ul>
        <?php if ($data['next'] !== null): ?><a href="<?= cl_html(cl_href(['view'=>'reports','before'=>$data['next']])) ?>">زنگ‌های قدیمی‌تر</a><?php endif; ?>
    </section>
<?php }
