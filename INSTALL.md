# راهنمای نصب — پلتفرم کلاس مجازی

نصب روی سرور لینوکس، مستقیماً از این ریپو. کل سیستم **یک باینری Go بدون
وابستگی خارجی** است: نه پایگاه داده، نه Node، نه بیلد فرانت‌اند.

---

## بخش صفر — پیش‌نیازها

| نیاز | نسخه | برای چه |
|---|---|---|
| Go | ۱.۲۴ یا بالاتر | فقط برای بیلد (روی سرور نهایی لازم نیست) |
| systemd | — | اجرای سرویس |
| PHP + PDO_MySQL | ۷.۴+ | **فقط** برای همگام‌سازی با سیستم مدیریت مدرسه (اختیاری) |

بررسی:

```sh
go version
php -m | grep -i pdo_mysql   # فقط اگر همگام‌سازی مدرسه می‌خواهید
```

---

## بخش یک — نصب

```sh
git clone https://github.com/Maxess/bacirat-function.git
cd bacirat-function
sudo ./install.sh
```

اسکریپت این کارها را می‌کند:

1. باینری را با `CGO_ENABLED=0` می‌سازد (بدون وابستگی به libc)
2. کاربر سیستم `bacirat` و پوشه‌های داده در `/opt/bacirat` می‌سازد
3. یک رمز تصادفی برای ادمین تولید و **هش‌شده** ذخیره می‌کند
4. واحد systemd را نصب و فعال می‌کند

**رمز ادمین را یادداشت کنید — فقط یک بار نمایش داده می‌شود.**

گزینه‌ها:

```sh
sudo ./install.sh --dir /opt/bacirat --user bacirat --port 8443
```

اجرای دوبارهٔ اسکریپت بی‌خطر است (فقط بیلد مجدد و راه‌اندازی دوباره)؛
فایل `data/config.json` و در نتیجه رمز ادمین دست‌نخورده می‌ماند.

در پایان می‌بینید:

```
administrator login:    admin
administrator password: ********************
Class page:  https://SERVER:8443/group/NAME/
Admin panel: https://SERVER:8443/admin.html
```

> گواهی TLS در اولین اجرا self-signed ساخته می‌شود. برای استفادهٔ واقعی،
> گواهی معتبر را در `data/cert.pem` و `data/key.pem` بگذارید، یا سرور را
> پشت reverse proxy اجرا کنید.

### نصب دستی (بدون اسکریپت)

```sh
cd galene
CGO_ENABLED=0 go build -ldflags='-s -w'

mkdir -p /opt/bacirat/{groups,data,recordings}
cp galene /opt/bacirat/
cp -r static/. /opt/bacirat/static/
cp ../export-school.php /opt/bacirat/ && chmod 700 /opt/bacirat/export-school.php

cat > /opt/bacirat/data/config.json <<'EOF'
{
    "writableGroups": true,
    "users": {
        "admin": {"password": {"type": "plain", "key": "CHANGE_ME"},
                  "permissions": "admin"}
    }
}
EOF

/opt/bacirat/galene -http :8443 \
  -static /opt/bacirat/static/ -data /opt/bacirat/data/ \
  -groups /opt/bacirat/groups/ -recordings /opt/bacirat/recordings/
```

> **`writableGroups: true` ضروری است.** بدون آن، ساخت کلاس و همگام‌سازی
> مدرسه کار نمی‌کند.

---

## بخش دو — بررسی صحت نصب

```sh
# ۱. کامپایل همهٔ بسته‌ها
cd bacirat-function/galene
go build ./...

# ۲. اجرای تست‌ها
go test ./...
```

سپس سرویس:

```sh
systemctl status bacirat
journalctl -u bacirat -f
```

و در مرورگر:

| آدرس | انتظار |
|---|---|
| `https://SERVER:8443/` | صفحهٔ اصلی + لینک «Administration» |
| `https://SERVER:8443/admin.html` | پنل مدیریت (با یوزر/رمز ادمین) |

---

## بخش سه — اتصال به سیستم مدیریت مدرسه (اختیاری)

کلاس‌ها و اعضا از پایگاه دادهٔ سیستم مدیریت مدرسه خوانده می‌شوند،
**بدون هیچ تغییری در آن سیستم**.

```
mtagbot (MySQL)  →  export-school.php  →  data/school-export.json  →  Galene
      فقط خوانده می‌شود          روی همین سرور              گروه‌های class-{id}
```

احراز هویت پس از همگام‌سازی **کاملاً محلی** است؛ کلاس حتی با قطع بودن
پایگاه دادهٔ مدرسه هم برقرار می‌ماند.

### نگاشت

