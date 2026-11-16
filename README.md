# bacirat-function

پلتفرم کلاس مجازی شخصی، بر پایهٔ **Galene** (سیستم ویدئوکنفرانس متن‌باز)
— قابل نصب روی سرور لینوکس، مستقیماً از همین ریپو.

## ساختار ریپو

| مسیر | توضیح |
|------|-------|
| `galene/` | سورس‌کد Galene با سفارشی‌سازی‌های کلاس مجازی |
| `install.sh` | نصب یک‌مرحله‌ای روی سرور لینوکس (بیلد + systemd) |
| `persianmeet/` | نسخهٔ قدیمی (باینری آمادهٔ PersianMeet) — فقط برای مرجع |

## نصب روی سرور لینوکس

دو راهنما وجود دارد:

| فایل | برای چه | چه زمانی |
|---|---|---|
| **[`PREPARE.md`](PREPARE.md)** | آماده‌سازی سرور: سخت‌افزار و پهنای باند، نصب Go، باز کردن پورت‌ها و فایروال، گواهی TLS، آماده‌سازی پایگاه داده، تنظیمات سیستم — با یک چک‌لیست خودکار در پایان | **اول این را بخوانید** |
| **[`INSTALL.md`](INSTALL.md)** | نصب خودِ پلتفرم، بررسی صحت، اتصال به سیستم مدیریت مدرسه، عیب‌یابی، به‌روزرسانی | پس از آماده‌سازی |

اگر سرور از قبل Go، فایروال و گواهی دارد، فقط چک‌لیست انتهای
`PREPARE.md` را اجرا کنید.

خلاصه:

پیش‌نیاز: **Go 1.24 یا بالاتر** روی سرور (فقط برای بیلد؛ خود سرویس فقط
یک باینری سبک و بدون وابستگی است).

```sh
git clone https://github.com/Maxess/bacirat-function.git
cd bacirat-function
sudo ./install.sh
```

اسکریپت به‌طور خودکار:

1. باینری را با `CGO_ENABLED=0` می‌سازد (بدون وابستگی به libc)
2. کاربر سیستمی و پوشه‌های داده را در `/opt/bacirat` می‌سازد
3. یک رمز عبور تصادفی برای ادمین تولید و **هش‌شده** ذخیره می‌کند
4. سرویس `systemd` را نصب و فعال می‌کند

گزینه‌ها: `--dir /opt/bacirat`، `--user bacirat`، `--port 8443`

پس از نصب:

- صفحهٔ کلاس: `https://SERVER:8443/group/NAME/`
- **پنل مدیریت**: `https://SERVER:8443/admin.html`
- صفحهٔ اصلی: `https://SERVER:8443/`

> گواهی TLS در اولین اجرا به‌صورت self-signed ساخته می‌شود. برای استفادهٔ
> واقعی، گواهی معتبر را در `data/cert.pem` و `data/key.pem` قرار دهید یا
> سرور را پشت reverse proxy اجرا کنید.

### نصب دستی

```sh
cd galene
CGO_ENABLED=0 go build -ldflags='-s -w'
mkdir -p groups data
cat > data/config.json <<'EOF'
{
    "writableGroups": true,
    "users": {
        "admin": {"password": {"type": "plain", "key": "CHANGE_ME"},
                  "permissions": "admin"}
    }
}
EOF
./galene
```

> **`writableGroups: true` ضروری است** — بدون آن، ساخت کلاس و مدیریت
> کاربران از پنل مدیریت و از API وب‌سایت کار نمی‌کند.

## اتصال به سیستم مدیریت مدرسه

کلاس‌ها و اعضا از پایگاه دادهٔ سیستم مدیریت مدرسه خوانده می‌شوند —
**بدون هیچ تغییری در آن سیستم**. کلاس سرور فقط یک فایل JSON می‌خواند.

```
mtagbot (MySQL)  →  export-school.php  →  data/school-export.json  →  Galene
      فقط خوانده می‌شود          روی همین سرور              گروه‌های class-{id}
```

احراز هویت پس از همگام‌سازی **کاملاً محلی** انجام می‌شود، بنابراین کلاس
حتی در صورت قطع بودن پایگاه دادهٔ مدرسه هم برقرار می‌ماند.

### نگاشت

| سیستم مدیریت مدرسه | کلاس مجازی |
|---|---|
| `classes.id` | نام گروه: `class-{id}` — با تغییر نام کلاس تغییر نمی‌کند |
| `classes.name` | نام نمایشی (مثلاً «101 ریاضی») |
| `students.national_id` | نام کاربری |
| `students.serial_number` (۶ رقم) | رمز عبور |
| `students.first_name + last_name` | نام نمایش داده‌شده در کلاس و چت |
| `teachers.personnel_code` | رمز عبور دبیر |
| نقش `student` / `teacher` | مجوزهای کلاس |

