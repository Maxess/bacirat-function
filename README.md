# bacirat-function

## پایلوت صوتی سایت مدرسه — نصب و حذف از پنل

1. [دانلود ZIP مدیر افزونه‌ها](artifacts/mtagbot-plugin-manager-v0.1.0.zip) — یک‌بار روی هاست **تست** فایل `school-plugins.php` را اضافه کن.
2. [دانلود ZIP کلاس آنلاین](artifacts/school-classroom-plugin-v0.2.0.zip) — بدون Extract، از **تنظیمات دیگر ← افزونه‌ها** نصب کن.
3. [راهنمای مدیر و کلاس آنلاین](plugin-manager/README-fa.md) · [راهنمای نصب LiveKit روی VPS](server/README-fa.md)

گذرگاه PHP از نشست موجود مدرسه و MySQL همان سایت تست استفاده می‌کند. غیرفعال‌سازی فقط دسترسی را می‌بندد. حذف کامل، فایل‌های افزونه، جدول اختصاصی و رکورد آن را پاک می‌کند و پیوند کلاس را از سه پنل برمی‌دارد؛ زیرساخت عمومی مدیر باقی می‌ماند. ریپوی اصلی `mtagbot` و سایت اصلی تغییر نکرده‌اند. این پایلوت فقط یک اتاق صوتی، سقف ۴ اتصال و کنترل نوبت میکروفون دارد و به PersianMeet نیاز ندارد.

### بازسازی و آزمون محلی

```sh
python3 scripts/build-managed-school.py
php tests/plugin-manager.php
php tests/school-classroom.php
python3 -m unittest discover -s tests -p 'test_*.py'
```

اسکریپت ساخت، SDK را از ZIP منتشرشدهٔ قدیمی با SHA-256 ثابت برمی‌دارد و تنها بسته‌ای با manifest مطابق کاتالوگ تولید می‌کند. تست PHP به `ZipArchive`، `OpenSSL` و `pdo_sqlite` نیاز دارد. برای اجرای چرخهٔ مدیریت روی MySQL/MariaDB، یک پایگاه **خالی و موقت** محلی با نام `mpm_fixture_*` بساز و `MPM_TEST_DSN` (به‌شکل `mysql:host=127.0.0.1;port=3306;dbname=mpm_fixture_local;charset=utf8mb4`)، `MPM_TEST_USER` و `MPM_TEST_PASSWORD` را در محیط اجرای `tests/plugin-manager.php` تنظیم کن؛ هرگز دیتابیس مدرسه را به آزمون نده. نسخهٔ وب روی یک MariaDB مجزا با سه پنل مرجع هم آزموده شد. آزمون صدای واقعی و شبکهٔ VPS هنوز لازم است.

### نسخهٔ دستی پیشین (بایگانی)

[ZIP نسخهٔ ۰٫۱](artifacts/school-classroom-pilot-v0.1.0.zip) · [راهنمای قدیمی](school-addon/README-fa.md). این بسته در مدیر افزونه‌ها نصب نمی‌شود. بازسازی نسخهٔ دستی از کد commit `885bd8f4754a74edbb3e496a0da9cddb19aef468` انجام می‌شود، نه از منبع فعلی کلاس مدیریت‌شده.

## طرح ظرفیت و آزمایش‌های قبلی

- [طرح کلاس مجازی و سنجش VPS](docs/school-plan.fa.md)
- [ابزار آزمون یک‌بارهٔ سرعت خروجی VPS](scripts/vps-egress.py)
- [پایلوت PersianMeet صرفاً برای مقایسه](docs/persianmeet-pilot.fa.md)
