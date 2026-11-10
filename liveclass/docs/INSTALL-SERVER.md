# راهنمای نصب LiveClass روی سرور (اوبونتو/دبیان)

> نسخه‌ی خلاصه‌ی عملیاتی — همه‌ی دستورات از داخل ریپو اجرا می‌شوند. برای جزئیات هر بخش، `README.md` در ریشه‌ی liveclass را ببینید.

## ۰) پیش‌نیازها

| چیز | حداقل | پیشنهاد |
|---|---|---|
| سیستم‌عامل | Ubuntu 22.04 / Debian 12 | Ubuntu 24.04 LTS |
| RAM | ۲ GB (تا ~۳۰ نفر، ۴–۶ ناشر) | ۴–۸ GB (تا ~۲۰۰ نفر) |
| vCPU | ۱–۲ | ۲–۴ |
| دیسک | ۱۰ GB | ۲۰ GB+ (اگر ضبط فعال باشد خیلی بیشتر) |
| دامنه | اختیاری ولی برای HTTPS الزامی | دو رکورد DNS: `class.example.com` و `livekit.class.example.com` → IP سرور |

**چرا HTTPS الزامی است؟** مرورگرها فقط در «زمینه‌ی امن» (HTTPS یا localhost) اجازه‌ی دسترسی به دوربین/میکروفون می‌دهند. بدون دامنه و گواهی، ویدیو روی اینترنت کار نمی‌کند.

رکوردهای DNS را **قبل از نصب** بسازید تا certbot موفق شود. پورت‌های باز لازم: ۸۰/tcp و ۴۴۳/tcp (وب + TLS)، ۷۸۸۱/tcp (LiveKit TCP fallback)، **۵۰۰۰۰–۶۰۰۰۰/udp** (مدیا WebRTC).

## ۱) دریافت کد

### راه الف — دانلود مستقیم از GitHub (ریپو عمومی است)
```bash
sudo apt-get update && sudo apt-get install -y curl unzip
curl -fL -o liveclass-install.zip https://raw.githubusercontent.com/Maxess/bacirat-function/arena/977de953-bacirat-function/liveclass-install.zip
unzip -o liveclass-install.zip && cd liveclass
```
> بعد از merge شدن شاخه به main آدرس دائمی می‌شود: `https://raw.githubusercontent.com/Maxess/bacirat-function/main/liveclass-install.zip`

### راه ب — با git
```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/Maxess/bacirat-function.git ~/bacirat-function
cd ~/bacirat-function/liveclass
```

> اگر فقط `liveclass/` را می‌خواهید: همان پوشه را کپی کنید؛ بقیه‌ی ریپو (persianmeet و …) لازم نیست.

## ۲) نصب یک‌دستوری (با دامنه — توصیه‌شده)

```bash
sudo DOMAIN=class.example.com LIVEKIT_DOMAIN=livekit.class.example.com ./scripts/install-liveclass.sh
```

اسکریپت خودش انجام می‌دهد:
1. نصب وابستگی‌ها (`python3-venv`, `nginx`, `ufw`, `certbot` …) — **node لازم نیست** (خروجی `web/dist` داخل ریپو commit شده است؛ اگر نبود، روی یک دستگاه با node بسازید: `cd web && npm ci && npm run build`)
2. کپی به `/opt/liveclass`، ساخت venv، نصب `requirements.txt`
3. ساخت ادمین اولیه (رمز را از شما می‌پرسد؛ در حالت غیرفعال رمز تصادفی می‌سازد و همان‌جا نشان می‌دهد)
4. نصب **LiveKit Server** از GitHub Releases + تولید کلید واقعی + سرویس systemd (`livekit.service`)
5. نوشتن `/etc/liveclass/liveclass.env` (سِکرت‌ها تصادفی، `chmod 600`) و `liveclass.service`
6. پیکربندی **nginx** (دو server-block: اپ روی ۸۲۰۰ و سیگنالینگ LiveKit روی ۷۸۸۰) + **گواهی Let's Encrypt خودکار** برای هر دو دامنه
7. تنظیم **ufw**: ۲۲/۸۰/۴۴۳/۷۸۸۱ باز + بازه‌ی UDP 50000-60000

بعد از اتمام: `https://class.example.com` — با `admin` و رمزی که ساختید وارد شوید. **اولین کار:** «⚙️ تنظیمات › تغییر رمز».

### بدون دامنه (فقط آزمایش محلی/شبکه‌ی داخلی)

```bash
sudo ./scripts/install-liveclass.sh
```
آدرس: `http://<IP>/` — **دوربین/میکروفون کار نمی‌کند** (زمینه‌ی ناامن)؛ فقط برای دیدن جریان‌هاست.

