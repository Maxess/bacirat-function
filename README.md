# bacirat-function

## پایلوت یکپارچه با سایت مدرسه (بدون PersianMeet)

- [دانلود ZIP نسخهٔ ۰٫۱ افزونهٔ سایت آزمایشی](artifacts/school-classroom-pilot-v0.1.0.zip)
- [راهنمای نصب افزونه روی هاست اشتراکی](school-addon/README-fa.md)
- [راهنمای نصب موتور رسانه از همین شاخه روی VPS](server/README-fa.md)

این نسخه افزونهٔ کار با نشست موجود دانش‌آموز، دبیر و مدیر، یک اتاق صوتی و کنترل نوبت میکروفون است. هیچ فایل یا migration روی ریپوی `mtagbot` اعمال نشده است. برای بازسازی ZIP: `python3 scripts/build-school-zip.py`. آزمون‌های محلی: `php tests/school-classroom.php` (با PDO SQLite) و `python3 -m unittest discover -s tests -p 'test_*.py'`.

## طرح ظرفیت و آزمایش‌های قبلی

- [طرح کلاس مجازی مدرسه و راهنمای سنجش VPS](docs/school-plan.fa.md)
- [ابزار آزمون یک‌بارهٔ سرعت خروجی VPS](scripts/vps-egress.py)
- [پایلوت PersianMeet صرفاً برای مقایسه، نه پیش‌نیاز افزونه](docs/persianmeet-pilot.fa.md)
