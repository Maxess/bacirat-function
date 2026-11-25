# نصب مستقیم VPS کلاس از همین مخزن GitHub

این روش جایگزین **دانلود و انتقال آرشیو VPS** در مرحلهٔ ۴ [راهنمای نصب از صفر](../server/offline/README-fa.md) است. نصب سایت PHP/MySQL، DNS و فایروال و اتصال خصوصی در پنل مدرسه مثل قبل‌اند. فقط روی **VPS اختصاصی تازه با Ubuntu 24.04 LTS amd64** اجرا کن؛ برای ارتقای نصب موجود نیست.

بعد از تنظیم رکورد `A` مستقیم به IPv4 VPS و باز کردن `443/TCP`، `7882/UDP` و `7881/TCP`، با حساب SSH دارای `sudo` وارد شو. اگر `curl` نصب نیست، آن را با `sudo apt-get update && sudo apt-get install -y curl` از مخزن رسمی Ubuntu نصب کن.

## انتخاب حالت برای VPS با ۵۱۲ MiB

روی VPS با **۵۱۲ MiB RAM** از `--profile live` استفاده کن. صدا، تصویر، تخته، ورود و SSL در این حالت در دسترس‌اند؛ ضبط محلی، Chromium و FFmpeg نصب نمی‌شوند. در پنل مدرسه ضبط را خاموش نگه دار. برای دیسک سیستم دست‌کم ۸ GiB فضای خالی در نظر بگیر.

