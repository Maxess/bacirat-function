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
| **اخراج تک‌تک دانش‌آموز** توسط دبیر از داخل اتاق | ✅ |
| **حضور و غیاب دقیق با مدت حضور** (heartbeat + وب‌هوک بسته‌شدن) + خروجی CSV | ✅ |
| حضور زنده‌ی کلاس (جدول آنلاین/خارج‌شده، به‌روزرسانی خودکار) | ✅ |
| **برند پویا بومی**: نام/تگ‌لاین/رنگ + **آپلود لوگو و آیکون** در پنل ادمین — بدون هیچ وصله‌ای | ✅ |
| یک سرویس واحد روی یک پورت (۸۲۰۰) — API + اپ | ✅ |

## ساختار

```
liveclass/
├── server/            # FastAPI — همه‌ی APIها + سرو استاتیک اپ
│   ├── app/main.py    # مسیرها (auth, classes, sessions, brand, import, attendance)
│   ├── app/livekit_hub.py  # توکن LiveKit + مدیریت room (+ حالت mock برای تست)
│   ├── app/brand.py   # تنظیمات برند در دیتابیس + آپلود فایل
│   └── tests/test_smoke.py
├── web/               # Vue 3 + Vite + livekit-client
├── scripts/install-livekit.sh   # نصب livekit-server از GitHub Releases + systemd
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

## استقرار روی سرور (Ubuntu 22.04/24.04) — گام‌به‌گام

```bash
git clone <ریپو> /opt/bacirat-function && cd /opt/bacirat-function/liveclass
python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt
.venv/bin/python server/run.py init-admin --username admin --password 'رمز-قوی' --full-name 'مدیر'
cd web && npm install && npm run build && cd ..
sudo chown -R www-data:www-data /opt/bacirat-function/liveclass   # سرویس با کاربر www-data اجرا می‌شود

# ۱) LiveKit از GitHub Releases نصب می‌شود (همان نسخه‌ای که محصول قبلی استفاده می‌کرد)
sudo ./scripts/install-livekit.sh          # کلید واقعی می‌سازد و سرویسش را بالا می‌آورد
# مقادیر چاپ‌شده API_KEY/API_SECRET را در /etc/liveclass/liveclass.env بنویسید:
sudo tee /etc/liveclass/liveclass.env >/dev/null <<ENVEOF
LIVECLASS_SECRET=$(head -c 32 /dev/urandom | base64 | tr -dc a-zA-Z0-9 | head -c 40)
LIVECLASS_LIVEKIT_URL=ws://127.0.0.1:7880
LIVECLASS_LIVEKIT_PUBLIC_URL=wss://livekit.example.com   # ساب‌دامنه‌ای که مرورگرها با آن به LiveKit وصل می‌شوند
LIVECLASS_LIVEKIT_KEY=<کلید از نصب‌کننده>
LIVECLASS_LIVEKIT_SECRET=<سِکرت از نصب‌کننده>
ENVEOF
sudo cp liveclass.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now liveclass
# اگر livekit.yaml را ویرایش کردید: sudo systemctl restart livekit
```

- nginx: از `nginx.liveclass.conf` استفاده کنید (دو server-block: اپ روی `class.example.com`، سیگنالینگ LiveKit روی `livekit.example.com`).
- فایروال: `sudo ufw allow 443/tcp && sudo ufw allow 7881/tcp && sudo ufw allow 50000:60000/udp`
- ادمین اول: داخل اپ با `admin` وارد شوید → «تنظیمات برند» + «ایمپورت CSV».

## متغیرهای محیطی بک‌اند

| متغیر | پیش‌فرض | کاربرد |
|---|---|---|
| `LIVECLASS_DB` | `server/data/liveclass.db` | مسیر SQLite |
| `LIVECLASS_SECRET` | خودکار (فایل کنار دیتابیس) | کلید امضای کوکی |
| `LIVECLASS_LIVEKIT_URL` | `ws://127.0.0.1:7880` | آدرس داخلی LiveKit (برای API) |
| `LIVECLASS_LIVEKIT_PUBLIC_URL` | همان | آدرسی که **مرورگرها** با آن وصل می‌شوند |
| `LIVECLASS_LIVEKIT_KEY` / `LIVECLASS_LIVEKIT_SECRET` | `devkey` / کلید توسعه | کلیدهای LiveKit — در تولید حتماً عوض کنید |
| `LIVECLASS_LIVEKIT_MOCK` | خاموش | `1` = اتاق‌های ساختگی (فقط برای تست آفلاین) |

## نکات امنیتی

- `admin` اولیه را با رمز قوی بسازید و بعد از اولین ورود… (آینده: تغییر رمز از داخل اپ).
- `livekit.yaml` شامل سِکرت‌هاست — `chmod 600`.
- توکن ورود دانش‌آموزها تصادفی ۲۴ بایتی است؛ لینک‌ها را خصوصی نگه دارید.
- کوکی سِشن `HttpOnly; SameSite=Lax` است؛ پشت HTTPS حتماً `Secure` اضافه کنید (با nginx/TLS).

## نقشه‌ی راه

1. **تخته تعاملی + کتاب PDF روی برد** (Excalidraw/Konva + pdf.js — دو طرفه از طریق DataChannel)
2. تغییر رمز از پنل + مدیریت کاربران/دبیران از داخل اپ
3. بکاپ خودکار SQLite (مثل `deploy/backup.sh`)
4. گزارش‌های کلاسی و نمودار پیشرفت
5. PWA نصبی (manifest + service worker)

## مجوز/مالکیت

کد این پوشه متعلق به پروژه‌ی شماست (همان مالکیت ریپوی `bacirat-function`). LiveKit تحت Apache-2.0 است.
