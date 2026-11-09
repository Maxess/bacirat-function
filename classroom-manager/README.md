# ماژول مدیریت کلاس (classroom-manager)

لایه‌ی مدیریتی مستقل برای PersianMeet — **بدون تغییر در سورس فروشنده** — که دقیقاً خواسته‌های شما
را پوشش می‌دهد:

| خواسته‌ی شما | وضعیت در این ماژول |
|---|---|
| تعریف دانش‌آموزان در هر کلاس | ✅ ثبت‌نام در کلاس (CSV یا پنل) |
| ایمپورت کاربران با فایل CSV | ✅ دبیران / کلاس‌ها / دانش‌آموزان (با گزارش خطای هر ردیف) |
| تغییر سیستم ورود دانش‌آموزان و دبیران | ✅ دبیر با حساب واقعی وارد می‌شود؛ دانش‌آموز **فقط با لینک اختصاصی کلاس** (بدون رمز) وارد می‌شود |
| کلاس با ورود دبیر فعال می‌شود | ✅ دکمه‌ی «شروع کلاس» دبیر → اتاق PersianMeet ساخته می‌شود و لینک‌های اختصاصی فعال می‌شوند |
| بستن کلاس توسط دبیر و خروج همه | ✅ «بستن کلاس» → ثبت خروج همه + توقف/حذف اتاق (`DeleteMeet`) → واقعاً همه بیرون می‌افتند |
| حضور و غیاب لحظه‌ای + مدت حضور | ✅ ورود واقعی از مسیر `/enter` ثبت می‌شود؛ مدت حضور تا خروج/بستن کلاس؛ گزارش زنده + خروجی CSV |

اتصال به PersianMeet فقط از طریق **API رسمی مستندشده** انجام می‌شود (`POST /api`) و همه‌ی
پیام‌های سیگنالینگ اتاق دست‌نخورده می‌مانند.

## معماری

```
دبیر ──► https://class.example.com/        (این سامانه — Python + SQLite، پورت ۸۰۹۰)
           │  شروع کلاس / بستن کلاس / گزارش حضور
           ▼
      PersianMeet (API رسمی: CreateMeet / Join / DeleteMeet)
           ▲
دانش‌آموز ─┘  لینک پایدار: https://class.example.com/enter?token=...
              → زمان ورود واقعی ثبت می‌شود → ریدایرکت به /user/<pid> داخل PersianMeet
```

## اجرا روی سرور (Ubuntu 22.04/24.04)

پیش‌نیاز: پایتون ۳٫۱۰+ (همان `python3` اوبونتو) — **هیچ pip install لازم نیست**.

```bash
# فرض: ریپو در /opt/bacirat-function کلون شده و PersianMeet در /opt/persianmeet نصب است
cd /opt/bacirat-function/classroom-manager
python3 run.py init-admin --username admin --password 'یک-رمز-قوی' --full-name 'مدیر آموزش'
python3 run.py serve --host 127.0.0.1 --port 8090     # یا با systemd: classroom-manager.service
```

سپس Nginx جلوی آن بگذارید (نمونه زیر) یا برای تست محلی مستقیم روی پورت ۸۰۹۰ باز کنید.

### env های مهم

| متغیر | پیش‌فرض | کاربرد |
|---|---|---|
| `PERSIANMEET_URL` | `http://127.0.0.1:8003` | **آدرس عمومی** PersianMeet که کاربران می‌بینند (مثلاً `https://class.example.com`) — لینک‌های ورود و ریدایرکت به همین می‌روند |
| `PERSIANMEET_SECRET` | خالی | همان `secret` داخل `config.yml` محصول (برای `DeleteMeet` هنگام «بستن کلاس») |
| `PM_MANAGER_CLOSE_STRATEGY` | `delete` | `delete` = حذف اتاق هنگام بستن (خروج واقعی همه) / `keep` = فقط بستن محلی |
| `PM_MANAGER_DB` | `./data/classroom_manager.db` | مسیر SQLite |
| `PM_MANAGER_SECRET` | auto | کلید امضای کوکی (خودکار ساخته می‌شود) |
| `PERSIANMEET_DIST_DIR` | `$PERSIANMEET_INSTALL_DIR/dist` (یا `/opt/persianmeet/dist`) | مسیر `dist` محصول — برای «برند پویا» (نوشتن `brand.json` و آیکون/لوگو) |
| `PERSIANMEET_INSTALL_DIR` | `/opt/persianmeet` | اگر `PERSIANMEET_DIST_DIR` تنظیم نشده باشد، از این ساخته می‌شود |
| `PM_PRODUCT_DB` | `$PERSIANMEET_INSTALL_DIR/test.db` | مسیر دیتابیس sqlite محصول — **فقط‌خواندنی** برای حضور دقیق (جدول `Connection`) |

