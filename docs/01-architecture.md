# معماری فنی PersianMeet (بر اساس مستندات رسمی و کد عمومی سازنده)

این سند برای این ساخته شده که قبل از «دست‌بردن» در سیستم بدانید داخل بسته چه خبر است.

## نمای کلی

```
                       ┌──────────────────────────── دامنه شما (class.example.com)
                       ▼
        ┌───────────────────────────────┐        ┌──────────────┐
        │   باینری persianmeet (تک‌فایلی)│        │  wwwroot     │
        │   - HTTP/HTTPS(8003/443)      │◄───────│  (HTML/CSS/JS│
        │   - REST API:  POST /api      │ استاتیک│   + لوگو)    │
        │   - WebSocket :  /wsroom      │        └──────────────┘
        │   - درگاه فایل‌ها /files/…    │
        │   - SQLite(؟) برای متادیتا    │
        └───────────────┬───────────────┘
                        │ WebRTC
        ┌───────────────▼───────────────┐
        │ SFU داخلی/یک‌پارچه (صدا و ویدئو) │  ~ UDP بازه‌های بزرگ
        └───────────────────────────────┘
                        ▲
              ┌─────────┴─────────┐
              │ مرورگر کاربران     │ Chrome / Firefox / Safari
              └───────────────────┘
```

## اجزای دقیقاً شناخته‌شده

| جزء | توضیح | منبع |
|---|---|---|
| باینری `persianmeet` | سورس عمومی نیست؛ تک‌فایلی کامپایل‌شده (صفحه‌ی اصلی doc می‌گوید «written in Go») | مستندات نصب |
| `config.yml` | پیکربندی اصلی: حداقل `auto_ssl` و `secret` قابل‌تنظیم‌اند؛ انتخاب اینکه کدام API ها secret بخواهند هم در همین فایل تعریف می‌شود | سند api/intro و deploy |
| `app/optimized` | فایل مکمل اجرایی (در نسخه‌های جدید `chmod +x` لازم دارد) | سند deploy_ubuntu |
| HTTP داخلی | پورت `8003` برای `http://localhost:8003` | سند installation |
| HTTPS عمومی | با `auto_ssl: true` و یک رکورد A/AAAA معتبر روی دامنه، گواهی Let's Encrypt خودکار صادر می‌شود (HTTP-01 → لازم است پورت ۸۰ از بیرون در دسترس باشد) | سند deploy |
| REST API | یک endpoint: `POST /api` با `Content-type: application/json` و بدنه‌ی `{Secret, Type, Data}` | سند api/intro |
| WebSocket سیگنالینگ | مسیر `/wsroom`، پیاده‌سازی شده روی **Centrifugo** با پیام‌های **protobuf** (توکن = همان PID خصوصی کاربر)؛ الگوی `meet-<id>` برای channel هر اتاق | سورس عمومی `sunmobiir/meet-react` → `src/service/signal.ts` و `protobufSingnal.ts` |
| SFU | در توزیع docker جداست (`alit771/sfu` روی ۷۰۰۰ + UDP 5000-5200؛ در توزیع تک‌باینری احتمالاً داخل همان باینری است) | docker-compose ریپوی `learn100ir/meetapp` |
| Collabora Online (`collabora/code`) | آفیس تحت وب مشترک (ویرایش Word/Excel/...) در حالت docker روی ۹۹۸۰ | همان docker-compose |
| Mosquitto | MQTT برای پیام‌رسانی/حضور (در حالت docker، پورت‌های ۱۸۸۳ و ۹۰۰۱) | همان docker-compose |
| Traefik | ریورس‌پراکسی + TLS در حالت docker (۸۰/۴۴۳/۸۰۸۰) | همان docker-compose |
| ذخیره‌ی فایل‌ها | پوشه‌ای کنار باینری/زیر `wwwroot/files` (در docker: `${dataPath} → /root/linux12/wwwroot/files/`) | همان docker-compose |
| ابزار PDF | `mupdf-tools` (استخراج تصویر از PDF برای وایت‌برد/آنالیز) | وابستگی مستندشده |

## جریان یک جلسه‌ی معمولی

1. مدیر/سامانه شما با `POST /api` → `CreateMeet` اتاق می‌سازد و `ID` اتاق می‌گیرد.
2. برای هر کاربر با `Type: "Join"` و `JoinRole: 0|1` یک **PID** و **لینک خصوصی** (`/user/<pid>`) می‌گیرید.
3. کاربر با آن لینک وارد می‌شود؛ صفحه‌ی SPA بارگذاری می‌شود و به `wss://<domain>/wsroom` با `token=PID` وصل می‌شود و در channel اتاق (`meet-<id>`) عضو می‌شود.
4. سیگنالینگ WebRTC (چیدمان/کنترل/پیام‌ها) روی همان وب‌سوکت و با protobuf انجام می‌شود؛ جریان صوتی/تصویری روی UDP از طریق SFU می‌گذرد.
5. فایل‌ها/ضبط‌ها در `wwwroot/files` (مسیر دقیق بستگی به نسخه دارد — بعد از نصب با `find /opt/persianmeet -name files -type d` پیدا کنید) ذخیره می‌شود.

## مدل نقش‌ها

- **adminUser (مدیر)**: لینک `/user/<pid>` با `JoinRole=1` می‌سازد؛ کنترل کامل (قطع صدای دیگران، مدیریت اتاق، ...).
- **کاربر عادی**: با `JoinRole=0`؛ فقط امکانات شرکت‌کننده.
- رمز اتاق (`UserPass` / `AdminPass`) فقط اگر `UserNeedPass/AdminNeedPass = true` لازم است.

## چیزهایی که **وجود ندارند** (تا معلوم نشود بعداً سورپرایز نشوید)

- ❌ سورس بک‌اند به زبان Go/C# (هرکدام که باشد) — عمومی نیست.
- ❌ فایل Dockerfile رسمی برای تک‌باینری (فقط توزیع docker آماده‌ی `alit771/proom` موجود است).
- ❌ مستندات رسمی از ساختار `config.yml` جز `auto_ssl` و `secret` (بقیه‌ی کلیدها را بعد از نصب در خود فایل ببینید؛ نمونه در `deploy/config.yml.example`).
- ❌ لایسنس متن‌باز برای هرکدام از ریپوهای سازنده — پس کپی داخل ریپوی شما مجاز نیست.