فقط کلاس‌های **سال تحصیلی پیش‌فرض** همگام می‌شوند.

### راه‌اندازی

```sh
# ۱. خروجی‌گیر را کنار پایگاه مدرسه اجرا کنید
DB_HOST=127.0.0.1 DB_NAME=school DB_USER=root DB_PASS=SECRET \
  php /opt/bacirat/export-school.php > /opt/bacirat/data/school-export.json

# ۲. همگام‌سازی خودکار را فعال کنید
cat > /opt/bacirat/data/school.json <<'EOF'
{
  "exportCommand": "php /opt/bacirat/export-school.php > /opt/bacirat/data/school-export.json",
  "syncInterval": "1h",
  "bcryptCost": 8
}
EOF

systemctl restart bacirat
```

سپس در پنل مدیریت، تب **School sync** وضعیت را نشان می‌دهد و دکمهٔ
«Sync now» همگام‌سازی را فوری اجرا می‌کند.

قالب فایل خروجی در همان تب نمایش داده می‌شود. نکته‌ها:

- رمز فقط زمانی دوباره هش می‌شود که واقعاً تغییر کرده باشد؛ بنابراین
  همگام‌سازی‌های بعدی روی سرورهای ضعیف تقریباً بدون هزینه است.
- دانش‌آموزی که از کلاس خارج شود، در همگام‌سازی بعدی حذف می‌شود؛
  کاربران `op` که دستی ساخته شده‌اند هرگز حذف نمی‌شوند.
- برای فعال بودن همگام‌سازی، `writableGroups: true` در
  `data/config.json` ضروری است.

## نقش‌ها (Roles)

دو نقش جدید به نقش‌های Galene اضافه شده است:

| نقش | مجوزها | مناسب برای |
|-----|--------|-----------|
| `teacher` | `present`, `message`, `caption`, `whiteboard`, `token`, `moderate` | دبیر |
| `student` | `message` | دانش‌آموز |

نقش‌های اصلی Galene (`op`, `present`, `message`, `observe`, `caption`, `admin`)
بدون تغییر باقی مانده‌اند.

دبیر در کلاس می‌تواند:

- روی تختهٔ سفید بنویسد و آن را پاک کند
- اجازهٔ صحبت به دانش‌آموز بدهد یا بگیرد (`present` / `unpresent`)
- میکروفون دانش‌آموز را ببندد (`mute`)
- دانش‌آموز را از کلاس اخراج کند (`kick`)
- چت کلاس را پاک کند (`clearchat`)
- لینک دعوت (token) صادر کند

ارتقای کاربر به `op` و مشاهدهٔ IP کاربران (`identify`) همچنان فقط در
اختیار `op` است.

نمونهٔ تعریف کلاس در `groups/math-101.json`:

```json
{
    "displayName": "ریاضی ۱۰۱",
    "public": true,
    "max-clients": 40,
    "users": {
        "teacher": {"password": "SECRET", "permissions": "teacher"},
        "alice":   {"password": "SECRET", "permissions": "student"},
        "bob":     {"password": "SECRET", "permissions": "student"}
    }
}
```

## پنل مدیریت

آدرس: `/admin.html` — با نام کاربری و رمز ادمین سراسری وارد می‌شود.

بخش‌ها:

- **Classes** — ساخت/حذف کلاس، تنظیمات کلاس، مدیریت کاربران و نقش‌ها
- **Attendance** — حضور و غیاب هر کلاس در هر روز + خروجی CSV
- **Whiteboards** — مشاهدهٔ تعداد خطوط و پاک‌کردن تخته
- **Branding** — نام سایت، شعار، لوگو، favicon و رنگ اصلی
- **Website API** — مستندات اتصال وب‌سایت

## حضور و غیاب

هر ورود و خروج کاربران (به‌جز ضبط‌کننده) در
`data/attendance/{group}/{YYYY-MM-DD}.jsonl` ثبت می‌شود — یک خط JSON به
ازای هر رویداد. نوشتن به‌صورت **ناهمزمان** انجام می‌شود تا هیچ‌گاه قفل
کلاس را منتظر دیسک نگذارد.

نمایش در پنل مدیریت و از طریق API:

```
GET /galene-api/v0/.attendance/math-101?date=2026-11-16
```

## تختهٔ سفید

بدون هیچ وابستگی اضافه: خطوط از کانال پیام‌های Galene عبور می‌کنند.

