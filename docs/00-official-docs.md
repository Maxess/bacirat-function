# مستندات رسمی PersianMeet — بایگانی کامل

> **تاریخ بایگانی:** ۲۰۲۶-۱۱-۰۹
> **منابع اصلی:**
> - صفحات فارسی `https://doc.learn100.ir/fa/docs/...` از این محیط مستقیماً باز نمی‌شدند (بعضی صفحات پاسخ خالی برمی‌گردانند).
>   محتوای این فایل از **نسخه‌های آرشیوشده‌ی Wayback Machine** از همان صفحات رسمی + وب‌سایت اصلی `https://doc.learn100.ir/` (نسخه انگلیسی ریشه) استخراج شده است.
> - نقشه سایت رسمی (`/sitemap.xml`) تأیید می‌کند که تمام صفحات مستندات همین‌ها هستند و صفحه‌ی دیگری وجود ندارد.
>
> اگر لینک زنده‌ی فارسی برایتان باز شد، همان را مرجع نهایی بگیرید؛ محتوای آن با این بایگانی یکی است (فقط شماره‌ی نسخه‌ها جلوتر رفته است: v0.24.3 → v0.26.3).

---

## ۰) intro — معرفی
منبع: `https://doc.learn100.ir/docs/intro` (بایگانی ۲۰۲۵-۰۱-۱۸)

> Welcome to persianmeet documentation. Easy to use, Scalable, High Performance, web conferencing system. Every website can have an end-to-end real-time conferencing solution.