## ۳) بررسی «سالم است» پس از نصب

```bash
systemctl status liveclass livekit            # هر دو: active (running)
journalctl -u liveclass -n 50 --no-pager      # خطای پایتون/DB نباید باشد
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8200/api/config   # → 200
ss -tlnp | grep -E ':(8200|7880|7881)\b'      # سه پورت گوش می‌دهند
ufw status | grep -E '50000|443|80|7881'      # قوانین فایروال
```

سپس از مرورگر: ورود ادمین → ساخت یک کلاس → اضافه‌کردن دانش‌آموز → شروع کلاس → بازکردن لینک دانش‌آموز در یک مرورگر دیگر → ویدیو/صدا/چت/تخته/PDF.

## ۳٫۵) HTTPS خودکار (auto-ssl) — معادل auto_ssl پرشین‌مییت

اگر نصب را با `--domain` اجرا کرده باشید، این مراحل خودکار انجام می‌شوند؛ این بخش برای مواردی است که نصب بدون دامنه انجام شده یا بعداً دامنه اضافه می‌کنید.

```bash
sudo DOMAIN=class.example.com LIVEKIT_DOMAIN=livekit.class.example.com \
  ./scripts/auto-ssl.sh
```

چه می‌کند (idempotent — هر بار اجرا کنید مشکلی ندارد):
1. پیکربندی موقت HTTP برای چالش ACME (webroot) ← ری‌لود nginx
2. صدور (یا استفاده‌ی مجدد از) گواهی Let's Encrypt برای اپ و زیردامنه‌ی LiveKit
3. پیکربندی نهایی nginx: ریدایرکت ۸۰→۴۴۳، HSTS، هدرهای امنیتی، `client_max_body_size 30m` (PDF)، WebSocket برای LiveKit
4. تمدید خودکار: `certbot.timer` + deploy-hook که بعد از هر تمدید nginx را ری‌لود می‌کند
5. ویرایش `/etc/liveclass/liveclass.env` (با بکاپ): `LIVECLASS_SECURE_COOKIES=1` و `LIVECLASS_LIVEKIT_PUBLIC_URL=wss://livekit.class.example.com` ← ری‌استارت سرویس
6. بررسی نهایی و نمایش تاریخ انقضای گواهی

نکات صادقانه:
- نیازمند DNS صحیح و پورت‌های ۸۰/۴۴۳ باز است؛ بدون آن certbot شکست می‌خورد (اسکریپت خطا می‌دهد ولی اپ را خراب نمی‌کند).
- برای تست بدون مصرف سهمیه‌ی Let's Encrypt: `STAGED=1 DOMAIN=… ./scripts/auto-ssl.sh`.
- این معماری «auto_ssl داخلی باینری پرشین‌مییت» نیست؛ معادل عملی آن روی پشته‌ی ما (nginx + certbot) است و همان نتیجه را می‌دهد: HTTPS معتبر + تمدید خودکار بدون دخالت شما.

## ۴) کارهای بعد از نصب (توصیه‌شده)

```bash
# الف) تغییر رمز admin از داخل اپ (⚙️ تنظیمات)
# ب) ساخت دبیران در «👥 کاربران»؛ ساخت کاربر مهمان در صورت نیاز
# ج) افزودن دبیر/مهمان به هر کلاس: صفحه‌ی کلاس → «🔐 دسترسی‌های کلاس»
# د) بکاپ را تست کنید: ⚙️ تنظیمات → «⬇️ دانلود بکاپ» (فایل zip محرمانه است)
```

### ضبط جلسه (اختیاری — پیش‌فرض خاموش)

نیازمند داکر؛ سنگین (حدود ۳× CPU بیشتر). روی سرور ضعیف توصیه نمی‌شود.

```bash
sudo ./scripts/install-livekit.sh --with-egress
cat /etc/liveclass/recordings.env        # دو خط: LIVECLASS_EGRESS_ENABLE=1 و LIVECLASS_RECORDINGS_DIR=/opt/liveclass/recordings
sudoedit /etc/liveclass/liveclass.env    # همان دو خط را اضافه کنید
sudo systemctl restart liveclass
```
بعد از آن دکمه‌ی ⏺ ضبط در اتاق برای دبیر روشن می‌شود؛ فایل MP4 در همان صفحه‌ی کلاس («خروجی») دانلود می‌شود.
**صادقانه:** این مسیر در محیط توسعه‌ی ما (بدون داکر) فقط به‌صورت mock تست شده؛ روی سرور واقعی حتماً یک جلسه‌ی کوتاه را ضبط و دانلود کنید. بدون این مراحل، دکمه‌ی ضبط خطای «فعال نیست» می‌دهد و بقیه‌ی اپ سالم است.

