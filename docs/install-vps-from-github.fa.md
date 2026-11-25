# نصب مستقیم VPS کلاس از همین مخزن GitHub

این روش جایگزین **دانلود و انتقال آرشیو VPS** در مرحلهٔ ۴ [راهنمای نصب از صفر](../server/offline/README-fa.md) است. نصب سایت PHP/MySQL، تنظیم DNS و فایروال، و اتصال خصوصی در پنل مدرسه مثل قبل‌اند. فقط روی **VPS اختصاصی تازه با Ubuntu 24.04 LTS amd64** اجرا کن؛ برای ارتقای نصب موجود نیست.

## از داخل SSH روی VPS

بعد از آماده‌شدن رکورد `A` مستقیم به IPv4 VPS و باز کردن `443/TCP`، `7882/UDP` و `7881/TCP`، با حساب SSH دارای `sudo` وارد شو. VPS برای دریافت بسته باید به GitHub و میزبان دانلود GitHub Releases دسترسی HTTPS داشته باشد. اگر `curl` نصب نیست، آن را با `sudo apt-get update && sudo apt-get install -y curl` از مخزن رسمی Ubuntu بگیر یا از مسیر آفلاین راهنمای اصلی استفاده کن.

```bash
cd ~
curl --disable -fsSL --proto '=https' --proto-redir '=https' \
  -H 'Accept: application/vnd.github.raw+json' \
  'https://api.github.com/repos/Maxess/bacirat-function/contents/install-vps.sh?ref=465a3f8d987b72fc8fe07100e8c7fb22580409a6' \
  -o baci-install-vps.sh &&
bash ./baci-install-vps.sh --tls auto
```

اگر همین شاخهٔ مخزن را از قبل روی VPS `clone` کرده‌ای، از ریشهٔ آن `bash ./install-vps.sh --tls auto` را اجرا کن. نیازی به `git clone` برای روش بالا، توکن GitHub، ساخت از سورس یا آپلود آرشیو با FileZilla نیست.

- اسکریپت **همان آرشیو آزموده‌شدهٔ [نسخهٔ ۱٫۱٫۰](https://github.com/Maxess/bacirat-function/releases/tag/vps-offline-v1.1.0)** را از انتشار همین مخزن می‌گیرد، SHA-256 ثابت آن را بررسی می‌کند و فقط بعد از تطابق، نصب‌گر بسته را با دسترسی لازم اجرا می‌کند. دانلود و استخراج موقت است؛ سرور از شاخهٔ در حال توسعه ساخته نمی‌شود.
- مانند قبل نشانی دقیق HTTPS مدرسه (با زیرپوشه اگر دارد)، نام رسانه و IPv4 عمومی را وارد کن. برای بررسی بدون نصب، `--check` را به فرمان `bash` اضافه کن؛ فایل دانلود می‌شود، اما سرویس یا گواهی ساخته نمی‌شود. اگر می‌خواهی گواهی آماده بدهی، `--tls files --cert ./fullchain.pem --key ./privkey.pem` را به جای `--tls auto` بگذار؛ در این حالت **فقط دانلود بسته** اینترنت می‌خواهد، نه صدور SSL. اگر VPS به GitHub هم دسترسی ندارد، از روش انتقال آرشیو در راهنمای اصلی استفاده کن.
- پس از پیام `installation=ok`، فایل خصوصی `Baci-VPS-pairing.json` را با SFTP بگیر و طبق [مرحلهٔ اتصال](../server/offline/README-fa.md#pairing) فقط در پنل HTTPS مدیر کل بارگذاری کن. هیچ فایلی از VPS به GitHub فرستاده نمی‌شود.