| سیستم مدیریت مدرسه | کلاس مجازی |
|---|---|
| `classes.id` | نام گروه `class-{id}` — با تغییر نام کلاس عوض نمی‌شود |
| `classes.name` | نام نمایشی (مثلاً «101 ریاضی») |
| `classes.grade` | توضیحات |
| `students.national_id` | نام کاربری |
| `students.serial_number` (۶ رقم) | رمز عبور |
| `first_name + last_name` | نام نمایشی در لیست و چت |
| `teachers.personnel_code` | رمز عبور دبیر |
| — | نقش `student` یا `teacher` |

فقط کلاس‌های **سال تحصیلی پیش‌فرض** همگام می‌شوند (از
`settings.current_academic_year`، و در نبود آن `academic_years.is_default`).

### ۳-۱. تنظیم خروجی‌گیر

`export-school.php` فقط پایگاه را **می‌خواند**. با متغیرهای محیطی پیکربندی
می‌شود:

```sh
export DB_HOST=127.0.0.1
export DB_NAME=school
export DB_USER=root
export DB_PASS=SECRET
php /opt/bacirat/export-school.php
```

در صورت موفقیت، یک JSON شامل کلاس‌ها و اعضا چاپ می‌شود. اگر خروجی خالی
یا خطا بود، ابتدا اتصال به پایگاه را بررسی کنید.

### ۳-۲. فعال‌سازی همگام‌سازی

```sh
cat > /opt/bacirat/data/school.json <<'EOF'
{
  "exportCommand": "php /opt/bacirat/export-school.php > /opt/bacirat/data/school-export.json",
  "syncInterval": "1h",
  "bcryptCost": 8
}
EOF

systemctl restart bacirat
```

فیلدها (همه اختیاری):

| فیلد | معنا |
|---|---|
| `exportFile` | مسیر فایل خروجی؛ پیش‌فرض `school-export.json` (نسبت به `data/`) |
| `exportCommand` | دستوری که پیش از هر همگام‌سازی اجرا می‌شود |
| `commandWritesStdout` | اگر `true` باشد، خروجی دستور مستقیماً خوانده می‌شود |
| `syncInterval` | بازهٔ همگام‌سازی، مثلاً `1h`؛ اگر نباشد فقط دستی |
| `bcryptCost` | هزینهٔ هش؛ پیش‌فرض ۸ |

> **بدون این فایل، بخش مدرسه کاملاً غیرفعال است** و سرور دقیقاً مثل یک
> Galene معمولی رفتار می‌کند.

### ۳-۳. اجرا و بررسی

در پنل مدیریت، تب **School sync**:

- دکمهٔ **Sync now** = همگام‌سازی فوری
- جدول وضعیت = زمان آخرین اجرا و آخرین خطا
- قالب دقیق فایل خروجی هم در همان تب نمایش داده می‌شود

یا از خط فرمان:

```sh
curl -k -u admin:PASSWORD https://localhost:8443/galene-api/v0/.school/status
curl -k -u admin:PASSWORD -X POST https://localhost:8443/galene-api/v0/.school/sync
```

بررسی نتیجه:

```sh
ls /opt/bacirat/groups/          # باید class-*.json ببینید
cat /opt/bacirat/groups/class-12.json
```

ساختار انتظاری:

```json
{
  "displayName": "101 ریاضی",
  "description": "دهم ریاضی",
  "users": {
    "0012345678": {"password": {"type":"bcrypt","key":"$2a$08$..."},
                   "permissions": "student"},
    "5560755680": {"password": {"type":"bcrypt","key":"$2a$08$..."},
                   "permissions": "teacher"}
  }
}
```

### ۳-۴. خودکارسازی با cron (جایگزین syncInterval)

اگر ترجیح می‌دهید سرور خودش دستور را اجرا نکند:

```sh
cat > /etc/cron.d/bacirat-school <<'EOF'
DB_HOST=127.0.0.1
DB_NAME=school
DB_USER=root
DB_PASS=SECRET
17 * * * * root php /opt/bacirat/export-school.php > /opt/bacirat/data/school-export.json
EOF
```

و در `data/school.json` فقط بگذارید:

```json
{ "syncInterval": "1h" }
```

### نکات عملکردی

- رمز فقط زمانی دوباره هش می‌شود که **واقعاً تغییر کرده باشد**؛ نقش هم فقط
  در صورت تغییر بازنویسی می‌شود. بنابراین همگام‌سازی دوم به بعد عملاً
  بدون هزینه است (۰ نوشتن، ۰ هش برای اعضای بدون تغییر).
- دانش‌آموزی که از کلاس خارج شود، در همگام‌سازی بعدی حذف می‌شود؛ اما
  کاربران `op` که دستی ساخته‌اید **هرگز** حذف نمی‌شوند.
- دستور خروجی با مهلت ۲ دقیقه اجرا می‌شود؛ اسکریپتِ معلق نمی‌تواند سرور را
  قفل کند.

