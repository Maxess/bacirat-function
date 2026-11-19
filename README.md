# bacirat-function

## کلاس مجازی برای سایت آزمایشی مدرسه

1. [مدیر افزونه‌ها ۰٫۱٫۵](artifacts/mtagbot-plugin-manager-v0.1.5.zip) — فقط `plugins/school-plugins.php` را از ZIP روی هاست تست بگذار یا جایگزین کن.
2. [کلاس آنلاین ۰٫۳٫۰](artifacts/school-classroom-plugin-v0.3.0.zip) — ZIP را از مسیر **تنظیمات دیگر ← افزونه‌ها** نصب کن. برای ارتقا اول زنگ‌های فعلی را ببند و **غیرفعال‌سازی (حفظ داده)** را بزن.
3. [راهنمای نصب/ارتقا و اعلان اختیاری](plugin-manager/README-fa.md) · [راه‌اندازی ناظر در VPS رسانه](server/README-fa.md#supervision)

افزونه از سال پیش‌فرض، کلاس‌ها، عضویت دانش‌آموزان و برنامهٔ هفتگی مدرسه استفاده می‌کند. هر زنگ برگزاری گزارش مستقل دارد. دانش‌آموز فقط با حضور دبیر یا ناظر مجاز به کلاس صوتی، چت و تختهٔ تصویر/PDF دسترسی دارد؛ انتظار و وقفه به حضور اضافه نمی‌شوند. کنار نام دانش‌آموزها زمان ثبت‌شده دیده می‌شود. دوربین فعال نیست. اعلان بله، روبیکا و تلگرام با انتخاب مدیر و پس از پایان زنگ قابل تنظیم است؛ اجرای دوره‌ای PHP روی هاست لازم است و هیچ اعلان پیش‌فرضی ارسال نمی‌شود.

عمومی فقط ورودی `plugins/school-plugins.php` است. فایل‌های کلاس و محتوای تخته در پوشهٔ اختصاصی همین افزونه، خارج از ریشهٔ وب می‌مانند. حذف کامل به سایر افزونه‌ها یا جدول‌های مدرسه کاری ندارد. VPS واقعی، سایت اصلی، مخزن `mtagbot` و PersianMeet با ساخت بسته‌ها تغییر نمی‌کنند.

### ساخت و آزمون محلی

```sh
python3 scripts/build-managed-school.py
php tests/plugin-manager.php
php tests/school-classroom.php
php tests/classroom-lessons.php
node --test tests/classroom-ui.cjs
python3 -m unittest discover -s tests -p 'test_*.py'
```

ساخت، SDK ثابت از ZIP منتشرشدهٔ قدیمی و PDF.js ثابت از npm را با هش تأیید می‌کند. برای ساخت آفلاین از `--pdf-archive` با همان آرشیو npm استفاده کن. آزمون PHP به `pdo_sqlite` و برای چرخهٔ مدیر به `ZipArchive` و `OpenSSL` نیاز دارد. برای اجرای `tests/plugin-manager.php` و `tests/classroom-lessons.php` روی MariaDB/MySQL، `MPM_TEST_DSN` را روی **پایگاه محلی خالی و موقت** `mpm_fixture_*` تنظیم کن (نمونه: `mysql:host=127.0.0.1;port=3306;dbname=mpm_fixture_local;charset=utf8mb4`) و `MPM_TEST_USER` و `MPM_TEST_PASSWORD` را در محیط بده. پایگاه واقعی مدرسه را به آزمون نده.

آزمون‌ها حسابداری زنگ، چت و تخته، مجوزهای ورود و کنترل رسانه، ارتقا/حذف، و اعلان را با دادهٔ مصنوعی بررسی می‌کنند. آزمون مرورگر HTTPS/MariaDB با شبیه‌ساز رسانه، رندر واقعی PDF/تصویر و بازگشت به انتظار را بررسی می‌کند؛ جای تأیید گوشی، WebRTC، پایداری، ظرفیت یا ارسال واقعی در VPS تست را نمی‌گیرد.

### نسخهٔ دستی پیشین (بایگانی)

[ZIP نسخهٔ ۰٫۱](artifacts/school-classroom-pilot-v0.1.0.zip) · [راهنمای قدیمی](school-addon/README-fa.md). این بسته در مدیر افزونه‌ها نصب نمی‌شود. بازسازی نسخهٔ دستی از کد commit `885bd8f4754a74edbb3e496a0da9cddb19aef468` انجام می‌شود، نه از منبع فعلی کلاس مدیریت‌شده.

## طرح ظرفیت و آزمایش‌های قبلی

- [طرح کلاس مجازی و سنجش VPS](docs/school-plan.fa.md)
- [ابزار آزمون یک‌بارهٔ سرعت خروجی VPS](scripts/vps-egress.py)
- [پایلوت PersianMeet صرفاً برای مقایسه](docs/persianmeet-pilot.fa.md)