- مختصات نرمال‌شده (۰ تا ۱) — روی هر اندازهٔ صفحه‌ای درست کار می‌کند
- ارسال نقاط به‌صورت دسته‌ای (حدود ۳۰ بار در ثانیه) — حداقل ترافیک
- فقط نقاط جدید فرستاده می‌شود، نه کل تصویر
- وضعیت کامل تخته هنگام ورودِ هر کاربر تازه برایش ارسال می‌شود
- سقف ۵۰۰ خط برای هر کلاس (جلوگیری از مصرف بی‌رویهٔ حافظه)

تنها دارندهٔ مجوز `whiteboard` (یعنی دبیر) می‌تواند بنویسد؛ بقیه فقط
می‌بینند.

## اتصال به وب‌سایت

یک API ساده برای همگام‌سازی کلاس‌ها و دانش‌آموزان از سمت وب‌سایت:

```
GET  /api/v1/groups
     ->  ["math-101", "physics-202"]

POST /api/v1/groups/{name}/sync
Content-Type: application/json

{
  "displayName": "ریاضی ۱۰۱",
  "description": "کلاس صبح دوشنبه",
  "users": [
    {"username": "teacher", "password": "SECRET", "permissions": "teacher"},
    {"username": "alice",   "password": "SECRET", "permissions": "student"}
  ]
}
```

احراز هویت: Basic با نام کاربری/رمز ادمین سراسری، یا توکن Bearer.

همچنین کلاس‌ها و حضور و غیاب از API مدیریت قابل دریافت است:

```
GET /galene-api/v0/.groups/
GET /galene-api/v0/.attendance/{group}[?date=YYYY-MM-DD]
GET /galene-api/v0/.attendance/{group}/.dates
GET /galene-api/v0/.whiteboard/{group}
DELETE /galene-api/v0/.whiteboard/{group}
GET|PUT /galene-api/v0/.branding
```

### ورود کاربران از طریق وب‌سایت

برای اینکه کاربر مستقیماً از سایت وارد کلاس شود، وب‌سایت می‌تواند توکن
JWT صادر کند (سرور احراز هویت Galene) یا از مسیر
`?token=...` استفاده کند؛ مستندات کامل در
[`galene/galene-api.md`](galene/galene-api.md) و
[`galene/galene.md`](galene/galene.md).

## برندینگ

از پنل مدیریت قابل تنظیم است و در `data/branding.json` ذخیره می‌شود.
تغییرات بدون نیاز به بیلد مجدد اعمال می‌شوند.

> نکته: به دلیل سیاست امنیتی محتوا (CSP)، نشانی لوگو و favicon باید از
> همان سرور باشد (مثلاً `/logo.png` داخل پوشهٔ `static/`).

## مصرف منابع

تغییرات با هدف اجرا روی ضعیف‌ترین سرورها انجام شده‌اند:

- باینری تک‌فایل Go، بدون پایگاه داده و بدون نیاز به Node یا بیلد فرانت‌اند
- ثبت حضور و غیاب ناهمزمان و append-only — بدون قفل و بدون بازخوانی فایل
- تختهٔ سفید فقط نقاط جدید را می‌فرستد و در سمت سرور محدود به ۵۰۰ خط است
- پنل مدیریت و تختهٔ سفید با JavaScript خالص و بدون هیچ فریم‌ورک

## همگام‌سازی با نسخهٔ اصلی (Upstream)

- Upstream: https://github.com/jech/galene
- وب‌سایت رسمی: https://galene.org
- لایسنس: MIT (فایل `galene/LICENCE`)

سفارشی‌سازی‌ها تا حد امکان در فایل‌های جدید نگه داشته شده‌اند تا اعمال
تغییرات بالادست آسان بماند:

| فایل جدید | محتوا |
|-----------|-------|
| `galene/group/attendance.go` | سیستم حضور و غیاب |
| `galene/group/whiteboard.go` | وضعیت تختهٔ سفید |
| `galene/webserver/admin.go` | API حضور و غیاب، تخته، وب‌سایت |
| `galene/webserver/branding.go` | تنظیمات ظاهری |
| `galene/static/whiteboard.js|css` | تختهٔ سفید در مرورگر |
| `galene/static/admin.html|js|css` | پنل مدیریت |
| `galene/static/brand.js|css` | اعمال برندینگ |

فایل‌های تغییرکرده از بالادست: `group/description.go` (نقش‌ها)،
`group/group.go`، `rtpconn/webclient.go`، `webserver/api.go`،
`webserver/webserver.go` و چند فایل در `static/`.
