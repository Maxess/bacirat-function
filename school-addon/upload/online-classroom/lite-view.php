<?php
declare(strict_types=1);

$go = ['class'=>$class['id']];
$refresh = cl_href($go + ['view'=>'lite']);
?>
<header>
    <h1><?= cl_html($pageTitle) ?> · نمای سبک</h1>
    <nav aria-label="مسیرهای کلاس">
        <a href="<?= cl_html(cl_href($go + ['audio'=>'1'])) ?>">ورود با صدا، بدون دریافت ویدیو</a>
        <a href="<?= cl_html(cl_site_href($panel)) ?>">بازگشت به پنل</a>
    </nav>
    <p>این نما فقط خواندنی است: صدا، ویدیو، ارسال پیام و ثبت حضور ندارد. برای گرفتن آخرین وضعیت، «تازه‌کردن» را بزن.</p>
    <a class="lite-action" href="<?= cl_html($refresh) ?>">تازه‌کردن</a>
</header>
<?php if ($snapshot === null): ?>
    <section>
        <h2><span class="lite-spinner" aria-hidden="true"></span> در انتظار کلاس</h2>
        <p>هنوز زنگی با حضور تأییدشدهٔ دبیر یا مسئول مجاز در جریان نیست. کمی بعد دوباره تازه کن.</p>
    </section>
<?php else:
    $doc = $snapshot['doc'];
    $paths = cl_lite_paths($snapshot['ink'], $snapshot['undone']);
?>
    <section aria-labelledby="lite-board">
        <h2 id="lite-board"><?= cl_html($snapshot['subject']) ?> · تختهٔ کلاس</h2>
        <p>آخرین دریافت: <bdi dir="ltr"><?= cl_html(cl_tehran_clock($snapshot['at'])) ?></bdi> به وقت تهران</p>
        <?php if ($doc): ?>
            <p>صفحهٔ <?= cl_persian_digits((string)$snapshot['page']) ?> از فایل
                <bdi><?= cl_html($doc['name']) ?></bdi> · <?= cl_persian_digits((string)ceil($doc['bytes']/1024)) ?> کیلوبایت ·
                <a href="<?= cl_html(cl_href($go + ['route'=>'content','light'=>'1','lesson'=>$snapshot['lesson'],'epoch'=>$snapshot['epoch'],'doc'=>$doc['id']])) ?>">دریافت فایل اصلی</a>
            </p>
        <?php endif; ?>
        <?php if ($paths === null): ?>
            <p>پیش‌نمایش خطوط این صفحه در نمای سبک قابل نمایش نیست. در صورت امکان از نمای کامل تخته استفاده کن.</p>
        <?php elseif ($paths): ?>
            <?php if ($doc): ?><p>خطوط روی فایل، جدا از تصویر فایل:</p><?php endif; ?>
            <svg class="lite-board" viewBox="0 0 10000 10000" preserveAspectRatio="none" role="img" aria-label="پیش‌نمایش خطوط ثبت‌شدهٔ تخته"
                 xmlns="http://www.w3.org/2000/svg" fill="none" stroke-linecap="round" stroke-linejoin="round">
                <?php foreach ($paths as $path): ?>
                    <path d="<?= cl_html($path['d']) ?>" stroke="<?= cl_html($path['color']) ?>" stroke-width="<?= $path['width'] ?>" />
                <?php endforeach; ?>
            </svg>
        <?php elseif (!$doc): ?><p>تخته فعلاً خالی است.</p><?php endif; ?>
    </section>
    <section aria-labelledby="lite-chat">
        <h2 id="lite-chat">آخرین پیام‌های کلاس</h2>
        <?php if (!$snapshot['chat']): ?><p>هنوز پیامی نوشته نشده است.</p><?php else: ?>
        <ul>
            <?php foreach ($snapshot['chat'] as $post): ?>
                <li><strong><bdi><?= cl_html($post['by']) ?></bdi></strong> · <?= cl_html(cl_tehran_clock($post['at'])) ?>
                    <p dir="auto"><?= cl_html($post['text']) ?></p></li>
            <?php endforeach; ?>
        </ul>
        <?php endif; ?>
    </section>
<?php endif; ?>
