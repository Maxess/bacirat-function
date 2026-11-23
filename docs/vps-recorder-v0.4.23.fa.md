# ارتقای Recorder ۰٫۴٫۲۳ از مخزن

این دستور فقط برای **Recorder استاندارد، فعال و بدون کار** `0.4.21` یا `0.4.22`، روی VPS رسانهٔ `Linux x86-64` با systemd و cgroup v2 است. اگر VPS فقط LiveKit دارد و از ضبط استفاده نمی‌شود، این دستور را نزن. برای نصب اول، [راهنمای Recorder](../server/recorder/README-fa.md) را بخوان.

اگر اسکریپت اول با `Unexpected file in the Recorder spool` متوقف شد، فایل‌های ضبط یا کش را حذف یا جابه‌جا نکن. روی نصب استاندارد، `.cache/fontconfig` ممکن است به‌طور عادی در پوشهٔ خصوصی Recorder ساخته شود؛ اسکریپت اصلاح‌شده فقط همین ساختار را با بررسی مالک، نوع و مجوزها می‌پذیرد. بررسی ضبط و رسید معلق و اتاق باز همچنان برقرار است. **ZIPها تغییر نکرده‌اند؛ اسکریپت را از دستور زیر بگیر، نه از نسخهٔ اولیهٔ بستهٔ یکجا.**

## قبل از اجرا

1. زمان نگهداری بگذار و زنگ تازه باز نکن. از سایت، داده و تنظیمات بکاپ بگیر. بگذار تمام کارهای ضبط، ساخت، ارسال و رسید به پایان برسند.
2. مدیر `0.1.29` را در File Manager نصب کن. از «آماده‌سازی ارتقا (حفظ داده و کنترل صف)» استفاده کن و افزونهٔ کلاس `0.4.23` را نصب و با **تنظیمات ضبط و کلید موجود** فعال کن؛ «حذف کامل» نزن.
3. روی VPS ضبط، اسکریپت و ZIP دقیق همین نسخه را از مخزن عمومی یا بستهٔ یکجا بگیر. هر دو هش را پیش از `sudo` بررسی کن. ابتدا `--check-only` و فقط بعد از موفقیت `--apply` را اجرا کن.

## دستور روی VPS ضبط

این دستور فقط دو فایل را از [کامیت ثابتِ اصلاح اسکریپت](https://github.com/Maxess/bacirat-function/commit/706321fceaca3e9135fd3000db27e514da733a6c) می‌گیرد. به توکن GitHub، clone کامل یا Go روی VPS نیاز نیست. اگر بررسی اولیه موفق بود، فقط با وارد کردن `yes` ارتقا اعمال می‌شود.

```sh
(
  set -eu
  umask 077
  d=$(mktemp -d)
  trap 'rm -rf -- "$d"' EXIT
  cd "$d"
  rev=706321fceaca3e9135fd3000db27e514da733a6c
  base=https://api.github.com/repos/Maxess/bacirat-function/contents
  script=upgrade-v0.4.23.py
  zip=baci-classroom-recorder-v0.4.23.zip
  get() {
    curl --proto '=https' -fsS --connect-timeout 10 --max-time 120 --retry 2 \
      -H 'Accept: application/vnd.github.raw+json' "$base/$1?ref=$rev" -o "$2"
  }
  get "server/recorder/$script" "$script"
  get "artifacts/$zip" "$zip"
  sha256sum -c <<'SHA256'
539b193a81c33a986bb93727dbe259d205a914d4bbe6c3e080083323af3269a5  upgrade-v0.4.23.py
663da68a85bdfac47ae2a0fbb48628d856f567e116bf367bdbd28ff935b490d1  baci-classroom-recorder-v0.4.23.zip
SHA256
  sudo python3 -I "$script" --check-only "$zip"
  printf 'Apply Recorder 0.4.23? Type yes to continue: '
  IFS= read -r answer
  [ "$answer" = yes ] || exit 0
  sudo python3 -I "$script" --apply "$zip"
)
```

اگر VPS دسترسی به GitHub ندارد، از یک دستگاه دیگر [اسکریپت اصلاح‌شده](https://github.com/Maxess/bacirat-function/blob/706321fceaca3e9135fd3000db27e514da733a6c/server/recorder/upgrade-v0.4.23.py?raw=true) و همان ZIP Recorder `0.4.23` را منتقل کن. **هر دو هش بالا** را روی VPS بررسی کن و فقط پس از تطابق، فرمان‌های `sudo` را با مسیر فایل‌های محلی بزن. اگر هنوز پیام `Unexpected Recorder spool entry` می‌بینی، فقط متن خطا را (بدون محتوای فایل یا کلیدها) گزارش کن و چیزی از فضای ضبط پاک نکن.

## بعد از اجرا

```sh
sudo systemctl status school-classroom-recorder.service school-classroom-postprocess.service --no-pager
```

در «گروه‌های پیام‌رسان و ضبط کلاس»، وضعیت «آماده» را ببین و یک زنگ با حساب‌ها و گروه آزمایشی اجرا کن. اسکریپت نه LiveKit را restart می‌کند و نه Caddy، فایروال، sysctl، کلیدها، مرورگر یا فایل‌های موقت ضبط را جایگزین می‌کند. اگر زمان نگهداری رعایت نشود و صف یا زنگ تازه بعد از شروع Recorder ایجاد شود، ممکن است برگشت خودکار برای حفظ همان کار متوقف شود. در صورت خطا کار را به زور تکرار نکن و spool را پاک نکن. [بررسی اختیاری UDP](classroom-board-network.fa.md#udp-check) بعد از ارتقا جداگانه است.