### نمونه بلوک Nginx (روی همان دامنه‌ی کلاس)

```nginx
location /enter { proxy_pass http://127.0.0.1:8090; proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto $scheme; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; }
location /      { include /etc/nginx/pm-proxy.inc; }   # بقیه‌ی مسیرها به PersianMeet
```
یا برای یک زیردامنه‌ی جدا: `panel.class.example.com` → `127.0.0.1:8090` و `class.example.com` → PersianMeet.

### داده‌ی اولیه (CSV نمونه در `examples/`)

```bash
# محتوای teachers.csv:
#   username,password,full_name
# classes.csv:
#   class_title,teacher_username
# students.csv:
#   student_code,full_name,class_title
```
سپس از پنل ادمین → «ایمپورت CSV» هر سه فایل را آپلود کنید.

### systemd

```bash
sudo cp classroom-manager.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now classroom-manager
journalctl -u classroom-manager -f
```

## برند پویا (نام/لوگو/آیکون از پنل ادمین محصول)

این ماژول endpoint های `/brand-api/*` را هم سرو می‌کند:

- `GET /brand-api/config` — برگرداندن `brand.json` فعلی (عمومی)
- `GET /brand-api/admin-check` — بررسی ادمین‌بودن GUID محصول
- `POST /brand-api/save` — ذخیره‌ی برند + آپلود لوگو/آیکون (**فقط ادمین محصول**، احراز با GUID علیه خود محصول)

نصب پل برند در dist محصول و راهنمای کامل: [`../docs/13-branding.md`](../docs/13-branding.md).
nginx باید `location /brand-api/ → 127.0.0.1:8090` و `location /brand/ → PersianMeet (no-store)` داشته باشد
(نمونه در `../deploy/nginx.reverse-proxy.conf`).

## تست‌ها

```bash
python3 -m unittest discover -s classroom-manager/tests -v     # ۱۰ تست، بدون اینترنت
```

## محدودیت‌های فعلی (و چیزی که به سورس نیاز دارد)

- ⏱️ **مدت حضور — حالا دقیق است**: اگر دیتابیس محصول (`PM_PRODUCT_DB`) خوانده شود، بازه‌های واقعی
  WebSocket از جدول `Connection` محصول تجمیع می‌شود (اولین ورود / آخرین خروج / آنلاین-الآن) — چه حین
  کلاس و چه بعد از بستن («اسنپ‌شات» لحظه‌ی بستن، *قبل از* `DeleteMeet` گرفته می‌شود تا داده حتی اگر
  اتاق حذف شود حفظ شود). خروجی CSV ستون «منبع داده» (دقیق/تقریبی) دارد. اگر دیتابیس محصول در دسترس
  نباشد، رفتار به حالت تقریبی قبلی (از `entered_at` خودمان) عقب‌نشینی می‌کند — صادقانه در پنل نمایش داده می‌شود.
  نکته‌ی مهم: «دبیر هنوز داخل کلاس هست؟» هم روی صفحه‌ی نشست نمایش داده می‌شود.
- 🚫 اخراج **تک‌تک** دانش‌آموز (بدون بستن کلاس) با API رسمی ممکن نیست؛ باید در سورس یک پیام
  broadcast اضافه شود (یا حذف اتاق).
- 📚 کتاب PDF داخل تخته تعاملی → تغییر سورس (فرانت Pod «کتاب» + تبدیل صفحه با `mupdf-tools`).
