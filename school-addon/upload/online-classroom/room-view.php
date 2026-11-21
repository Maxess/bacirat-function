<?php
declare(strict_types=1);

// Small, local icons; no dependency on a conference app or icon CDN.
function cl_room_icon(string $name): string
{
    $mark = match ($name) {
        'board' => '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21l4-4 4 4M7 12l3-3 3 2 3-4"/>',
        'clock' => '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        'mic' => '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11v1a7 7 0 0 0 14 0v-1m-7 8v3m-3 0h6"/>',
        'mic-off' => '<path d="m3 3 18 18M9 9v3a3 3 0 0 0 5 2m1-4V6a3 3 0 0 0-5-2M5 11v1a7 7 0 0 0 11 5m3-6v1a7 7 0 0 1-.5 2.6M12 19v3m-3 0h6"/>',
        'camera' => '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/>',
        'camera-off' => '<path d="m3 3 18 18M3 7v9a2 2 0 0 0 2 2h11M9 6h5a2 2 0 0 1 2 2v2l5-3v9"/>',
        'menu' => '<path d="M4 6h16M4 12h16M4 18h16"/>',
        'settings' => '<path d="M4 6h16M4 12h16M4 18h16M8 4v4m8 2v4m-5 2v4"/>',
        'wake' => '<rect x="3" y="5" width="18" height="13" rx="2"/><path d="M9 21h6m-1-13-4 4h4l-2 3"/>',
        'laser' => '<path d="m4 20 8-8m1-6V3m4 6 3-2m-4 6h5"/><circle cx="12" cy="12" r="2"/>',
        'present' => '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M8 22h8m-4-4v4m0-7V8m-3 3 3-3 3 3"/>',
        'sound' => '<path d="M11 5 6 9H3v6h3l5 4V5Zm4.5 4a4.2 4.2 0 0 1 0 6m3-9a8 8 0 0 1 0 12"/>',
        'people' => '<circle cx="9" cy="7.5" r="3"/><path d="M2.5 20v-2a4 4 0 0 1 4-4h5a4 4 0 0 1 4 4v2M16 5a3 3 0 0 1 0 6m2 3a4 4 0 0 1 3.5 4v2"/>',
        'chat' => '<path d="M21 11.5A8.5 8.5 0 0 1 12.5 20H5l1.3-3A8.5 8.5 0 1 1 21 11.5ZM8 10h8m-8 4h5"/>',
        'exit' => '<path d="M10 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h5m-1-8h12m-4-4 4 4-4 4"/>',
        'finish' => '<rect x="5" y="5" width="14" height="14" rx="2"/>',
        'pen' => '<path d="m3 21 1-5L16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 2Zm11-15 3 3"/>',
        'erase' => '<path d="m8 20-5-5a2.1 2.1 0 0 1 0-3L12 3a2.1 2.1 0 0 1 3 0l5 5a2.1 2.1 0 0 1 0 3l-9 9m-4-12 8 8m-7 4h13"/>',
        'pan' => '<path d="M8 12V6a1.5 1.5 0 0 1 3 0v5-7a1.5 1.5 0 0 1 3 0v7-5a1.5 1.5 0 0 1 3 0v6-3a1.5 1.5 0 0 1 3 0v6a6 6 0 0 1-6 6h-2a5 5 0 0 1-4-2l-4-5a1.6 1.6 0 0 1 2.5-2Z"/>',
        'upload' => '<path d="M12 16V4m-5 5 5-5 5 5M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
        'trash' => '<path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5"/>',
        'expand' => '<path d="M9 4H4v5m11-5h5v5m0 6v5h-5m-6 0H4v-5"/>',
        'shrink' => '<path d="M4 9h5V4m6 0v5h5m0 6h-5v5m-6 0v-5H4"/>',
        'next' => '<path d="m15 6-6 6 6 6"/>',
        'prev' => '<path d="m9 6 6 6-6 6"/>',
        'plus' => '<path d="M12 5v14M5 12h14"/>',
        'minus' => '<path d="M5 12h14"/>',
        'check' => '<path d="m5 12 4 4L19 6"/>',
        'send' => '<path d="m21 4-5 16-4-7-8-3 17-6Zm-9 9 5-5"/>',
        default => '',
    };
    return '<svg class="room-icon icon-' . $name . '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' . $mark . '</svg>';
}
?>
<section id="classroom" class="room-shell" data-api="<?= cl_html(cl_href(['route'=>'api'])) ?>" data-csrf="<?= cl_html(cl_context()['csrf']) ?>"
  data-class="<?= $class['id'] ?>" data-bell-date="<?= cl_html($bellDate) ?>" data-moderator="<?= $staff ? 'yes' : 'no' ?>"
  data-content="<?= cl_html(cl_href(['route'=>'content','class'=>$class['id']])) ?>"
  data-pdf="<?= cl_html(cl_pdf_href('runtime/pdf.min.mjs')) ?>" data-pdf-worker="<?= cl_html(cl_pdf_href('runtime/pdf.worker.min.mjs')) ?>"
  data-pdf-data="<?= cl_html(cl_pdf_href()) ?>">
    <header class="room-top">
        <h1 class="room-name" title="<?= cl_html($pageTitle) ?>">
            <?php if ($schoolName !== ''): ?><bdi class="school-name"><?= cl_html($schoolName) ?></bdi><?php endif; ?>
            <bdi class="class-name" dir="<?= preg_match('~^[0-9]+/[0-9]+$~D', $classLabel) ? 'ltr' : 'auto' ?>"><?= cl_html($classLabel) ?></bdi>
        </h1>
        <div class="room-controls" role="group" aria-label="کنترل‌های اتاق">
            <button type="button" id="microphone" class="icon-button mic-toggle" aria-pressed="false" aria-label="روشن‌کردن میکروفون" title="روشن‌کردن میکروفون" disabled>
                <?= cl_room_icon('mic-off') . cl_room_icon('mic') ?>
            </button>
            <button type="button" id="camera" class="icon-button camera-toggle" aria-pressed="false" aria-label="روشن‌کردن دوربین" title="روشن‌کردن دوربین" hidden>
                <?= cl_room_icon('camera-off') . cl_room_icon('camera') ?>
            </button>
            <button type="button" id="screen-toggle" class="icon-button screen-toggle" aria-pressed="false" aria-label="اشتراک صفحه" title="اشتراک صفحه" disabled hidden>
                <?= cl_room_icon('present') ?><?php if (!$staff): ?><span id="screen-action">اشتراک صفحه</span><?php endif; ?>
            </button>
            <button type="button" id="enable-audio" class="icon-button audio-toggle" aria-label="فعال‌کردن پخش صدا" title="فعال‌کردن پخش صدا" hidden><?= cl_room_icon('sound') ?></button>
            <button type="button" id="toggle-people" class="icon-button" aria-label="حاضران" aria-controls="people-panel" aria-expanded="<?= $staff ? 'true' : 'false' ?>" title="نمایش یا بستن فهرست حاضران" disabled><?= cl_room_icon('people') ?></button>
            <button type="button" id="toggle-chat" class="icon-button" aria-label="گفت‌وگوی کلاس" aria-controls="chat-panel" aria-expanded="true" title="نمایش یا بستن گفت‌وگو" disabled><?= cl_room_icon('chat') ?></button>
        </div>
        <div class="room-indicators">
            <div class="room-signal">
                <span class="status-dot" id="status-dot" title="اتاق انتظار"><span class="sr-only" id="status" role="status">اتاق انتظار</span></span>
                <span class="lesson-start" id="lesson-start" title="ساعت شروع کلاس به وقت تهران" hidden>شروع <bdi dir="ltr" id="lesson-start-time"></bdi></span>
            </div>
            <?php if ($staff): ?>
            <div class="room-permissions" id="room-permissions">
                <button type="button" class="icon-button" id="toggle-access" aria-label="منوی کلاس" title="منوی کلاس" aria-expanded="false" aria-controls="access-menu"><?= cl_room_icon('menu') ?></button>
                <div class="access-menu" id="access-menu" role="group" aria-labelledby="access-title" hidden>
                    <p id="access-title">منوی کلاس</p>
                    <div id="access-features" hidden>
                        <?php foreach (['mic'=>['mic','میکروفون'], 'camera'=>['camera','دوربین'], 'board'=>['pen','نوشتن روی تخته']] as $key=>[$icon,$label]): ?>
                        <div class="access-row" id="access-<?= $key ?>">
                            <span class="access-name"><?= cl_room_icon($icon) ?> <?= cl_html($label) ?></span>
                            <button type="button" class="subtle-button" id="all-<?= $key ?>-give" aria-label="دادن اجازهٔ <?= cl_html($label) ?> به همه">اجازه به همه</button>
                            <button type="button" class="subtle-button" id="all-<?= $key ?>-cut" aria-label="لغو اجازهٔ <?= cl_html($label) ?> برای همه">لغو برای همه</button>
                        </div>
                        <?php endforeach; ?>
                        <p class="access-hint" id="camera-unavailable" hidden>وبکم در تنظیمات یا سرور رسانهٔ این اتاق فعال نیست.</p>
                        <p class="access-hint">میکروفون و دوربین را هر نفر خودش روشن می‌کند.</p>
                        <p class="access-hint" id="pupil-screen-hint" hidden>برای اجازهٔ اشتراک صفحه، دکمهٔ کنار نام دانش‌آموز را بزن. در هر لحظه فقط یک نفر می‌تواند ارائه کند.</p>
                    </div>
                    <div class="access-actions">
                        <?php if (cl_quality_enabled($cfg)): ?>
                        <button type="button" id="quality-open" class="icon-button" aria-haspopup="dialog" aria-controls="quality-dialog" hidden><?= cl_room_icon('settings') ?><span>تنظیمات</span></button>
                        <?php endif; ?>
                        <a id="leave" class="icon-button leave-button" href="<?= cl_html(cl_site_href($panel)) ?>" title="بازگشت به پنل بدون بستن کلاس برای دیگران"><?= cl_room_icon('exit') ?><span>خروج از اتاق</span></a>
                        <button type="button" id="close" class="icon-button finish-button" hidden><?= cl_room_icon('finish') ?><span id="close-label">پایان کلاس برای همه</span></button>
                    </div>
                </div>
            </div>
            <template id="permission-icons"><?= cl_room_icon('mic') . cl_room_icon('mic-off') . cl_room_icon('camera') . cl_room_icon('camera-off') . cl_room_icon('pen') . cl_room_icon('present') ?></template>
            <?php else: ?>
            <a id="leave" class="icon-button leave-button" href="<?= cl_html(cl_site_href($panel)) ?>" aria-label="خروج از اتاق" title="خروج از اتاق و بازگشت به پنل"><?= cl_room_icon('exit') ?><span>خروج</span></a>
            <?php endif; ?>
        </div>
        <?php if (!$staff): ?><p id="screen-hint" class="screen-hint" role="status" hidden></p><?php endif; ?>
    </header>
    <?php if ($staff && cl_quality_enabled($cfg)): ?>
    <dialog class="quality-dialog" id="quality-dialog" aria-labelledby="quality-title" aria-describedby="quality-help">
        <form id="quality-form">
            <h2 id="quality-title">تنظیمات کیفیت تصویر</h2>
            <p id="quality-help">«پیش‌فرض افزونه» از تنظیمات مدیر استفاده می‌کند. انتخاب‌ها برای زنگ‌های بعدی همین کلاس هم می‌مانند و مجوز ارسال نمی‌دهند. اگر تصویری در حال ارسال باشد و کیفیتش تغییر کند، فرستنده باید آن را دوباره روشن کند.</p>
            <div class="quality-fields">
                <?php foreach (CL_QUALITY_FIELDS as $key=>$label): ?>
                <label for="quality-<?= $key ?>"><?= cl_html($label) ?>
                    <select id="quality-<?= $key ?>" name="<?= $key ?>" required>
                        <?php foreach (cl_quality_options() as $level=>$name): ?>
                        <option value="<?= $level ?>"><?= cl_html($name) ?></option>
                        <?php endforeach; ?>
                    </select>
                </label>
                <?php endforeach; ?>
            </div>
            <p id="quality-message" role="status" hidden></p>
            <div class="actions">
                <button type="submit" id="quality-save">ذخیره برای این کلاس</button>
                <button type="button" id="quality-cancel" class="secondary">بستن</button>
            </div>
        </form>
    </dialog>
    <?php endif; ?>
    <?php if ($staff): ?>
    <div class="staff-actions actions" id="staff-start">
        <label for="slot" class="sr-only">انتخاب زنگ</label>
        <select id="slot"><option value="">انتخاب زنگ امروز</option>
            <?php foreach ($choices as $choice): ?><option value="<?= cl_html((string)$choice['id']) ?>"><?= cl_html(cl_persian_digits($choice['label'])) ?></option><?php endforeach; ?>
        </select>
        <button type="button" id="open" disabled>شروع زنگ</button>
    </div>
    <?php endif; ?>
    <p id="notice" role="status" class="room-toast" hidden></p>
    <section class="waiting board-grid" id="waiting" aria-labelledby="wait-title">
        <div class="waiting-copy">
            <span class="waiting-icon"><?= cl_room_icon('clock') ?></span>
            <h2 id="wait-title"><?= $staff ? 'در انتظار شروع زنگ' : 'در انتظار ورود دبیر یا مسئول مدرسه' ?></h2>
            <p id="wait-message"><?= $staff ? 'در حال بررسی زنگ‌های امروز…' : 'وقتی اتصال دبیر تأیید شود، خودکار وارد کلاس می‌شوی.' ?></p>
            <p class="wait-hint"><?= cl_room_icon('clock') ?> زمان انتظار در تایمر حضور حساب نمی‌شود.</p>
            <?php if (!$staff): ?>
            <button type="button" id="join" hidden>بازگشت به اتاق انتظار</button>
            <?php endif; ?>
        </div>
    </section>
    <div id="live-panel" hidden>
        <section class="camera-strip" id="camera-strip" aria-label="دوربین‌های روشن" hidden></section>
        <dialog class="camera-preview" id="camera-preview" aria-labelledby="camera-preview-name">
            <header>
                <h2 id="camera-preview-name" dir="auto"></h2>
                <form method="dialog"><button type="submit" class="icon-button" autofocus>بستن تصویر</button></form>
            </header>
            <video id="camera-preview-video" muted playsinline autoplay disablepictureinpicture></video>
        </dialog>
        <section class="screen-panel" id="screen-panel" aria-labelledby="screen-label" hidden>
            <h2 id="screen-label"></h2>
            <video id="screen-video" muted playsinline autoplay controls disablepictureinpicture></video>
        </section>
        <section class="board-card" id="board" aria-label="تختهٔ کلاس">
            <div class="board-toolbar">
                <div class="board-heading"><?= cl_room_icon('board') ?><h2>تختهٔ کلاس</h2></div>
                <div class="ink-tools" id="ink-tools" role="group" aria-label="ابزار نوشتن" hidden>
                    <button type="button" class="icon-button" id="pen" aria-pressed="true" title="قلم" aria-label="قلم"><?= cl_room_icon('pen') ?></button>
                    <button type="button" class="icon-button" id="eraser" aria-pressed="false" aria-label="<?= $staff ? 'پاک‌کن خط کامل' : 'پاک‌کن فقط خط‌های خودم' ?>" title="<?= $staff ? 'پاک‌کن خط کامل' : 'پاک‌کن فقط خط‌های خودم' ?>" <?= $staff ? '' : 'hidden' ?>><?= cl_room_icon('erase') ?></button>
                    <?php if ($staff): ?><button type="button" class="icon-button" id="board-laser" aria-pressed="false" title="نشانگر لیزری موقت" aria-label="نشانگر لیزری موقت"><?= cl_room_icon('laser') ?></button><?php endif; ?>
                    <button type="button" class="icon-button" id="pan" aria-pressed="false" title="پیمایش تخته" aria-label="پیمایش تخته"><?= cl_room_icon('pan') ?></button>
                    <label class="color-control" title="رنگ قلم"><span class="sr-only">رنگ قلم</span><input type="color" id="ink-color" value="#278bc7"></label>
                    <label class="size-control" title="ضخامت قلم"><span class="sr-only">ضخامت قلم</span><input type="range" id="ink-size" min="1" max="36" value="4"></label>
                    <?php if ($staff): ?><button type="button" class="icon-button" id="board-clear" title="پاک‌کردن همهٔ نوشته‌های این صفحه" aria-label="پاک‌کردن همهٔ نوشته‌های این صفحه"><?= cl_room_icon('trash') ?></button><?php endif; ?>
                </div>
                <?php if ($staff): ?>
                <div class="page-tools" role="group" aria-label="صفحه‌های فایل">
                    <button type="button" class="icon-button" id="page-prev" aria-label="صفحهٔ قبلی" title="صفحهٔ قبلی" disabled><?= cl_room_icon('prev') ?></button>
                    <label><span class="sr-only">شمارهٔ صفحه</span><input type="number" id="page-number" min="1" value="1" dir="ltr" disabled></label>
                    <button type="button" class="icon-button" id="page-go" aria-label="رفتن به صفحه" title="رفتن به صفحه" disabled><?= cl_room_icon('check') ?></button>
                    <button type="button" class="icon-button" id="page-next" aria-label="صفحهٔ بعدی" title="صفحهٔ بعدی" disabled><?= cl_room_icon('next') ?></button>
                </div>
                <button type="button" class="icon-button file-toggle" id="toggle-files" aria-controls="board-files" aria-expanded="false" title="فایل‌های تخته"><?= cl_room_icon('upload') ?><span>فایل</span></button>
                <?php endif; ?>
                <div class="view-tools" role="group" aria-label="اندازهٔ نمایش تخته">
                    <button type="button" id="board-zoom-out" class="icon-button" aria-label="کوچک‌نمایی تخته" title="کوچک‌نمایی"><?= cl_room_icon('minus') ?></button>
                    <button type="button" id="board-zoom" class="zoom-value" dir="ltr" aria-label="بازنشانی بزرگ‌نمایی به ۱۰۰ درصد" title="بازنشانی بزرگ‌نمایی">۱۰۰٪</button>
                    <button type="button" id="board-zoom-in" class="icon-button" aria-label="بزرگ‌نمایی تخته" title="بزرگ‌نمایی"><?= cl_room_icon('plus') ?></button>
                    <button type="button" id="board-expand" class="icon-button" aria-label="نمای بزرگ تخته" aria-pressed="false" title="نمای بزرگ تخته"><?= cl_room_icon('expand') . cl_room_icon('shrink') ?></button>
                    <?php if ($actor['role'] === 'teacher'): ?>
                    <details id="wake-control" class="wake-control" hidden>
                        <summary class="icon-button" aria-label="تنظیم روشن‌ماندن صفحهٔ کلاس" title="تنظیم روشن‌ماندن صفحهٔ کلاس"><?= cl_room_icon('wake') ?></summary>
                        <div class="wake-menu">
                            <label for="wake-mode">روشن‌ماندن صفحه در این زنگ</label>
                            <select id="wake-mode" aria-describedby="wake-help wake-status">
                                <option value="live">تا وقتی کلاس در جریان است</option>
                                <option value="idle">رهاسازی بعد از ۵ دقیقه بدون تعامل</option>
                                <option value="system">طبق تنظیمات هر دستگاه</option>
                            </select>
                            <p id="wake-help">برای همهٔ زبانه‌های قابل‌دیدن این زنگ. زمان خاموشی یا قفل را خود دستگاه تعیین می‌کند.</p>
                            <p id="wake-status" role="status"></p>
                        </div>
                    </details>
                    <?php endif; ?>
                </div>
            </div>
            <?php if ($staff): ?>
            <div class="board-files" id="board-files" hidden>
                <form id="board-upload" class="actions">
                    <label>تصویر یا PDF <input type="file" id="board-file" accept="image/png,image/jpeg,image/webp,application/pdf" required></label>
                    <button type="submit">افزودن به تخته</button>
                </form>
                <label>زمینهٔ تخته <select id="board-doc"><option value="">تختهٔ خالی</option></select></label>
            </div>
            <?php endif; ?>
            <div class="board-file-receive" id="board-file-receive" hidden>
                <button type="button" id="board-load-file" class="subtle-button">نمایش فایل تخته</button>
                <span id="board-file-size"></span>
            </div>
            <div class="board-workspace">
                <div id="board-scroll" class="board-scroll board-grid" tabindex="0" aria-label="نمای تخته؛ زوم و پیمایش با دو انگشت یا دکمه‌های بزرگ‌نمایی">
                    <div class="board-bed"><div id="board-stage" class="board-stage">
                        <canvas id="board-paper" aria-label="صفحهٔ فایل یا تصویر تخته"></canvas>
                        <canvas id="board-ink" aria-label="نوشته‌های روی تخته"></canvas>
                        <span id="eraser-tip" class="eraser-tip" aria-hidden="true" hidden></span>
                        <div id="board-writers" class="board-writers" aria-hidden="true"></div>
                        <svg class="laser-trails" viewBox="0 0 10000 10000" preserveAspectRatio="none" aria-hidden="true" focusable="false">
                            <g id="board-laser-remote-trail"></g><g id="board-laser-local-trail"></g>
                        </svg>
                        <span id="board-laser-remote" class="laser-dot" aria-hidden="true" hidden></span>
                        <span id="board-laser-local" class="laser-dot" aria-hidden="true" hidden></span>
                    </div></div>
                </div>
            </div>
            <div class="board-notices">
                <?php if (!$staff): ?><p id="board-screen-hint" class="board-toast" role="status" hidden></p><?php endif; ?>
                <p id="board-join-toast" class="board-toast join-toast" role="status" hidden></p>
                <p id="board-chat-toast" class="board-toast" role="status" hidden></p>
            </div>
            <div class="board-footer" id="board-footer" hidden>
                <bdi class="board-page" id="board-page-label" dir="ltr" aria-label="شمارهٔ صفحهٔ تخته"></bdi>
            </div>
        </section>
        <section class="speaker-strip" id="speaker-strip" aria-label="در حال صحبت" hidden>
            <?= cl_room_icon('mic') ?>
            <ul id="speakers" class="speakers" role="list"></ul>
        </section>
        <div class="room-side">
            <section class="room-panel" id="people-panel" aria-labelledby="people-title" <?= $staff ? '' : 'hidden' ?>>
                <div class="room-panel-head">
                    <h2 id="people-title"><?= cl_room_icon('people') ?> حاضران <bdi id="people-count" class="count-chip">۰</bdi></h2>
                </div>
                <ul id="people" class="people"></ul>
            </section>
            <section class="room-panel" id="chat-panel" aria-labelledby="chat-title">
                <div class="room-panel-head"><h2 id="chat-title"><?= cl_room_icon('chat') ?> گفت‌وگوی کلاس</h2></div>
                <p id="chat-empty" class="chat-empty">هنوز پیامی در کلاس نیست.</p>
                <ol id="chat-log" class="chat-log" role="log" aria-label="پیام‌های کلاس"></ol>
                <form id="chat-form">
                    <label for="chat-text" class="sr-only">متن پیام</label>
                    <textarea id="chat-text" maxlength="600" rows="1" placeholder="متن پیام…" required></textarea>
                    <button type="submit" aria-label="ارسال پیام" title="ارسال پیام"><?= cl_room_icon('send') ?><span>ارسال</span></button>
                </form>
            </section>
        </div>
    </div>
    <div id="audio-tracks" aria-hidden="true"></div>
</section>
