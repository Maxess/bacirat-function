# اصلاح مستقل سرویس ساخت فیلم Recorder ۰٫۴٫۲۳–r1

این اصلاح برای **Recorder استاندارد و فعال `0.4.23`** است. انتظار متقابل لوله‌های صدا و تصویر را که به `postprocess: film failed (TimeoutError)` می‌رسید برطرف می‌کند. [جزئیات بازتولید و نتیجهٔ آزمایش](vps-recorder-render-timeout.fa.md).

افزونهٔ مدرسه، کلیدها، FFmpeg نصب‌شده، ثبت زنده و LiveKit تغییر نمی‌کنند. بستهٔ قدیمی `0.4.23` را دوباره نصب نکن. ضبطی که فایل موقت آن پاک شده با این اصلاح بازیابی یا دوباره ارسال نمی‌شود.

## ۱. فقط بررسی، بدون نصب

روی **همان VPS ضبط** و در زمانی که کلاس یا اتاق آزمایشی باز نیست و صف ضبط/ساخت/ارسال خالی است، این بلوک را اجرا کن. دانلود بدون توکن GitHub است. اسکریپت، بسته و نصب فعلی را اعتبارسنجی می‌کند؛ `--check-only` هیچ سرویسی را متوقف یا راه‌اندازی مجدد نمی‌کند و فیلم نمی‌سازد.

```sh
(
  set -eu
  umask 077
  d=$(mktemp -d /tmp/baci-renderer-r1.XXXXXX)
  cd "$d"
  ref=1cbc65e7da39016d6b2a32881ae529f3e2f16e11
  base=https://api.github.com/repos/Maxess/bacirat-function/contents
  script=repair-renderer-v0.4.23-r1.py
  zip=baci-classroom-renderer-v0.4.23-r1.zip
  get() {
    curl --proto '=https' -fsS --connect-timeout 10 --max-time 40 --retry 2 \
      -H 'Accept: application/vnd.github.raw+json' "$base/$1?ref=$ref" -o "$2"
  }
  get "server/recorder/$script" "$script"
  get "artifacts/$zip" "$zip"
  sha256sum -c <<'SHA256'
d290e83a7d7ce461abffe04456557ecff68d4e996815273853e053dd233e1ce6  repair-renderer-v0.4.23-r1.py
07d51104f5fa7bbfd90d6329931fa0e9d03967b9c8fc617c8fe08f10b4d38cfd  baci-classroom-renderer-v0.4.23-r1.zip
SHA256
  sudo /usr/bin/python3 -I "$script" --check-only "$zip"
  printf '\nFiles for the next step: %s\n' "$d"
)
```

نتیجهٔ موفق شامل `Check only; no service or installed file was changed.` و مسیر پوشهٔ دانلود است. در صورت خطا، فقط متن خطا را بدون کلید، اطلاعات کلاس یا محتوای فایل‌ها گزارش کن؛ چیزی از فضای ضبط، کش یا تنظیمات پاک نکن. اگر VPS به GitHub دسترسی ندارد، [اسکریپت](https://github.com/Maxess/bacirat-function/blob/1cbc65e7da39016d6b2a32881ae529f3e2f16e11/server/recorder/repair-renderer-v0.4.23-r1.py) و [ZIP](https://github.com/Maxess/bacirat-function/blob/1cbc65e7da39016d6b2a32881ae529f3e2f16e11/artifacts/baci-classroom-renderer-v0.4.23-r1.zip) را روی دستگاه دیگری بگیر، منتقل کن و **هر دو هش بالا** را پیش از `sudo` بررسی کن.

## ۲. اعمال در بازهٔ نگهداری، جدا از بررسی

فقط پس از موفقیت مرحلهٔ ۱ و وقتی هنوز هیچ کلاس، کار ضبط یا رسید معلقی وجود ندارد. تا اعلام نتیجه زنگ تازه باز نکن. در خط `cd` مسیر **واقعی** چاپ‌شده در مرحلهٔ ۱ را جایگزین کن:

```sh
(
  set -eu
  cd '/tmp/baci-renderer-r1.XXXXXX'
  sha256sum -c <<'SHA256'
d290e83a7d7ce461abffe04456557ecff68d4e996815273853e053dd233e1ce6  repair-renderer-v0.4.23-r1.py
07d51104f5fa7bbfd90d6329931fa0e9d03967b9c8fc617c8fe08f10b4d38cfd  baci-classroom-renderer-v0.4.23-r1.zip
SHA256
  sudo /usr/bin/python3 -I repair-renderer-v0.4.23-r1.py --apply baci-classroom-renderer-v0.4.23-r1.zip
)
```

`--apply` دوباره کنترل‌های مرحلهٔ اول را اجرا می‌کند. فقط `school-classroom-postprocess.service` راه‌اندازی مجدد می‌شود. دو ماژول اصلاح‌شده فقط در فضای همان سرویس به‌شکل فقط‌خواندنی دیده می‌شوند؛ فایل‌های اصلی `0.4.23` در جای خود باقی می‌مانند. اسکریپت با FFmpeg موجود یک MP4 مصنوعی H.264/AAC در سرویس محدودشده می‌سازد و سپس آماده‌بودن Recorder را از مدرسه بررسی می‌کند. این آزمون به «بله» چیزی نمی‌فرستد. نتیجهٔ موفق:

```text
Renderer 0.4.23-r1 is ready (sandboxed H.264/AAC MP4 and school heartbeat verified).
```

اگر آزمون شکست بخورد و صف همچنان خالی باشد، رندر قبلی برمی‌گردد. اگر بررسی بازگشت ممکن نباشد، اسکریپت دوباره رندر را قطع نمی‌کند تا کار تازه آسیب نبیند. در هر دو حالت دستور را به زور تکرار نکن؛ فقط نتیجه را گزارش کن.

## ۳. یک آزمون تازه، نه ارسال دوباره

بعد از موفقیت، در پنل وضعیت «آماده» را ببین. یک ضبط کوتاه با حساب‌ها و **گروه آزمایشی**، صدای ساختگی و یک تصویر تخته بگیر. پس از تأیید صریح ارسال در پایان همان زنگ، با همان تأخیر ثبت‌شده و در بازهٔ بدون کلاس، رسیدن و بازشدن فیلم در گروه آزمایشی را بررسی کن. مهلت ساخت، اولین زمان مجاز ارسال، تشخیص مقصد و قواعد رسید تغییر نکرده‌اند.
