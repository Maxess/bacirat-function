# عیب‌یابی PersianMeet

## دانلود پکیج در سرور انجام نمی‌شود

- اگر `wget https://app2.learn100.ir/upload/dl/persianmeet-linux-v0.26.3.zip` کار نکرد:
  1. لینک رسمی قبلی را امتحان کنید: `https://app.learn100.ir/upload/dl/persianmeet-linux-v0.26.3.zip` (چند نسخه‌ی قدیمی‌تر با نام `app.` منتشر شده‌اند).
  2. با curl تست کنید: `curl -I <url>` — اگر HTTP 200 دیدید اما سرعت صفر است، محدودیت شبکه/تحریم روی سرور است؛ از یک اتصال دیگر یا VPN روی **سیستم خودتان** فایل را دانلود کنید و با `scp` به سرور کپی کنید:
     ```bash
     scp persianmeet-linux-v0.26.3.zip root@<server>:/tmp/
     sudo /path/to/repo/deploy/install.sh --from-file /tmp/persianmeet-linux-v0.26.3.zip
     ```
- بعد از `unzip`، حجم تقریبی پوشه را چک کنید؛ تکه‌ی ناقص رایج است.

## خطای `Permission denied` موقع اجرای `./persianmeet`

```bash
cd /opt/persianmeet
sudo chmod +x persianmeet app/optimized
```
اگر باز هم نشد: پارتشن را با `noexec` mount کرده‌اند؟ `mount | grep $(df --output=target /opt/persianmeet | tail -1)` را ببینید.

## صفحه‌ی https باز نمی‌شود / گواهی SSL نمی‌آید

1. دامنه **حتماً** باید به IP سرور resolve شود: `dig +short class.example.com`
2. پورت‌های ۸۰ و ۴۴۳ از بیرون بازند: `ufw status` — حداقل `80/tcp` و `443/tcp` باید ALLOW باشند.
3. هیچ سرویس دیگری روی ۸۰/۴۴۳ نیست (`ss -tulpn | grep -E ':(80|443)\b'`)؛ اگر apache/nginx هست، یکی را خاموش کنید یا مسیر Nginx-reverse-proxy را انتخاب کنید (دلیل: محصول برای صدور Let's Encrypt به ۸۰ نیاز دارد).
4. لاگ را ببینید: `journalctl -u persianmeet -n 200 --no-pager`
5. اگر دامنه ندارید و فقط IP دارید: `auto_ssl: false` کنید و روی `http://IP:8003` کار کنید (HTTPS نخواهد بود) یا گواهی خودامضا پشت nginx بسازید.

## ورود با `admin / 1111` کار نمی‌کند

- اولین بار است؟ مطمئن شوید پوشه‌ی کاری (`WorkingDirectory`) همان پوشه‌ی نصب است؛ بعضی نسخه‌ها رمز پیش‌فرض را از یک فایل state محلی می‌خوانند. سرویس را با همان CWD اجرا کنید (یونیت systemd همین کار را می‌کند).
- اگر رمز را عوض کرده‌اید و فراموش شده: معمولاً راه‌حل، توقف سرویس و بازنشانی مطابق مستندات سازنده است (با پشتیبانی/فروشنده تماس بگیرید)؛ **فایل‌های دیتابیس محصول را دستی ویرایش نکنید.**

## اتاق می‌سازم اما ویدئو/صدا کار نمی‌کند

این تقریباً همیشه یعنی **ترافیک UDP از الزام WebRTC عبور نمی‌کند**:

1. پورت‌های باز محصول را ببینید: `ss -tulpn` و `ss -ulpn` روی سرور.
2. در فایروال خودِ سرور + فایروال ابری/دیتاسنتر (security group) بازه‌ی UDP لازم را باز کنید.
3. اگر پشت NAT هستید، پورت‌فوروردینگ UDP را انجام دهید.
4. مرورگر را Chrome تازه نگه دارید؛ `chrome://webrtc-internals` برای دیباگ مفید است.
5. اگر UDP اصلاً عبور نمی‌کند، اکثر کلاینت‌ها روی TURN fallback می‌کنند — از سازنده بخواهید راهنمای TURN برای نسخه‌ی شما را بدهد.

## فایل‌ها/ضبط‌ها کجا ذخیره می‌شوند؟

```bash
find /opt/persianmeet -type d \( -iname "*files*" -o -iname "*upload*" -o -iname "*record*" \) 2>/dev/null
```
در توزیع داکر، داده‌ها زیر `wwwroot/files/` (مسیر `dataPath`) هستند؛ در توزیع تک‌باینری هم معمولاً همین ساختار وجود دارد. برای بکاپ کامل همین پوشه‌ها + `config.yml` را بگیرید (`deploy/backup.sh` همین کار را می‌کند).

## بعد از آپدیت، لوگو/CSS های سفارشی من از بین رفت!

طبیعی است — نسخه‌ی جدید فایل‌ها را بازنویسی می‌کند. برای همین:

- قبل از هر آپدیت `sudo deploy/backup.sh` را بزنید (به‌طور خودکار در `update.sh` انجام می‌شود)
- یا تغییرات ظاهری را از طریق **Nginx `sub_filter`** اعمال کنید تا هرگز با آپدیت پاک نشود (`docs/02-customization.md` § روش ب)

## مصرف منابع بالا است

- ویدئو = CPU/RAM زیاد می‌خورد. روی سرور ۲ گیگی زیر ۵۰ کاربر هم‌زمان تست کنید؛ برای بیشتر، ۴+ گیگ و ۴ هسته بگیرید.
- Recording (ffmpeg) را برای اتاق‌های خیلی شلوغ محدود کنید.
- `journalctl -u persianmeet` را برای کرش/restarts بررسی کنید.

## همچنان گیر کردم

اطلاعات لازم برای درخواست کمک (به فروشنده یا به ما): خروجی `ss -tulpn`, `journalctl -u persianmeet -n 100 --no-pager`, `cat config.yml` (بدون secret!), حجم `free -h`, نسخه‌ی دقیق (`ls /opt/persianmeet`).
