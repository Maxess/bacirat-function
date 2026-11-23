# ارتقای Recorder ۰٫۴٫۲۳ از مخزن

این دستور فقط برای **Recorder استاندارد، فعال و بدون کار** `0.4.21` یا `0.4.22`، روی VPS رسانهٔ `Linux x86-64` با systemd و cgroup v2 است. اگر VPS فقط LiveKit دارد و از ضبط استفاده نمی‌شود، این دستور را نزن. برای نصب اول، [راهنمای Recorder](../server/recorder/README-fa.md) را بخوان.

## قبل از اجرا

1. زمان نگهداری بگذار و زنگ تازه باز نکن. از سایت، داده و تنظیمات بکاپ بگیر. بگذار تمام کارهای ضبط، ساخت، ارسال و رسید به پایان برسند.
2. مدیر `0.1.29` را در File Manager نصب کن. از «آماده‌سازی ارتقا (حفظ داده و کنترل صف)» استفاده کن و افزونهٔ کلاس `0.4.23` را نصب و با **تنظیمات ضبط و کلید موجود** فعال کن؛ «حذف کامل» نزن.
3. روی VPS ضبط، اسکریپت و ZIP دقیق همین نسخه را از مخزن عمومی یا بستهٔ یکجا بگیر. هر دو هش را پیش از `sudo` بررسی کن. ابتدا `--check-only` و فقط بعد از موفقیت `--apply` را اجرا کن.

## دستور روی VPS ضبط

این دستور فقط دو فایل را از [کامیت ثابت انتشار](https://github.com/Maxess/bacirat-function/commit/43aeb10a1f46f369830c4c1d5139a03d10985ea0) می‌گیرد. به توکن GitHub، clone کامل یا Go روی VPS نیاز نیست. اگر بررسی اولیه موفق بود، فقط با وارد کردن `yes` ارتقا اعمال می‌شود.

```sh
(
  set -eu
  umask 077
  d=$(mktemp -d)
  trap 'rm -rf -- "$d"' EXIT
  cd "$d"
  rev=43aeb10a1f46f369830c4c1d5139a03d10985ea0
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
49f08faf1e84df77c0c71a8030e6dbfbbeb198d800c097b3979ea3b5db6abb17  upgrade-v0.4.23.py
663da68a85bdfac47ae2a0fbb48628d856f567e116bf367bdbd28ff935b490d1  baci-classroom-recorder-v0.4.23.zip
SHA256
  sudo python3 -I "$script" --check-only "$zip"
  printf 'Apply Recorder 0.4.23? Type yes to continue: '
  IFS= read -r answer
  [ "$answer" = yes ] || exit 0
  sudo python3 -I "$script" --apply "$zip"
)
```

اگر VPS دسترسی به GitHub ندارد، دو فایل همین نسخه را از ZIP یکجا منتقل کن و **همین دو هش** را روی VPS بررسی کن. بعد از تطابق، همان دو فرمان `sudo` را با مسیر فایل‌های محلی بزن. محاسبهٔ هش با فایلِ تغییرکرده جایگزین تطبیق با هش بالا نیست.

## بعد از اجرا

```sh
sudo systemctl status school-classroom-recorder.service school-classroom-postprocess.service --no-pager
```

در «گروه‌های پیام‌رسان و ضبط کلاس»، وضعیت «آماده» را ببین و یک زنگ با حساب‌ها و گروه آزمایشی اجرا کن. اسکریپت نه LiveKit را restart می‌کند و نه Caddy، فایروال، sysctl، کلیدها، مرورگر یا فایل‌های موقت ضبط را جایگزین می‌کند. اگر زمان نگهداری رعایت نشود و صف یا زنگ تازه بعد از شروع Recorder ایجاد شود، ممکن است برگشت خودکار برای حفظ همان کار متوقف شود. در صورت خطا کار را به زور تکرار نکن و spool را پاک نکن. [بررسی اختیاری UDP](classroom-board-network.fa.md#udp-check) بعد از ارتقا جداگانه است.
