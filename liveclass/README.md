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
| **کتاب PDF روی تخته**: دبیر فایل را بارگذاری می‌کند؛ صفحه از DataChannel هماهنگ می‌شود؛ دیررسیده‌ها همان صفحه را می‌بینند | ✅ |
| **تغییر رمز از داخل اپ** (دبیر/مهمان/ادمین) + **مدیریت کاربران** در پنل ادمین (ساخت دبیر/مهمان، ریست رمز، حذف) | ✅ |
| **کاربر مهمان** با دسترسی دبیر روی کلاس‌های مشخص (منوی «🔐 دسترسی‌های کلاس»؛ مجوزدار مثل دبیر همان کلاس ولی فقط همان کلاس‌ها) | ✅ |
| **اختصاص کاربران به کلاس‌ها** (ادمین/مالک کلاس) — لیست و حذف دسترسی در همان منو | ✅ |
| **بکاپ کامل داده‌ها** از پنل ادمین: zip شامل `backup.json` (کل جداول: کاربران، کلاس‌ها، جلسات، حضور، دسترسی‌ها، تخته) + `liveclass.db` خام + `README.txt` | ✅ |
| **سه حالت کیفیت تصویر** (اقتصادی ۶۴۰×۳۶۰ / متعادل ۹۶۰×۵۴۰ / کیفیت بالا ۱۲۸۰×۷۲۰) + **فریم‌ریت ۱۵/۲۰/۲۴ در هر سه حالت** — تنظیم‌شده‌ی هر کاربر، برای جلسات بعدی اعمال می‌شود | ✅ |
| **ضبط جلسه** (دکمه‌ی ⏺ در اتاق) — با LiveKit Egress؛ اختیاری، پیش‌فرض خاموش | ✅* |
| **نصب یک‌دستوری** (`install-liveclass.sh`) — بدون نیاز به node روی سرور (dist داخل ریپو است) | ✅ |

\* ضبط فقط وقتی فعال است که LiveKit Egress نصب و `LIVECLASS_EGRESS_ENABLE=1` تنظیم شده باشد (پایین‌تر، بخش «ضبط جلسه»). بدون آن، دکمه‌ی ضبط خطای واضح می‌دهد و بقیه‌ی اپ بی‌اثر نیست.

## ساختار

```
liveclass/
├── server/            # FastAPI — همه‌ی APIها + سرو استاتیک اپ
│   ├── app/main.py    # مسیرها (auth, classes, sessions, brand, import, attendance, board, pdf, record, users, backup)
│   ├── app/pdf_store.py  # ذخیره/کش PDF کتاب با سقف ۲۵MB (data/pdf/)
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

**دانلود مستقیم پکیج نصب از ریپوی GitHub** (نیازی به git روی سرور نیست):
```bash
curl -fL -o liveclass-install.zip https://raw.githubusercontent.com/Maxess/bacirat-function/main/liveclass-install.zip
unzip -o liveclass-install.zip && cd liveclass
sudo DOMAIN=class.example.com LIVEKIT_DOMAIN=livekit.class.example.com ./scripts/install-liveclass.sh
```
> تا قبل از merge شدن این شاخه، به‌جای `main` نام شاخه‌ی نگه‌داری را بگذارید.

یا با git:
```bash
git clone https://github.com/Maxess/bacirat-function.git ~/bacirat-function && cd ~/bacirat-function/liveclass
sudo DOMAIN=class.example.com LIVEKIT_DOMAIN=livekit.class.example.com ./scripts/install-liveclass.sh
```

همین. اسکریپت همه‌چیز را انجام می‌دهد:

1. وابستگی‌های سیستمی (python3-venv, nginx, ufw, …) — **بدون نیاز به node روی سرور** (خروجی build داخل ریپو است)
2. کپی به `/opt/liveclass` + ساخت venv و نصب requirements
3. ساخت ادمین اولیه (رمز را از شما می‌پرسد؛ در حالت غیرفعال رمز تصادفی می‌سازد و نشان می‌دهد)
4. نصب **LiveKit Server** از GitHub Releases + ساخت کلید واقعی + سرویس systemd
5. نوشتن `/etc/liveclass/liveclass.env` (سِکرت‌ها تصادفی)، سرویس `liveclass.service`، nginx و گواهی **Let's Encrypt خودکار**
6. تنظیم فایروال (۸۰/۴۴۳/۷۸۸۱ + بازه‌ی UDP مدیا)

### HTTPS خودکار (معادل auto_ssl پرشین‌مییت)

`scripts/auto-ssl.sh` کل چرخه‌ی HTTPS را خودکار و idempotent انجام می‌دهد: چالش ACME (webroot) ← صدور گواهی Let's Encrypt برای `DOMAIN` و `livekit.DOMAIN` ← پیکربندی nginx با HTTP→HTTPS، HSTS، WebSocket برای LiveKit ← فعال‌سازی **تمدید خودکار** (`certbot.timer` + deploy-hook ری‌لود nginx) ← تنظیم `LIVECLASS_SECURE_COOKIES=1` و `LIVECLASS_LIVEKIT_PUBLIC_URL=wss://…` در `liveclass.env` و ری‌استارت سرویس.

