# راهنمای دیپلوی PersianMeet روی سرور لینوکس (اوبونتو/دبیان)

دو مسیر دارید: **الف) خودکار با اسکریپت این ریپو (توصیه می‌شود)** یا **ب) دستی مطابق مستندات رسمی**.

---

## مسیر الف — نصب خودکار

روی سرور تمیز اوبونتو/دبیان (به عنوان root یا با sudo):

```bash
git clone <آدرس این ریپو>
cd bacirat-function
sudo DOMAIN=class.example.com ./deploy/install.sh
```

جدول متغیرهای محیطی که می‌توانید بدهید:

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `DOMAIN` | (الزامی برای حالت عمومی) مثل `class.example.com` | دامنه‌ای که A-record آن به همین سرور اشاره کند |
| `VERSION` | `0.26.3` | شماره‌ی نسخه‌ی پکیج |
| `DOWNLOAD_URL` | `https://app2.learn100.ir/upload/dl/persianmeet-linux-v${VERSION}.zip` | اگر سازنده لینک را عوض کرد همین‌جا override کنید |
| `INSTALL_DIR` | `/opt/persianmeet` | مقصد نصب |
| `SECRET` | تصادفی ۲۴ بایتی | secret برای API |
| `OPEN_UFW` | `1` | بازکردن ۸۰/۴۴۳/۸۰۰۳ در ufw |
| `ADMIN_EMAIL` | خالی | فقط برای نمایش راهنما (Let's Encrypt خودِ محصول هندل می‌کند) |

اسکریپت چه کار می‌کند: آپدیت apt → نصب `wget nano unzip mupdf-tools curl ufw` → دانلود همان لینک رسمی →
`unzip` به `/opt/persianmeet` → `chmod +x persianmeet app/optimized` → ساخت `config.yml` (با `auto_ssl: true`
روی دامنه + `secret` تصادفی) → نصب و فعال‌سازی سرویس systemd → تنظیم فایروال → چاپ مراحل نهایی.

بعد از آن:

```bash
journalctl -u persianmeet -f          # لاگ زنده
ss -tulpn | grep -E '80|443|8003'   # پورت‌ها
```

سپس به `https://class.example.com` بروید، با `admin/1111` وارد شوید و **فوراً رمز را عوض کنید**.

### آپدیت بعدی

```bash
sudo VERSION=0.27.0 ./deploy/update.sh
```
بکاپ خودکار از `config.yml` و فایل‌های آپلودی گرفته می‌شود و بعد نسخه‌ی جدید بر اساس همان ساختار جایگزین می‌شود.

### حذف کامل

```bash
sudo ./deploy/uninstall.sh           # فقط سرویس را حذف می‌کند؛ فایل‌ها نگه داشته می‌شوند
sudo ./deploy/uninstall.sh --purge   # به‌علاوه حذف /opt/persianmeet (با تأیید)
```

### بکاپ دوره‌ای

```bash
sudo ./deploy/backup.sh /var/backups/persianmeet
# در cron هفتگی:
#   15 3 * * 6 /path/to/repo/deploy/backup.sh /var/backups/persianmeet >> /var/log/pm-backup.log 2>&1
```

---

## مسیر ب — نصب دستی (دقیقاً طبق مستندات رسمی)

```bash
sudo apt-get update
sudo apt-get install -y wget nano unzip mupdf-tools

wget https://app2.learn100.ir/upload/dl/persianmeet-linux-v0.26.3.zip
unzip persianmeet-linux-v0.26.3.zip
cd persianmeet

sudo chmod +x persianmeet app/optimized

sudo nano config.yml    # auto_ssl: true  +  secret: "یک رشته طولانی تصادفی"

./persianmeet &         # برای سرویس پایدار: از deploy/persianmeet.service استفاده کنید
```

مرورگر: `https://class.example.com` — ورود `admin` / `1111`.

---

## تنظیم DNS برای auto_ssl

۱. یک رکورد `A` بسازید: `class.example.com → IP.سرور.شما`
۲. (اختیاری برای IPv6) رکورد `AAAA` هم بزنید.
۳. پورت‌های ۸۰ و ۴۴۳ باید از بیرون باز باشند (برای challenge و پس از آن HTTPS).
۴. اولین اجرا ممکن است ۳۰–۹۰ ثانیه برای صدور گواهی طول بکشد؛ با `journalctl -u persianmeet -f` پیشرفت را ببینید.

## (اختیاری) قرار دادن Nginx جلوی PersianMeet

اگر می‌خواهید مدیریت TLS دستی/شرکتی داشته باشید (نه خودکار Let's Encrypt محصول):

1. `auto_ssl: false` در `config.yml` — محصول فقط روی `http://127.0.0.1:8003` سرو می‌شود.
2. `deploy/nginx.reverse-proxy.conf` را کپی و ویرایش کنید (دامنه و مسیر سرتیفیکیت شما).
3. `systemctl reload nginx`

**توجه:** اگر nginx را بعداً جلوی آن بگذارید، و همزمان `auto_ssl: true` هم باشد، دو TLS روی هم conflict می‌شود. یکی را انتخاب کنید.

## امنیت بعد از نصب (چک‌لیست ۲ دقیقه‌ای)

- [ ] رمز ادمین را عوض کنید.
- [ ] `secret` در `config.yml` یک رشته‌ی طولانی تصادفی است (نه `123456`).
- [ ] فقط ۸۰/۴۴۳/۲۲ از ufw باز است.
- [ ] بکاپ هفتگی فعال است.
- [ ] پورت ۸۰۰۳ از بیرون **بسته** است (فقط localhost) اگر پشت nginx هستید.
- [ ] نسخه‌ی محصول را به‌روز نگه دارید (`update.sh`).