[آزمون همین بسته](https://github.com/Maxess/bacirat-function/actions/runs/45525112331) روی Ubuntu تازه با ۵۱۲ MiB، یک vCPU و بدون swap، تبادل صدا و تصویر بین دو همتای WebRTC، HTTPS و راه‌اندازی مجدد را بررسی کرد؛ ظرفیت کلاس پرجمعیت اندازه‌گیری نشده است.

حداقل RAM قابل‌مشاهده در Ubuntu `393216 KiB` (`384 MiB`) است. بخشی از RAM پلن ۵۱۲ MiB را هسته رزرو می‌کند و swap شمرده نمی‌شود:

```bash
awk '/^MemTotal:/ {printf "MemTotal = %.1f MiB (%s KiB)\n", $2/1024, $2}' /proc/meminfo
```

برای ضبط محلی و ساخت MP4 حالت `--profile full` و پلن دست‌کم ۲ GiB لازم است. اگر `--profile` را حذف کنی، نصب‌گر بر اساس RAM حالت را انتخاب و گزارش می‌کند؛ انتخاب حالت روی SSL اثری ندارد.

اگر نسخهٔ ۱٫۱٫۰ با خطای RAM متوقف شده، آرشیو همان نسخه را دستکاری نکن. نسخهٔ ۱٫۲٫۰ را **در پوشهٔ تازه روی همان VPS** بگیر؛ تا وقتی سرویس کلاس قبلاً نصب نشده، به نصب مجدد Ubuntu یا تغییر سایت نیاز نیست.

## دانلود مستقیم بسته، بدون API گیت‌هاب

این روش به `api.github.com`، نصب Git یا توکن نیاز ندارد. خود VPS بستهٔ [انتشار ۱٫۲٫۰](https://github.com/Maxess/bacirat-function/releases/tag/vps-offline-v1.2.0) را از `github.com` و میزبان فایل انتشار دریافت می‌کند (حدود ۴۰۲ مگابایت). هش ثابت را پیش از استخراج بررسی می‌کند. این فرمان فقط پیش‌بررسی است و سرویس یا گواهی نمی‌سازد:

```bash
archive=baci-classroom-vps-offline-v1.2.0-ubuntu24.04-amd64.tar.gz
work=$(mktemp -d "$HOME/baci-vps-v120.XXXXXXXX") &&
cd "$work" &&
curl --disable --fail --show-error --location --remove-on-error \
  --proto '=https' --proto-redir '=https' --connect-timeout 12 --max-time 3600 \
  -o "$archive" "https://github.com/Maxess/bacirat-function/releases/download/vps-offline-v1.2.0/$archive" &&
printf '%s  %s\n' '9a79d791d38e2277b5fb29f9d05ae223338bb4c25ae30ab83ffb7c9d148c1cb4' "$archive" | sha256sum --check --strict &&
tar -xzf "$archive" --no-same-owner --no-same-permissions &&
sudo bash ./baci-classroom-vps-offline/install.sh --profile live --tls auto --check
```

نشانی دقیق HTTPS مدرسه (با زیرپوشه اگر دارد)، نام رسانه و IPv4 عمومی را وارد کن. اگر `preflight=ok tls=auto profile=live` آمد، **از همان پوشه و بدون دانلود دوباره** نصب را شروع کن:

```bash
sudo bash ./baci-classroom-vps-offline/install.sh --profile live --tls auto
```

## روش کوتاه با اسکریپت مخزن

اگر `raw.githubusercontent.com` هم از VPS در دسترس است، می‌توانی به‌جای روش بالا، اسکریپت یک commit ثابت را بگیری. این اسکریپت نسخه و هش همان بسته را بررسی می‌کند، موقت استخراج می‌کند و نصب‌گر را اجرا می‌کند:

```bash
cd ~ &&
curl --disable -fsSL --proto '=https' --proto-redir '=https' \
  --connect-timeout 12 --max-time 30 \
  'https://raw.githubusercontent.com/Maxess/bacirat-function/43ccc10a50b5785e15cfafd0bd1920334bcfcd78/install-vps.sh' \
  -o baci-install-vps.sh &&
bash ./baci-install-vps.sh --profile live --tls auto
```

اگر همین شاخه را از قبل `clone` کرده‌ای، از ریشهٔ آن `bash ./install-vps.sh --profile live --tls auto` را اجرا کن. در هر دو فرمان می‌توانی برای پیش‌بررسی `--check` اضافه کنی.

## اگر اتصال برقرار نشد

- `curl: (28)` هنگام اتصال به `api.github.com` یعنی فرمان قبلی پیش از اجرای نصب‌گر منتظر اتصال مانده است. به‌جای تکرار آن، روش دانلود مستقیم بالا را بزن. موفقیت `apt` روی `http://` ثابت نمی‌کند که VPS به GitHub روی `443/TCP` دسترسی دارد.
- اگر دانلود مستقیم از VPS ممکن نیست، [همان آرشیو نصب](https://github.com/Maxess/bacirat-function/releases/download/vps-offline-v1.2.0/baci-classroom-vps-offline-v1.2.0-ubuntu24.04-amd64.tar.gz) را روی رایانه بگیر و با SFTP به یک پوشهٔ تازه در خانهٔ کاربر SSH بفرست. از خود آن پوشه، سه فرمان آخر بلوک دانلود (بررسی SHA-256، استخراج و `--check`) را با همان متغیر `archive` اجرا کن. TLS دانلود را غیرفعال نکن.
- **انتقال آرشیو و صدور گواهی جدا هستند.** اگر فقط GitHub در دسترس نیست، پس از انتقال آرشیو هنوز می‌توانی `--tls auto` را نگه داری؛ برای آن VPS باید به Let’s Encrypt روی HTTPS خروجی دسترسی داشته باشد و مرجع صدور بتواند روی `443/TCP` به نام رسانه برسد. اگر دسترسی به مرجع صدور هم فراهم نیست، [حالت `--tls files` با گواهی معتبر برای نام جدید](../server/offline/README-fa.md#files-tls) را دنبال کن.

بعد از `installation=ok`، فایل خصوصی `Baci-VPS-pairing.json` را از پوشهٔ خانهٔ کاربر SSH بگیر و طبق [مرحلهٔ اتصال](../server/offline/README-fa.md#pairing) فقط در پنل HTTPS مدیر کل بارگذاری کن. هیچ فایلی از VPS به GitHub فرستاده نمی‌شود.