## ۵) نگهداری

```bash
# به‌روزرسانی (با بکاپ خودکار قبل از هر آپدیت)
cd ~/bacirat-function && git pull
sudo ./liveclass/scripts/update-liveclass.sh

# بکاپ دستی (دیتابیس + تنظیمات + برند)
sudo ./liveclass/scripts/backup-liveclass.sh

# یا بکاپ کامل از داخل اپ: ⚙️ تنظیمات → «⬇️ دانلود بکاپ»

# لاگ‌ها
journalctl -u liveclass -f
journalctl -u livekit -f

# ری‌استارت
sudo systemctl restart liveclass livekit
```

بکاپ‌ها را خارج از سرور نگه دارید (مثلاً دانلود دوره‌ای zip از پنل). `backup-liveclass.sh` و دکمه‌ی دانلود هر دو `liveclass.db` و تنظیمات را شامل می‌شوند.

## ۶) نکات مهم عملیاتی

- **فایروال/امنیت ابری:** اگر سرور پشت Security Group/Cloud Firewall است (AWS/GCP/Hetzner/…) همان پورت‌های ufw را آنجا هم باز کنید: ۸۰، ۴۴۳، ۷۸۸۱/tcp و ۵۰۰۰۰–۶۰۰۰۰/udp.
- **UDP مدیا بسته باشد → تصویر یخ می‌زند** ولی چت/تخته کار می‌کند. اول UDP را چک کنید.
- **حافظه:** اگر «خارج از حافظه» دیدید: `LIVECLASS_MAX_PUBLISHERS=4` در `/etc/liveclass/liveclass.env` و ری‌استارت.
- **کیفیت پیش‌فرض کلاس‌ها:** کاربران می‌توانند در «⚙️ تنظیمات» یکی از سه حالت کیفیت (اقتصادی/متعادل/کیفیت بالا) و فریم‌ریت ۱۵/۲۰/۲۴ را انتخاب کنند؛ پیش‌فرض «اقتصادی ۶۴۰×۳۶۰@۱۵» است که روی سرور ضعیف بهترین گزینه است.
- **PDF روی تخته:** سقف ۲۵MB؛ برای فایل‌های خیلی بزرگ ابتدا `qpdf --linearize` یا کاهش کیفیت اسکن پیشنهاد می‌شود.
- **بکاپ داخل zip شامل رمزهای عبور هش‌شده است** — مثل رمز اصلی نگهداری کنید.

## ۷) عیب‌یابی سریع

| نشانه | علت محتمل | راه‌حل |
|---|---|---|
| `502 Bad Gateway` از nginx | سرویس liveclass بالا نیست | `systemctl status liveclass`؛ `journalctl -u liveclass -n 100` |
| صفحه باز می‌شود ولی ویدیو نه | دوربین در زمینه‌ی ناامن؛ یا WSS اشتباه | HTTPS داشته باشید؛ `LIVECLASS_LIVEKIT_PUBLIC_URL=wss://livekit.domain` را چک کنید |
| «connection to livekit failed» | سیگنالینگ ۷۸۸۰ یا TLS زیردامنه مشکل دارد | `curl -I https://livekit.domain/`؛ لاگ certbot؛ فایروال ۴۴۳ |
| تصویر یخ می‌زند ولی صدا هست | UDP بسته است | بازه‌ی ۵۰۰۰۰–۶۰۰۰۰/udp در ufw **و** فایروال ابری |
| آپلود PDF با خطای ۴۱۳ | `client_max_body_size` قدیمی | باید `30m` باشد (در نسخه‌های جدید اسکریپت هست؛ `nginx -t && systemctl reload nginx`) |
| ضبط کار نمی‌کند (۵۰۳) | Egress یا `LIVECLASS_EGRESS_ENABLE=1` نیست | بخش ۴ را انجام دهید؛ `docker ps` باید `liveclass-egress` را نشان دهد |
| گواهی TLS نگرفت | DNS هنوز propagate نشده یا ۸۰ بسته است | رکورد DNS/پورت ۸۰ را چک و `sudo DOMAIN=… ./scripts/auto-ssl.sh` را دوباره بزنید |

## ۸) حذف

```bash
sudo ./liveclass/scripts/uninstall-liveclass.sh   # با بکاپ خودکار قبل از حذف
```

---

**چک‌لیست نهایی قبل از اعلام «آماده‌ی استفاده»:** HTTPS سبز در مرورگر (قفل) ← ورود ادمین ← ساخت کلاس و دانش‌آموز ← ویدیو/صدا دوطرفه بین دو دستگاه واقعی ← تخته ← PDF ← بکاپ دانلود شد ← (اختیاری) یک ضبط آزمایشی.
