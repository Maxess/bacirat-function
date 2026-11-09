# LiveClass — پلتفرم کلاس آنلاین کاملاً اختصاصی شما

برنامه‌ای که **از صفر و برای شما** ساخته شده، روی **LiveKit** (SFU اپن‌سورس، Apache-2.0 —
همان موتوری که محصول قبلی استفاده می‌کرد) + **FastAPI/SQLite** (بک‌اند) + **Vue 3** (فرانت).
هیچ وابستگی به باینری یا کد فروشنده ندارد؛ همه‌چیز در همین ریپو است و قابل تغییر بی‌پایان.

## امکانات (MVP فعلی — تست‌شده)

| امکان | وضعیت |
|---|---|
| ورود دبیر/مدیر با حساب (pbkdf2 + کوکی امن) | ✅ |
| تعریف کلاس توسط دبیر/ادمین | ✅ |
| ثبت دانش‌آموز هر کلاس + **ایمپورت CSV** (دبیر/کلاس/دانش‌آموز، سرستون فارسی) | ✅ |
| **ورود دانش‌آموز فقط با لینک اختصاصی کلاس** (بدون رمز) | ✅ |
| **شروع کلاس با دبیر** → ساخت اتاق LiveKit؛ **بستن با دبیر** → حذف اتاق و خروج همه | ✅ |
| ویدیو/صدا، **اشتراک صفحه**، چت زنده در اتاق (LiveKit DataChannel) | ✅ |
| **تخته تعاملی مشترک** (قلم/پاک‌کن/رنگ/ضخامت) — لحظه‌ای با DataChannel و ماندگار در SQLite؛ دیررسیده‌ها تخته را کامل می‌بینند | ✅ |
| **چیدمان استیج کلاس**: گرید ویدیو با هایلایت گوینده + حالت تمام‌استیج تخته و اشتراک صفحه + پنل کناری «حضار / گفت‌وگو» | ✅ |
| **بالا بردن دست** (✋) با نشان در فهرست حضار و پیام سیستمی | ✅ |
| **سقف ناشران قابل تنظیم** (پیش‌فرض ۸) — بقیه‌ی دانش‌آموزان بیننده‌ی کم‌مصرف می‌شوند (گیم‌چنجر برای سرور ضعیف) | ✅ |
| **اخراج تک‌تک دانش‌آموز** توسط دبیر از داخل اتاق | ✅ |
| **حضور و غیاب دقیق با مدت حضور** (heartbeat + بستن جلسه) + خروجی CSV | ✅ |
| حضور زنده‌ی کلاس (جدول آنلاین/خارج‌شده، به‌روزرسانی خودکار) | ✅ |
| **برند پویا بومی**: نام/تگ‌لاین/رنگ + **آپلود لوگو و آیکون** در پنل ادمین — بدون هیچ وصله‌ای | ✅ |
| فونت وزیر و همه‌چیز **کاملاً محلی و آفلاین** (بدون CDN یا Google Fonts) | ✅ |
| یک سرویس واحد روی یک پورت (۸۲۰۰) — API + اپ | ✅ |
| **نصب یک‌دستوری** (`install-liveclass.sh`) — بدون نیاز به node روی سرور (dist داخل ریپو است) | ✅ |

## ساختار

```
liveclass/
├── server/            # FastAPI — همه‌ی APIها + سرو استاتیک اپ
│   ├── app/main.py    # مسیرها (auth, classes, sessions, brand, import, attendance, board)
│   ├── app/livekit_hub.py  # توکن LiveKit + مدیریت room (+ حالت mock برای تست)
│   ├── app/brand.py   # تنظیمات برند در دیتابیس + آپلود فایل
│   └── tests/test_smoke.py
├── web/               # Vue 3 + Vite + livekit-client  (خروجی build در web/dist و در git است)
│   └── src/components/{VideoTile,Board}.vue + views/Room.vue (استیج کلاس)
├── scripts/
│   ├── install-liveclass.sh   # نصب یک‌دستوری (سرور ضعیف کافی است؛ node لازم نیست)
│   ├── update-liveclass.sh    # به‌روزرسانی با بکاپ خودکار قبل از هر آپدیت
│   ├── backup-liveclass.sh    # بکاپ دیتابیس و تنظیمات
│   ├── uninstall-liveclass.sh # حذف کامل (با بکاپ خودکار)
│   └── install-livekit.sh     # نصب livekit-server از GitHub Releases + systemd
├── liveclass.service  # یونیت systemd بک‌اند
├── nginx.liveclass.conf
└── docker-compose.yml # (اختیاری) فقط برای livekit
```