---

## بخش چهار — امکانات

### نقش‌ها

| نقش | مجوزها |
|---|---|
| `teacher` | حرف زدن، چت، زیرنویس، تختهٔ سفید، صدور لینک دعوت، مدیریت کلاس |
| `student` | چت |

دبیر می‌تواند: اجازه/سلب اجازهٔ صحبت، بستن میکروفون، اخراج، پاک کردن چت،
صدور لینک دعوت. ارتقا به `op` و مشاهدهٔ IP فقط برای `op` است.

تعریف دستی یک کلاس (بدون همگام‌سازی) در `groups/math-101.json`:

```json
{
  "displayName": "ریاضی ۱۰۱",
  "public": true,
  "max-clients": 40,
  "users": {
    "teacher": {"password": "SECRET", "permissions": "teacher"},
    "alice":   {"password": "SECRET", "permissions": "student"}
  }
}
```

### تختهٔ سفید

بدون وابستگی: خطوط از کانال پیام‌های Galene عبور می‌کنند. مختصات
نرمال‌شده، ارسال دسته‌ای با نرخ ~۳۰/s، دریافت وضعیت کامل هنگام ورود، و
سقف ۵۰۰ خط برای هر کلاس. فقط دارندهٔ مجوز `whiteboard` (دبیر) می‌نویسد.

### حضور و غیاب

در `data/attendance/{group}/{date}.jsonl` — یک خط JSON برای هر رویداد،
به‌صورت ناهمزمان (دیسک کند هرگز کلاس را منتظر نمی‌گذارد). نمایش در تب
**Attendance** با خروجی CSV.

### برندینگ

در تب **Branding** پنل مدیریت: نام سایت، شعار، لوگو، favicon، رنگ اصلی.
بدون نیاز به بیلد مجدد.

> به دلیل سیاست امنیتی محتوا (CSP)، نشانی لوگو و favicon باید از همان
> سرور باشد (مثلاً `/logo.png` داخل پوشهٔ `static/`).

### API برای وب‌سایت

```
GET  /api/v1/groups
POST /api/v1/groups/{name}/sync
GET  /galene-api/v0/.attendance/{group}[?date=YYYY-MM-DD]
GET  /galene-api/v0/.attendance/{group}/.dates
GET  /galene-api/v0/.whiteboard/{group}
GET  /galene-api/v0/.school/status
POST /galene-api/v0/.school/sync
```

همه با احراز Basic (یوزر/رمز ادمین).

---

## بخش پنج — عیب‌یابی

| نشانه | علت احتمالی | بررسی |
|---|---|---|
| همگام‌سازی خطای ۴۰۳/۵۰۰ می‌دهد | `writableGroups` فعال نیست | `grep writableGroups /opt/bacirat/data/config.json` |
| «no such file» در لاگ | فایل خروجی وجود ندارد | فایل `data/school-export.json` را بسازید |
| دستور خروجی اجرا نمی‌شود | مسیر `php` یا دسترسی | دستور را دستی با کاربر `bacirat` اجرا کنید |
| دانش‌آموز وارد نمی‌شود | سریال شناسنامه خالی است | در این صورت رمز همان کد ملی خواهد بود |
| کلاس ساخته نشد | کلاس در سال پیش‌فرض نیست | مقدار `current_academic_year` را بررسی کنید |
| تغییرات مدرسه اعمال نمی‌شود | همگام‌سازی اجرا نشده | `journalctl -u bacirat \| grep -i school` |

بررسی دستیِ یک ورود:

```sh
# خروجی مدرسه
php /opt/bacirat/export-school.php | head -40

# گروه‌های ساخته‌شده
ls /opt/bacirat/groups/
```

---

## بخش شش — به‌روزرسانی

```sh
cd bacirat-function
git pull
sudo ./install.sh
```

فایل `data/config.json` (رمز ادمین)، `data/school.json` و پوشهٔ
`groups/` دست‌نخورده باقی می‌مانند.

---

## ساختار فایل‌های نصب‌شده

```
/opt/bacirat/
├── galene              باینری اصلی
├── export-school.php   خروجی‌گیر مدرسه (فقط خواندن)
├── static/             رابط کاربری
├── data/               تنظیمات، گواهی، برندینگ، حضور و غیاب
│   ├── config.json     کاربر ادمین + writableGroups
│   ├── school.json     تنظیم همگام‌سازی (اختیاری)
│   └── attendance/     حضور و غیاب
├── groups/             تعریف کلاس‌ها (class-*.json)
└── recordings/         ضبط کلاس‌ها
```

## لایسنس

بر پایهٔ [Galene](https://github.com/jech/galene) — MIT
(فایل `galene/LICENCE`).