**محتوا (ساده‌شده):**
1. [نصب روی localhost](https://doc.learn100.ir/docs/install/installation)
2. [دیپلوی روی سرور](https://doc.learn100.ir/docs/install/deploy)
3. [راهنمای API](https://doc.learn100.ir/docs/api/intro)

ویژگی‌های کلیدی از صفحه اصلی doc.learn100.ir:

- نصب آسان و ادغام با هر وب‌سایت/اپ/نرم‌افزار موجود؛ هر وب‌سایتی می‌تواند یک بستر هم‌زمان‌سازی انتها-به-انتها داشته باشد.
- **شخصی‌سازی کامل**: از URLها تا لوگو، رنگ‌های برند و ویژگی‌ها — با سهولت («You can personalize everything, from URLs to logos and branding colors, as well as features with ease»).
- تماس ویدیویی HD + اشتراک صفحه + اشتراک فایل + ضبط MP4.
- وایت‌برد و یادداشت مشترک؛ ارتباط WebRTC امن و رمزنگاری‌شده.
- سازگار با همه دستگاه‌ها (Chrome و Firefox توصیه؛ برای iOS سافاری).
- Simulcast و Dynacast برای اینترنت‌های ضعیف.
- **سیستم مقیاس‌پذیر و پرسرعت — به‌گفته‌ی سازنده «written in the Go programming language» که باعث شده به‌صورت یک فایل باینری تک‌فایلی توزیع شود.**
  (نکته: در README ریپوی `learn100ir/meetapp`، پشته‌ی «ASP.NET Core» هم ذکر شده؛ هر دو از زیرساخت‌های متن‌باز استفاده می‌کنند. مهم برای ما: خروجی نهایی یک باینری کامپایل‌شده است.)

مقایسه با رقبا (از README رسمی `sunmobiir/persianmeet`): وایت‌برد ✅، اشتراک صفحه ✅، کنفرانس ویدئو ✅، اشتراک فایل ✅، دیاگرام و ویرایشگر ریاضی ✅، رایگان ✅، نصب لینوکس/ویندوز ✅، **متن‌باز ❌**، اتاق‌های شکست (breakout rooms) ❌.

---

## ۱) installation — نصب روی localhost
منبع: `https://doc.learn100.ir/docs/install/installation` (بایگانی ۲۰۲۶-۰۳-۰۵)

### ۱.۱) ویندوز (localhost)
> Full package (server and client)

1. دانلود `persianmeet-win-v0.24.3.zip` (در نسخه‌ی شما: `persianmeet-linux-v0.26.3.zip`)
2. unzip کنید → پوشه‌ی `persianmeet` را باز کنید
3. روی `persianmeet.exe` دابل‌کلیک کنید
4. مرورگر Chrome را باز کنید: `http://localhost:8003`

**کاربر ادمین:** `admin` — **رمز:** `1111`

### ۱.۲) اوبونتو | دبیان (localhost)

```bash
sudo apt-get update
sudo apt-get install -y wget nano unzip mupdf-tools

wget  https://app.learn100.ir/upload/dl/persianmeet-linux-v0.24.3.zip
unzip  persianmeet-linux-v0.24.3.zip
cd persianmeet

sudo chmod +x persianmeet
./persianmeet
```

سپس: `http://localhost:8003` — همان کاربر/رمز ادمین بالا.

> نسخه‌ی فعلی شما (طبق پیامتان): `https://app2.learn100.ir/upload/dl/persianmeet-linux-v0.26.3.zip`
> توجه: در نسخه‌های جدیدتر، فایل `app/optimized` هم لازم دارد `chmod +x` بگیرد (طبق سند deploy لینوکس).

---

## ۲) deploy — دیپلوی روی ویندوز سرور
منبع: `https://doc.learn100.ir/docs/install/deploy` (بایگانی ۲۰۲۶-۰۱-۱۳)

**سیستم‌عامل پشتیبانی:** Windows Server 2016 / 2019 / 2022

### پیش‌نیازها
1. سرور ویندوز تمیز
2. IP عمومی
3. دامنه یا زیردامنه‌ای که به آن IP اشاره کند
4. ۳ گیگ رم و ۲ هسته CPU

### مراحل
0. موقتاً فایروال را غیرفعال کنید
1. دانلود و unzip پکیج `persianmeet-win-...zip`
2. پوشه‌ی `persianmeet` را باز کنید
3. فایل `config.yml` را باز کنید و `auto_ssl: true` بگذارید و ذخیره کنید
4. روی `persianmeet.exe` دابل‌کلیک کنید
5. مرورگر را روی دامنه‌تان باز کنید: `https://yourdomain.com`

**ادمین:** `admin` / `1111`

---

## ۳) deploy_ubuntu — دیپلوی روی اوبونتو | دبیان (سرور لینوکس)
منبع: `https://doc.learn100.ir/docs/install/deploy_ubuntu` (بایگانی ۲۰۲۵-۰۴-۱۳)

**سیستم‌عامل پشتیبانی:** Ubuntu | Debian

### پیش‌نیازها
1. اوبونتو/دبیان تمیز
2. IP عمومی
3. دامنه یا زیردامنه‌ای که به آن IP اشاره کند
4. کاربر با دسترسی root
5. ۲ گیگ رم و ۲ هسته CPU

### مراحل
```bash
# 1) آپدیت سیستم
sudo apt-get update

# 2) وابستگی‌ها
sudo apt-get install -y wget nano unzip mupdf-tools

# 3) دانلود پکیج لینوکس (server+client)
#    نسخه‌ی شما: v0.26.3 از app2.learn100.ir
wget https://app.learn100.ir/upload/dl/persianmeet-linux-v0.242.zip

# 4) استخراج
unzip persianmeet-linux-v0.242.zip

# 5) ورود به پوشه + مجوز اجرا
cd persianmeet
sudo chmod +x persianmeet app/optimized

# 6) فعال‌سازی SSL خودکار (گواهی رایگان Let's Encrypt روی دامنه)
sudo nano config.yml     # مقدار auto_ssl را true کنید و ذخیره کنید

# 7) اجرا
./persianmeet &

# 8) مرورگر:
#    https://yourdomain.com
```

**ادمین:** `admin` / `1111`

> نکات تکمیلی که در اسکریپت‌های این ریپو لحاظ شده:
> - برای اجرا در background به‌جای `./persianmeet &` بهتر است از **systemd service** استفاده کنید (فایل `deploy/persianmeet.service`).
> - اگر می‌خواهید پورت‌های ۸۰/۴۴۳ را Nginx تحویل بگیرد، `auto_ssl: false` کنید و از `deploy/nginx.reverse-proxy.conf` استفاده کنید.

---

## ۴) api/intro — قالب کلی API
منبع: `https://doc.learn100.ir/docs/api/intro` (بایگانی ۲۰۲۵-۱۲-۰۸)

- تمام endpoint ها یک‌جا هستند: **`POST /api`**
- هدر اجباری: `Content-type: application/json`
- بدنه‌ی درخواست (JSON):

| فیلد | نوع | اجباری | توضیح |
|---|---|:--:|---|
| `Secret` | string | اختیاری* | secret api (در `config.yml` تنظیم می‌شود) |
| `Type` | string | ✅ | نوع درخواست (مثلاً `CreateMeet`) |
| `Data` | json | اختیاری | بدنه‌ی مخصوص هر درخواست |

\* به طور پیش‌فرض `MeetList` و `ActiveMeets` به secret نیاز دارند؛ می‌توانید در `config.yml` تعیین کنید کدام درخواست‌ها به secret نیاز داشته باشند.

### فهرست درخواست‌ها

| Type | کار |
|---|---|
| `CreateMeet` | ساخت اتاق جدید |
| `GetMeetInfo` | دریافت اطلاعات اتاق |
| `Join` | پیوستن کاربر به اتاق (لینک خصوصی برمی‌گرداند) |
| `MeetList` | لیست آخرین اتاق‌ها (نیاز به secret) |
| `ActiveMeets` | اتاق‌هایی که کاربر آنلاین دارند (نیاز به secret) |
| `DeleteMeet` | توقف و حذف اتاق + تمام داده‌هایش (نیاز به secret) |

---

## ۵) CreateMeet — ساخت اتاق
منبع: `https://doc.learn100.ir/docs/api/create_meet` (بایگانی ۲۰۲۵-۰۷-۱۰)

نقش‌ها: کاربر عادی (شرکت‌کننده) و `adminUser` (مدیر/مجری). مدیر کنترل بیشتری دارد.

### پارامترها

| فیلد | نوع | اجباری | توضیح |
|---|---|:--:|---|
| `Secret` | string | اختیاری | پیش‌فرض نیاز نیست |
| `Type` | string | ✅ | باید `"CreateMeet"` باشد |
| `Data.Title` | string | ✅ | عنوان اتاق |
| `Data.UserNeedPass` | bool | ✅ | آیا کاربر عادی به رمز نیاز دارد؟ |
| `Data.UserPass` | string | اختیاری | رمز کاربر عادی (اگر نیاز باشد) |
| `Data.AdminNeedPass` | bool | ✅ | آیا مدیر به رمز نیاز دارد؟ |
| `Data.AdminPass` | string | اختیاری | رمز مدیر (اگر نیاز باشد) |
| `Data.UserCount` | int | ✅ | ظرفیت اتاق |

### نمونه درخواست
```json
{
  "Secret": "",
  "Type": "CreateMeet",
  "Data": {
    "UserPass": "",
    "UserNeedPass": false,
    "AdminNeedPass": true,
    "AdminPass": "password",
    "Title": "my room",
    "UserCount": 100
  }
}
```

### پاسخ
| فیلد | مقدار | توضیح |
|---|---|---|
| `Status` | `"ok"` | موفقیت |
| `Message` | string | پیام پاسخ |
| `ID` | int | شناسه‌ی اتاق |
| `Url` | string | مثلاً `/room/21` |
| `Title` | string | عنوان اتاق |

```json
{ "Status": "ok", "ID": 21, "Url": "/room/21", "Title": "my room", "Message": "" }
```

---

## ۶) Join — پیوستن به اتاق
منبع: `https://doc.learn100.ir/docs/api/join_meet` (بایگانی ۲۰۲۵-۰۷-۱۰)

### پارامترها

| فیلد | نوع | اجباری | توضیح |
|---|---|:--:|---|
| `Secret` | string | اختیاری | پیش‌فرض نیاز نیست |
| `Type` | string | ✅ | باید `"Join"` باشد |
| `Data.userName` | string | ✅ | نام نمایشی کاربر |
| `Data.meetID` | int | ✅ | شناسه‌ی اتاق |
| `Data.Pass` | string | اختیاری | رمز اتاق (اگر نیاز باشد) |
| `Data.JoinRole` | int | ✅ | `0` کاربر عادی / `1` مدیر |

### نمونه درخواست
```json
{
  "Secret": "",
  "Type": "Join",
  "Data": { "meetID": 21, "userName": "ali", "Pass": "password", "JoinRole": 1 }
}
```

### پاسخ
| فیلد | توضیح |
|---|---|
| `Status` = `"ok"` | موفقیت |
| `PID` | شناسه‌ی خصوصی کاربر (مثلاً `cipqrnhn4tji2f2kpn6g`) |
| `Url` | **لینک خصوصی ورود** کاربر، مثلاً `/user/cipqrnhn4tji2f2kpn6g` |
| `Message` | پیام |

```json
{ "Status": "ok", "PID": "cipqrnhn4tji2f2kpn6g", "Message": "", "Url": "/user/cipqrnhn4tji2f2kpn6g" }
```

> 💡 همین لینک خصوصی را به هر کاربر بدهید تا بدون رمز وارد همان اتاق با همان نقش شود.

---

## ۷) GetMeetInfo — اطلاعات اتاق
منبع: `https://doc.learn100.ir/docs/api/get_meet_info` (بایگانی ۲۰۲۶-۰۱-۱۳)

| فیلد | نوع | اجباری | توضیح |
|---|---|:--:|---|
| `Type` | string | ✅ | باید `"GetMeetInfo"` باشد |
| `Data.ID` | int | ✅ | شناسه‌ی اتاق |
| `Secret` | string | اختیاری | پیش‌فرض نیاز نیست |

```json
{ "Secret": "", "Type": "GetMeetInfo", "Data": { "ID": 21 } }
```

پاسخ:
```json
{ "Status": "ok", "ID": 21, "UserNeedPass": 0, "AdminNeedPass": 0, "Title": "my room", "Message": "" }
```

---

## ۸) MeetList — لیست اتاق‌ها
منبع: `https://doc.learn100.ir/docs/api/meet_list` (بایگانی ۲۰۲۵-۰۷-۱۰)

نیاز به secret دارد (طبق مستندات رسمی).

```json
{ "Secret": "apisecret", "Type": "MeetList" }
```
پاسخ: `{ "Status": "ok", "Data": { ... } }`

---

## ۹) ActiveMeets — اتاق‌های فعال
منبع: `https://doc.learn100.ir/docs/api/active_meets` (بایگانی ۲۰۲۶-۰۲-۰۸)

اتاق‌هایی که حداقل یک کاربر آنلاین دارند. نیاز به secret دارد.

```json
{ "Secret": "apisecret", "Type": "ActiveMeets" }
```
پاسخ: `{ "Status": "ok", "Data": { ... } }`

---

## ۱۰) DeleteMeet — حذف اتاق
منبع: `https://doc.learn100.ir/docs/api/delete_meet` (بایگانی ۲۰۲۶-۰۱-۱۳)

> ⚠️ **تمام چت‌ها، کاربران و فایل‌های آن اتاق برای همیشه حذف می‌شود.**

| فیلد | نوع | اجباری | توضیح |
|---|---|:--:|---|
| `Secret` | string | ✅ | طبق `config.yml` |
| `Type` | string | ✅ | باید `"DeleteMeet"` باشد |
| `Data.ID` | int | ✅ | شناسه‌ی اتاق |

```json
{ "Secret": "secret", "Type": "DeleteMeet", "Data": { "ID": 21 } }
```
پاسخ: `{ "Status": "ok", "Message": "" }`

---

## ۱۱) پورت‌ها و سرویس‌های داخلی (استخراج‌شده از مستندات و کد عمومی سازنده)

| پورت | سرویس |
|---|---|
| ۸۰ / ۴۴۳ | HTTP/HTTPS (با `auto_ssl: true` خودِ باینری گواهی Let's Encrypt می‌گیرد) |
| ۸۰۰۳ | پورت HTTP داخلی قبل از SSL / روی localhost (طبق `http://localhost:8003`) |
| `/api` | REST API خود محصول (همان بالا) |
| `/wsroom` | WebSocket سیگنالینگ (طبق سورس عمومی `sunmobiir/meet-react` → `src/service/signal.ts`، بر پایه‌ی Centrifugo با پروتکل protobuf) |
| UDP (بازه‌ی چندصدتایی مثل ۵۰۰۰–۵۲۰۰ در نسخه‌ی docker) | ترافیک SFU/WebRTC — بعد از نصب با `ss -tulpn` پورت‌های دقیق را ببینید |

## ۱۲) فایل‌های داخل پکیج لینوکس (مطابق مستندات)

- `persianmeet` — باینری اصلی (تک‌فایلی)
- `config.yml` — پیکربندی اصلی (حداقل شامل `auto_ssl` و `secret`)
- `app/optimized` — فایل مکمل که باید مجوز اجرا بگیرد (در نسخه‌های جدید)
- فایل‌های استاتیک وب (`wwwroot`) — معمولاً داخل/کنار باینری و بسته به نسخه در پکیج دیده می‌شوند؛ بعد از `unzip` با `ls -la persianmeet/` آن‌ها را ببینید.

## ۱۳) ریپوهای عمومی مرتبط با سازنده (ارجاع، نه کپی)

| آدرس | محتوا | وضعیت |
|---|---|---|
| `github.com/sunmobiir/persianmeet` | README و تصاویر محصول + جدول مقایسه | عمومی، بدون لایسنس |
| `github.com/learn100ir/meetapp` | `docker-compose.yml` استقرار داکر (traefik + mosquitto + collabora + SFU + proom `alit771/*`) | عمومی، بدون لایسنس |
| `github.com/sunmobiir/meet-react` | پروتوتایپ/نمونه‌ی فرانت‌اند React اتاق (برای مرجع؛ سیگنالینگ روی `/wsroom` + Centrifugo/protobuf) | عمومی، بدون لایسنس — نسخه‌ی نهایی نیست |
| `github.com/sunmobiir/meet-connect` | ماکت client-side اتاق (Vanilla/AntD + Zustand، بدون بک‌اند واقعی) | عمومی، بدون لایسنس |
| `github.com/sunmobiir/aimeet` | پروتوتایپ AI meeting (شبیه‌سازی) | عمومی، بدون لایسنس |
| `github.com/sunmobiir/g1`, `stream`, `webpagerecorder` | ابزارهای جانبی (ضبط/استریم/…) | عمومی، بدون لایسنس |

> ❗ هیچ‌کدام از این ریپوها LICENSE ندارند؛ طبق قانون کپی‌رایت «همه‌ی حقوق محفوظ» است و کپی/بازتوزیع
> آن‌ها در پروژه‌ی دیگران بدون اجازه‌ی سازنده مجاز نیست. فقط به‌عنوان **مرجع مطالعه** از آن‌ها استفاده کنید.