## اجرای سریع (توسعه، همین دستگاه)

```bash
cd liveclass
python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt
.venv/bin/python server/run.py init-admin --username admin --password 'رمز-قوی'
cd web && npm install && npm run build && cd ..     # یا npm run dev روی ۸۲۰۱ با proxy
LIVECLASS_LIVEKIT_MOCK=1 .venv/bin/python server/run.py serve --port 8200
# http://127.0.0.1:8200  —  با LIVECLASS_LIVEKIT_MOCK=1 اتاق‌ها واقعی نیستند؛ فقط برای دیدن جریان‌هاست
```

برای ویدئوی واقعی، LiveKit را اجرا کنید (پایین) و بدون `LIVECLASS_LIVEKIT_MOCK` بسازید.

## نصب روی سرور — یک دستور (مثل نصب PersianMeet)

```bash
git clone <ریپو> ~/bacirat-function && cd ~/bacirat-function/liveclass
sudo DOMAIN=class.example.com LIVEKIT_DOMAIN=livekit.class.example.com ./scripts/install-liveclass.sh
```

همین. اسکریپت همه‌چیز را انجام می‌دهد:

1. وابستگی‌های سیستمی (python3-venv, nginx, ufw, …) — **بدون نیاز به node روی سرور** (خروجی build داخل ریپو است)
2. کپی به `/opt/liveclass` + ساخت venv و نصب requirements
3. ساخت ادمین اولیه (رمز را از شما می‌پرسد؛ در حالت غیرفعال رمز تصادفی می‌سازد و نشان می‌دهد)
4. نصب **LiveKit Server** از GitHub Releases + ساخت کلید واقعی + سرویس systemd
5. نوشتن `/etc/liveclass/liveclass.env` (سِکرت‌ها تصادفی)، سرویس `liveclass.service`، nginx و گواهی **Let's Encrypt خودکار**
6. تنظیم فایروال (۸۰/۴۴۳/۷۸۸۱ + بازه‌ی UDP مدیا)

بدون دامنه (فقط تست محلی/شبکه‌ی داخلی): `sudo ./scripts/install-liveclass.sh`
هشدار: **دوربین و میکروفون فقط در HTTPS یا localhost کار می‌کنند**؛ برای استفاده‌ی واقعی حتماً `--domain` بدهید.

به‌روزرسانی: `sudo ./scripts/update-liveclass.sh` (بکاپ خودکار قبل از آپدیت)
بکاپ: `sudo ./scripts/backup-liveclass.sh` — حذف کامل: `sudo ./scripts/uninstall-liveclass.sh`

## متغیرهای محیطی بک‌اند

