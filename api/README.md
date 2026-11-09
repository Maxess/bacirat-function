# کلاینت API سرور PersianMeet

پیاده‌سازی کامل endpoint رسمی `POST /api` (طبق مستندات — `../docs/00-official-docs.md`) با پایتون خالص، بدون نیاز به `pip install`.

## تنظیم

از طریق آرگومان‌ها یا متغیرهای محیطی:

```bash
export PERSIANMEET_URL=https://class.example.com   # یا http://127.0.0.1:8003 برای اجرای محلی
export PERSIANMEET_SECRET=your-secret-from-config.yml
```

## گردش‌کار ساخت کلاس از سایت شما

```bash
# 1) ساخت اتاق
python3 persianmeet_api.py create --title "کلاس ریاضی ۱۰۱" --user-count 40 --admin-pass "quera-secret"
# خروجی: {"Status": "ok", "ID": 21, "Url": "/room/21", "Title": "..."}

# 2) گرفتن لینک خصوصی دانشجو (role=0) و مدرس (role=1)
python3 persianmeet_api.py join --meet-id 21 --username "سارا" --role 0
python3 persianmeet_api.py join --meet-id 21 --username "دکتر احمدی" --role 1 --pass "quera-secret"
# خروجی: {"Status": "ok", "PID": "xxxxxxxx", "Url": "/user/xxxxxxxx"}
# لینک کامل: https://class.example.com/user/xxxxxxxx
```

## مدیریت اتاق‌ها

```bash
python3 persianmeet_api.py info   --meet-id 21                 # اطلاعات اتاق
python3 persianmeet_api.py list                                 # لیست اتاق‌ها (secret)
python3 persianmeet_api.py active                               # اتاق‌های فعال (secret)
python3 persianmeet_api.py delete --meet-id 21 --yes            # حذف (secret؛ غیرقابل بازگشت!)
```

## استفاده به‌صورت کتابخانه در پایتون

```python
from persianmeet_api import PersianMeetAPI, PersianMeetError

api = PersianMeetAPI("https://class.example.com", secret="…", timeout=10)
room = api.create_meet(title="وبینار", user_count=200, admin_need_pass=True, admin_pass="x")
print(room["ID"])
print(api.join(room["ID"], "علی", join_role=0)["Url"])
```

## مثال‌ها با curl

همه‌ی درخواست‌ها در [`examples/curl-examples.sh`](examples/curl-examples.sh) آمده‌اند.

## خطاهای رایج

| پیام | علت | راه‌حل |
|---|---|---|
| `HTTP 401/403` | Secret اشتباه یا الزامی است | مقدار `secret` در `config.yml` را چک کنید |
| `اتصال به سرور برقرار نشد` | آدرس/پورت یا فایروال | `--base-url` را بررسی کنید؛ `ufw status` |
| `پاسخ سرور JSON نبود` | مسیر `/api` روی همان دامنه نیست یا ریورس‌پراکسی خراب است | با `--base-url http://127.0.0.1:8003` تست کنید تا بفهمید مشکل از پراکسی است یا محصول |

## امنیت

- `PERSIANMEET_SECRET` را در کد commit نکنید؛ از env استفاده کنید. CLI عمداً هیچ‌جا secret را echo نمی‌کند (به جز `create` که ورودی شماست).
- لینک‌های `/user/<pid>` مثل رمز عبورند؛ در لاگ‌های عمومی ذخیره نکنید.