```bash
sudo DOMAIN=class.example.com ./scripts/auto-ssl.sh                 # صدور/تکمیل HTTPS
sudo LETSENCRYPT_EMAIL=me@example.com DOMAIN=class.example.com ./scripts/auto-ssl.sh
sudo STAGED=1 DOMAIN=class.example.com ./scripts/auto-ssl.sh        # تست با staging (بدون مصرف سهمیه)
```

پیش‌نیاز: رکوردهای DNS (A) هر دو دامنه به سرور اشاره کنند و پورت‌های ۸۰/۴۴۳ باز باشند. نصب یک‌دستوری (`install-liveclass.sh`) در صورت دادن `--domain` خودش این را صدا می‌زند.

بدون دامنه (فقط تست محلی/شبکه‌ی داخلی): `sudo ./scripts/install-liveclass.sh`
هشدار: **دوربین و میکروفون فقط در HTTPS یا localhost کار می‌کنند**؛ برای استفاده‌ی واقعی حتماً دامنه بدهید یا بعد از نصب auto-ssl را اجرا کنید.

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
| `LIVECLASS_EGRESS_ENABLE` | خاموش | `1` = ضبط جلسه فعال است (نیازمند Egress؛ پایین) |
| `LIVECLASS_RECORDINGS_DIR` | `server/data/recordings/` | پوشه‌ی ذخیره‌ی فایل‌های MP4 ضبط |
| `LIVECLASS_PDF_DIR` | `server/data/pdf/` | پوشه‌ی کتاب‌های PDF بارگذاری‌شده |
| `LIVECLASS_MAX_PDF_MB` | `25` | سقف حجم PDF |
| `LIVECLASS_SECURE_COOKIES` | خاموش | `1` = کوکی سِشن فقط روی HTTPS (با اجرای auto-ssl خودکار می‌شود) |

## ضبط جلسه (اختیاری — پیش‌فرض خاموش)

ضبط از طریق **LiveKit Egress** انجام می‌شود: یک کانتینر داکر که Chrome + GStreamer داخلی دارد و کل اتاق را به MP4 تبدیل می‌کند. این قابلیت **سنگین** است؛ روی سرور ضعیف توصیه نمی‌شود.

```bash
docker --version || { echo 'اول داکر نصب کنید'; exit 1; }
sudo ./scripts/install-livekit.sh --with-egress
```

اسکریپت این کارها را می‌کند: نصب `redis-server`، اضافه‌کردن `redis` به `livekit.yaml` و ری‌استارت LiveKit، ساخت `/etc/liveclass/egress.yaml`، pull ایمیج `livekit/egress`، اجرای کانتینر با `--network host` (تا redis فقط روی لوپ‌بک بماند) و نوشتن `recordings.env` با دو خط لازم.

سپس محتوای `/etc/liveclass/recordings.env` را به `/etc/liveclass/liveclass.env` اضافه و `systemctl restart liveclass` کنید. بعد از آن دکمه‌ی ⏺ در اتاق روشن می‌شود؛ فایل ضبط در همان صفحه‌ی کلاس («خروجی» جلسه‌ی گذشته) با مجوز دبیر قابل دانلود است.

هشدارهای صادقانه:
- مسیر ضبط در هر دو سو (میزبان و کانتینر) یکی است (`/opt/liveclass/recordings`)؛ `filepath` درخواست ضبط داخل کانتینر ترجمه نمی‌شود.
- بدون داکر یا بدون `redis` یا بدون `LIVECLASS_EGRESS_ENABLE=1`، دکمه‌ی ضبط خطای ۵۰۳ می‌دهد و هیچ‌چیز خراب نمی‌شود.
- پوشه‌ی recordings با دسترسی 0777 ساخته می‌شود (egress غیر root می‌نویسد)؛ برای سخت‌گیری آن را با کاربر کانتینر هم‌مالک کنید.
- این مسیر در محیط تست ما (بدون داکر) فقط به‌صورت mock آزمایش شده؛ روی سرور واقعی حتماً یک جلسه‌ی کوتاه را ضبط و دانلود کنید.

## نکات امنیتی

- HTTPS و secure cookie: بعد از اجرای `auto-ssl.sh`، کوکی سِشن با پرچم `Secure` ست می‌شود؛ روی HTTP فقط برای تست محلی کار می‌کند.
- `admin` اولیه را با رمز قوی بسازید؛ سپس از «⚙️ تنظیمات › تغییر رمز» رمز را عوض کنید.
- `livekit.yaml` و `egress.yaml` شامل سِکرت‌هاست — `chmod 600`.
- توکن ورود دانش‌آموزها تصادفی ۲۴ بایتی است؛ لینک‌ها را خصوصی نگه دارید.
- کوکی سِشن `HttpOnly; SameSite=Lax` است؛ پشت HTTPS حتماً `Secure` اضافه کنید (با nginx/TLS).
- بکاپ (`/api/admin/backup`) شامل همه‌ی اطلاعات است — فایل zip را مثل رمز عبور حفظ کنید.

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

1. گزارش‌های کلاسی و نمودار پیشرفت
2. PWA نصبی (manifest + service worker)
3. امضای لینک دانلود ضبط برای دانش‌آموزان (فعلاً فقط دبیر/مالک دانلود می‌کند)
4. بکاپ زمان‌بندی‌شده‌ی خودکار (cron) علاوه بر دانلود دستی

## مجوز/مالکیت

کد این پوشه متعلق به پروژه‌ی شماست (همان مالکیت ریپوی `bacirat-function`). LiveKit تحت Apache-2.0 است.
