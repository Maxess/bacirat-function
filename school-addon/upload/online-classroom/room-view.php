<?php
declare(strict_types=1);

// Small, local icons; no dependency on a conference app or icon CDN.
function cl_room_icon(string $name): string
{
    $mark = match ($name) {
        'back' => '<path d="M5 12h14m-6-6 6 6-6 6"/>',
        'board' => '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21l4-4 4 4M7 12l3-3 3 2 3-4"/>',
        'clock' => '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
        'mic' => '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11v1a7 7 0 0 0 14 0v-1m-7 8v3m-3 0h6"/>',
        'mic-off' => '<path d="m3 3 18 18M9 9v3a3 3 0 0 0 5 2m1-4V6a3 3 0 0 0-5-2M5 11v1a7 7 0 0 0 11 5m3-6v1a7 7 0 0 1-.5 2.6M12 19v3m-3 0h6"/>',
        'sound' => '<path d="M11 5 6 9H3v6h3l5 4V5Zm4.5 4a4.2 4.2 0 0 1 0 6m3-9a8 8 0 0 1 0 12"/>',
        'people' => '<circle cx="9" cy="7.5" r="3"/><path d="M2.5 20v-2a4 4 0 0 1 4-4h5a4 4 0 0 1 4 4v2M16 5a3 3 0 0 1 0 6m2 3a4 4 0 0 1 3.5 4v2"/>',
        'chat' => '<path d="M21 11.5A8.5 8.5 0 0 1 12.5 20H5l1.3-3A8.5 8.5 0 1 1 21 11.5ZM8 10h8m-8 4h5"/>',
        'exit' => '<path d="M10 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h5m-1-8h12m-4-4 4 4-4 4"/>',
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
  data-class="<?= $class['id'] ?>" data-bell-date="<?= cl_day($now)->format('Y-m-d') ?>" data-moderator="<?= $staff ? 'yes' : 'no' ?>"
  data-content="<?= cl_html(cl_href(['route'=>'content','class'=>$class['id']])) ?>"
  data-pdf="<?= cl_html(cl_asset('pdf.min.mjs')) ?>" data-pdf-worker="<?= cl_html(cl_asset('pdf.worker.min.mjs')) ?>"
  data-pdf-data="<?= cl_html(cl_href(['route'=>'pdf']) . '&name=') ?>">
    <header class="room-top">
        <div class="room-identity">
            <a class="icon-button room-back" href="<?= cl_html(cl_href(['view'=>'classes'])) ?>" aria-label="بازگشت به فهرست کلاس‌ها" title="فهرست کلاس‌ها"><?= cl_room_icon('back') ?></a>
            <div class="room-name">
                <span>کلاس مجازی <span aria-hidden="true">/</span> <?= cl_html($actorLabel) ?></span>
                <h1><?= cl_html($class['name']) ?></h1>
            </div>
        </div>
        <div class="room-controls" role="group" aria-label="کنترل‌های اتاق">
            <button type="button" id="microphone" class="icon-button mic-toggle" aria-pressed="false" aria-label="روشن‌کردن میکروفون" title="روشن‌کردن میکروفون" disabled>
                <?= cl_room_icon('mic-off') . cl_room_icon('mic') ?><span id="microphone-label">میکروفون</span>
            </button>
            <button type="button" id="enable-audio" class="icon-button audio-toggle" title="فعال‌کردن پخش صدا" hidden><?= cl_room_icon('sound') ?><span>پخش صدا</span></button>
            <span class="control-divider" aria-hidden="true"></span>
            <button type="button" id="toggle-people" class="icon-button" aria-controls="people-panel" aria-expanded="true" title="نمایش یا بستن فهرست حاضران" disabled><?= cl_room_icon('people') ?><span>حاضران</span></button>
            <button type="button" id="toggle-chat" class="icon-button" aria-controls="chat-panel" aria-expanded="true" title="نمایش یا بستن گفت‌وگو" disabled><?= cl_room_icon('chat') ?><span>گفت‌وگو</span></button>
        </div>
        <div class="room-indicators">
            <?php if (!$staff): ?>
            <div class="attendance" aria-label="مدت حضور شما">
                <?= cl_room_icon('clock') ?>
                <div><span id="attendance-label">مدت حضور شما</span><bdi id="attendance-time" aria-describedby="attendance-label">00:00:00</bdi></div>
            </div>
            <?php endif; ?>
            <span class="status-chip" id="status" role="status">اتاق انتظار</span>
            <button type="button" id="leave" class="icon-button leave-button" aria-label="خروج از اتاق" title="خروج از اتاق" hidden><?= cl_room_icon('exit') ?><span>خروج</span></button>
        </div>
    </header>
    <section class="session-strip" aria-labelledby="lecture-title">
        <div class="session-heading">
            <span class="session-icon"><?= cl_room_icon('board') ?></span>
            <div class="session-name"><h2 id="lecture-title">جلسهٔ کلاس</h2><p><?= cl_html($actor['name']) ?> <span aria-hidden="true">·</span> <a href="<?= cl_html(cl_site_href($panel)) ?>">بازگشت به پنل</a></p></div>
            <span id="practice-badge" class="practice-chip" hidden>آزمایشی</span>
        </div>
        <?php if ($staff): ?>
        <div class="staff-actions actions">
            <div id="staff-start" class="actions">
                <label for="slot" class="sr-only">زنگ‌های امروز · وقت تهران</label>
                <select id="slot"><option value="">انتخاب زنگ امروز</option>
                    <?php foreach ($choices as $choice): ?><option value="<?= cl_html((string)$choice['id']) ?>"><?= cl_html($choice['label']) ?></option><?php endforeach; ?>
                </select>
                <button type="button" id="open" disabled>شروع زنگ</button>
            </div>
            <button type="button" id="close" class="danger-outline" hidden>پایان کلاس برای همه</button>
        </div>
        <?php endif; ?>
    </section>
    <p id="notice" role="status" class="notice" hidden></p>
    <p id="mic-feedback" role="status" class="notice" hidden></p>
    <section class="waiting board-grid" id="waiting" aria-labelledby="wait-title">
        <div class="waiting-copy">
            <span class="waiting-icon"><?= cl_room_icon('clock') ?></span>
            <p class="eyebrow">اتاق انتظار</p>
            <h2 id="wait-title"><?= $staff ? 'در انتظار شروع زنگ' : 'در انتظار ورود دبیر یا مسئول مدرسه' ?></h2>
            <p id="wait-message"><?= $staff ? 'در حال بررسی زنگ‌های امروز…' : 'وقتی اتصال دبیر تأیید شود، خودکار وارد کلاس می‌شوی.' ?></p>
            <p class="wait-hint"><?= cl_room_icon('clock') ?> زمان انتظار در تایمر حضور حساب نمی‌شود.</p>
            <?php if (!$staff): ?><button type="button" id="join" hidden>بازگشت به اتاق انتظار</button><?php endif; ?>
        </div>
    </section>
    <div id="live-panel" hidden>
        <section class="board-card" id="board" aria-label="تختهٔ کلاس">
            <div class="board-toolbar">
                <div class="board-heading"><?= cl_room_icon('board') ?><h2>تختهٔ کلاس</h2></div>
                <?php if ($staff): ?>
                <div class="ink-tools" role="group" aria-label="ابزار نوشتن">
                    <button type="button" class="icon-button" id="pen" aria-pressed="true" title="قلم" aria-label="قلم"><?= cl_room_icon('pen') ?></button>
                    <button type="button" class="icon-button" id="eraser" aria-pressed="false" title="پاک‌کن" aria-label="پاک‌کن"><?= cl_room_icon('erase') ?></button>
                    <button type="button" class="icon-button" id="pan" aria-pressed="false" title="پیمایش تخته" aria-label="پیمایش تخته"><?= cl_room_icon('pan') ?></button>
                    <label class="color-control" title="رنگ قلم"><span class="sr-only">رنگ قلم</span><input type="color" id="ink-color" value="#278bc7"></label>
                    <label class="size-control" title="ضخامت قلم"><span class="sr-only">ضخامت قلم</span><input type="range" id="ink-size" min="1" max="36" value="4"></label>
                    <button type="button" class="icon-button" id="board-clear" title="پاک‌کردن نوشته‌های این صفحه" aria-label="پاک‌کردن نوشته‌های این صفحه"><?= cl_room_icon('trash') ?></button>
                </div>
                <div class="page-tools" role="group" aria-label="صفحه‌های فایل">
                    <button type="button" class="icon-button" id="page-prev" aria-label="صفحهٔ قبلی" title="صفحهٔ قبلی" disabled><?= cl_room_icon('prev') ?></button>
                    <label><span class="sr-only">شمارهٔ صفحه</span><input type="number" id="page-number" min="1" value="1" dir="ltr" disabled></label>
                    <button type="button" class="icon-button" id="page-go" aria-label="رفتن به صفحه" title="رفتن به صفحه" disabled><?= cl_room_icon('check') ?></button>
                    <button type="button" class="icon-button" id="page-next" aria-label="صفحهٔ بعدی" title="صفحهٔ بعدی" disabled><?= cl_room_icon('next') ?></button>
                </div>
                <button type="button" class="icon-button file-toggle" id="toggle-files" aria-controls="board-files" aria-expanded="false" title="فایل‌های تخته"><?= cl_room_icon('upload') ?><span>فایل</span></button>
                <?php endif; ?>
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
            <p id="board-note" class="notice" role="status" hidden></p>
            <div class="board-workspace">
                <div id="board-scroll" class="board-scroll board-grid" tabindex="0" aria-label="نمای تخته؛ برای دیدن قسمت‌های بزرگ‌نمایی‌شده پیمایش کن">
                    <div class="board-bed"><div id="board-stage" class="board-stage<?= $staff ? ' editable' : '' ?>">
                        <canvas id="board-paper" aria-label="صفحهٔ فایل یا تصویر تخته"></canvas>
                        <canvas id="board-ink" aria-label="نوشته‌های روی تخته"></canvas>
                    </div></div>
                </div>
            </div>
            <div class="board-footer">
                <p class="board-caption" id="board-caption">تختهٔ آزاد</p>
                <div class="view-tools" role="group" aria-label="اندازهٔ نمایش تخته">
                    <button type="button" id="board-zoom-out" class="icon-button" aria-label="کوچک‌نمایی تخته" title="کوچک‌نمایی"><?= cl_room_icon('minus') ?></button>
                    <button type="button" id="board-zoom" class="zoom-value" dir="ltr" aria-label="بازنشانی بزرگ‌نمایی به ۱۰۰ درصد" title="بازنشانی بزرگ‌نمایی">۱۰۰٪</button>
                    <button type="button" id="board-zoom-in" class="icon-button" aria-label="بزرگ‌نمایی تخته" title="بزرگ‌نمایی"><?= cl_room_icon('plus') ?></button>
                    <button type="button" id="board-expand" class="icon-button" aria-label="نمای بزرگ تخته" aria-pressed="false" title="نمای بزرگ تخته"><?= cl_room_icon('expand') . cl_room_icon('shrink') ?></button>
                </div>
            </div>
        </section>
        <div class="room-side">
            <section class="room-panel" id="people-panel" aria-labelledby="people-title">
                <div class="room-panel-head">
                    <h2 id="people-title"><?= cl_room_icon('people') ?> حاضران <bdi id="people-count" class="count-chip">۰</bdi></h2>
                    <?php if ($staff): ?><button type="button" id="clear-floor" class="subtle-button">گرفتن نوبت از همه</button><?php endif; ?>
                </div>
                <ul id="people" class="people"></ul>
            </section>
            <section class="room-panel" id="chat-panel" aria-labelledby="chat-title">
                <div class="room-panel-head"><h2 id="chat-title"><?= cl_room_icon('chat') ?> گفت‌وگوی کلاس</h2></div>
                <p id="chat-empty" class="chat-empty">هنوز پیامی در کلاس نیست.</p>
                <ol id="chat-log" class="chat-log" role="log" aria-label="پیام‌های کلاس"></ol>
                <form id="chat-form">
                    <label for="chat-text" class="sr-only">متن پیام</label>
                    <textarea id="chat-text" maxlength="600" rows="1" placeholder="پیامی برای این کلاس…" required></textarea>
                    <button type="submit"><?= cl_room_icon('send') ?> ارسال</button>
                </form>
            </section>
        </div>
    </div>
    <div id="audio-tracks" aria-hidden="true"></div>
</section>