| متغیر | پیش‌فرض | کاربرد |
|---|---|---|
| `LIVECLASS_DB` | `server/data/liveclass.db` | مسیر SQLite |
| `LIVECLASS_SECRET` | خودکار (فایل کنار دیتابیس) | کلید امضای کوکی |
| `LIVECLASS_LIVEKIT_URL` | `ws://127.0.0.1:7880` | آدرس داخلی LiveKit (برای API) |
| `LIVECLASS_LIVEKIT_PUBLIC_URL` | همان | آدرسی که **مرورگرها** با آن وصل می‌شوند |
| `LIVECLASS_LIVEKIT_KEY` / `LIVECLASS_LIVEKIT_SECRET` | `devkey` / کلید توسعه | کلیدهای LiveKit — در تولید حتماً عوض کنید |
| `LIVECLASS_LIVEKIT_MOCK` | خاموش | `1` = اتاق‌های ساختگی (فقط برای تست آفلاین) |
| `LIVECLASS_MAX_PUBLISHERS` | `8` | چند دانش‌آموز اولِ هر جلسه دوربین/میکروفون بگیرند؛ بقیه بیننده می‌مانند |
| `LIVECLASS_ROOM_MAX_PARTICIPANTS` | `200` | سقف نفرات اتاق در LiveKit |

## نکات امنیتی

- `admin` اولیه را با رمز قوی بسازید و بعد از اولین ورود… (آینده: تغییر رمز از داخل اپ).
- `livekit.yaml` شامل سِکرت‌هاست — `chmod 600`.
- توکن ورود دانش‌آموزها تصادفی ۲۴ بایتی است؛ لینک‌ها را خصوصی نگه دارید.
- کوکی سِشن `HttpOnly; SameSite=Lax` است؛ پشت HTTPS حتماً `Secure` اضافه کنید (با nginx/TLS).

## بهینه‌سازی برای سرور ضعیف (این‌ها را رعایت کردیم)

- **SQLite در حالت WAL** با `synchronous=NORMAL` و کش ۸ مگابایتی + `busy_timeout` — بدون قفل‌شدن در همزمانی معمولی.
- **GZip** روی همه‌ی پاسخ‌ها + **کش یک‌ساله برای فایل‌های static با هش** + `index.html` بدون کش.
- **فونت وزیر محلی** — هیچ درخواستی به CDN/گوگل فونت نمی‌رود (هم سریع‌تر است هم «بدون فیلتر» کار می‌کند).
- **ویدیوی دوربین ۶۴۰×۳۶۰@۱۵fps به‌صورت پیش‌فرض + Simulcast + Adaptive Stream + Dynacast** — پهنای باند و CPU به‌شدت پایین می‌آید.
- **سقف ناشران** (`LIVECLASS_MAX_PUBLISHERS`، پیش‌فرض ۸): جلسات ۲۰۰ نفره با ۸ ناشر به‌راحتی روی سرور ضعیف اجرا می‌شوند؛ بقیه دیداری‌اند ولی چت و تخته و دست‌بلندکردن دارند.
- **تخته به‌جای بیت‌مپ** برداری (stroke) تبادل می‌شود — هر ضربه قلم چند بایت؛ ذخیره در SQLite برای دیررسیده‌ها.
- **تک‌پروسه‌ی uvicorn** کافی است (بار واقعی روی LiveKit است که خودش چند thread است).

پیشنهاد سخت‌افزاری تقریبی (LiveKit را روی همان سرور اجرا کنید):
| سناریو | حداقل RAM | توضیح |
|---|---|---|
| تا ۳۰ نفر با ۴–۶ ناشر | ۱–۲ GB | 1 vCPU کافی است |
| تا ۸۰ نفر با ۸ ناشر | ۲–۴ GB | 2 vCPU |
| تا ۲۰۰ نفر با ۸ ناشر | ۴–۸ GB | 2–4 vCPU؛ حتماً TURN/بازه‌ی UDP باز |

## نقشه‌ی راه

1. **کتاب PDF روی تخته** (pdf.js سمت کلاینت — بدون بار روی سرور)
2. تغییر رمز از پنل + مدیریت کاربران/دبیران از داخل اپ
3. ضبط جلسه (LiveKit Egress) — اختیاری، نیازمند فضای دیسک
4. گزارش‌های کلاسی و نمودار پیشرفت
5. PWA نصبی (manifest + service worker)

## مجوز/مالکیت

کد این پوشه متعلق به پروژه‌ی شماست (همان مالکیت ریپوی `bacirat-function`). LiveKit تحت Apache-2.0 است.
