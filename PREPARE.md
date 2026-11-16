# آماده‌سازی سرور — پیش‌نیازهای نصب

این راهنما سرور را برای نصب آماده می‌کند. پس از پایان آن، به
[`INSTALL.md`](INSTALL.md) بروید.

اگر از قبل Go، فایروال و گواهی را دارید، فقط **بخش ۹ (چک‌لیست)** را اجرا
کنید تا آمادگی تأیید شود.

---

## ۱. آنچه نصب خواهد شد

| جزء | نقش | نیاز در زمان اجرا |
|---|---|---|
| `galene` | سرور کلاس (یک باینری Go) | همیشه |
| Go | **فقط برای بیلد** | خیر (پس از نصب قابل حذف است) |
| PHP + `pdo_mysql` | خروجی‌گیر مدرسه | فقط برای همگام‌سازی |
| Nginx/Apache | reverse proxy | اختیاری |

کل سیستم یک باینری تک‌فایل است: **بدون پایگاه داده، بدون Node، بدون بیلد
فرانت‌اند**.

---

## ۲. سخت‌افزار و پهنای باند

سرور ویدئو را بین شرکت‌کنندگان پخش می‌کند (SFU)، بنابراین بار اصلی
**پهنای باند خروجی** است، نه CPU.

مصرف تقریبی برای هر جریان:

| نوع | پهنای باند |
|---|---|
| صدا | ~۴۰ کیلوبیت بر ثانیه |
| تصویر ۳۶۰p | ~۳۰۰ کیلوبیت بر ثانیه |
| تصویر ۷۲۰p | ~۱ مگابیت بر ثانیه |

پهنای باند خروجی ≈ تعداد دانش‌آموزان × بیت‌ریت تصویر دبیر.

| اندازهٔ کلاس | پهنای باند خروجی پیشنهادی | CPU | RAM |
|---|---|---|---|
| تا ۱۵ نفر | ۱۰ مگابیت | ۱ هسته | ۵۱۲ مگابایت |
| تا ۳۰ نفر | ۲۰ مگابیت | ۲ هسته | ۱ گیگابایت |
| تا ۶۰ نفر | ۵۰ مگابیت | ۲ تا ۴ هسته | ۲ گیگابایت |

> این اعداد برای یک کلاس هم‌زمان است. برای چند کلاسِ هم‌زمان، جمع را
> حساب کنید. مصرف واقعی CPU پایین است چون ویدئو رمزگشایی/رمزنگاری
> نمی‌شود، فقط بازارسال می‌گردد.

حداقل فضای دیسک: ۲۰۰ مگابایت (بدون ضبط). هر ساعت ضبط ≈ ۲۰۰ مگابایت به
ازای هر جریان.

---

## ۳. سیستم‌عامل

توزیع‌های پشتیبانی‌شده: Debian ۱۲+، Ubuntu ۲۲.۰۴+، Alma/Rocky ۹+،
و هر لینوکس دارای systemd و کرنل ۴.x+.

```sh
cat /etc/os-release
uname -r
```

به‌روزرسانی:

```sh
# Debian / Ubuntu
sudo apt update && sudo apt upgrade -y

# Alma / Rocky / RHEL
sudo dnf update -y
```

اگر به‌روزرسانی کرنل انجام شد، یک بار重启 کنید.

---

## ۴. شبکه: نشانی، DNS و پورت‌ها

### ۴-۱. یک نشانی عمومی ثابت

سرور باید یک **IPv4 عمومی** داشته باشد که مستقیماً به اینترنت وصل است
(پشت NAT نباشد). بررسی:

```sh
curl -4 ifconfig.me
ip -4 addr show scope global
```

اگر نشانی عمومی ندارید یا پشت NAT هستید، Galene نیاز به یک سرور TURN
خارجی دارد (بخش ۶-۳).

### ۴-۲. نام دامنه

یک نام به نشانی سرور اشاره کند:

```
class.school.ac.ir    A    <IPv4 سرور>
```

بررسی:

```sh
dig +short class.school.ac.ir
```

### ۴-۳. پورت‌های مورد نیاز

| پورت | پروتکل | برای چه | ضروری |
|---|---|---|---|
| ۸۴۴۳ | TCP | رابط وب و سیگنال‌دهی | بله |
| ۱۱۹۴ | TCP + UDP | سرور TURN داخلی | بله (برای اتصال از شبکه‌های محدود) |
| ۴۰۰۰۰–۵۰۰۰۰ | UDP | انتقال تصویر و صدا | برای عملکرد خوب |
| ۸۰ / ۴۴۳ | TCP | reverse proxy (در صورت استفاده) | اختیاری |

**Debian / Ubuntu (ufw):**

```sh
sudo apt install -y ufw
sudo ufw allow 22/tcp
sudo ufw allow 8443/tcp
sudo ufw allow 1194/tcp
sudo ufw allow 1194/udp
sudo ufw allow 40000:50000/udp
sudo ufw enable
sudo ufw status
```

**Alma / Rocky (firewalld):**

```sh
sudo dnf install -y firewalld
sudo systemctl enable --now firewalld
sudo firewall-cmd --permanent --add-port=8443/tcp
sudo firewall-cmd --permanent --add-port=1194/tcp
sudo firewall-cmd --permanent --add-port=1194/udp
sudo firewall-cmd --permanent --add-port=40000-50000/udp
sudo firewall-cmd --reload
sudo firewall-cmd --list-ports
```

> بازهٔ UDP را در زمان اجرا با `-udp-range 40000-50000` به سرور می‌دهیم.
> این بسیار امن‌تر از باز گذاشتن همهٔ پورت‌های ephemeral است. می‌توانید
> از `-udp-range 40000` (تک پورت) هم استفاده کنید، اما مستندات بالادست
> می‌گوید آن حالت هنوز کامل نیست.

> اگر ارائه‌دهندهٔ شما (مانند برخی سرویس‌های ابری) فایروال جداگانه‌ای در
> سطح پنل دارد، همان پورت‌ها را آنجا هم باز کنید.

---

## ۵. نصب Go (نسخهٔ ۱.۲۴ یا بالاتر)

سرور **در زمان اجرا به Go نیاز ندارد**؛ فقط برای بیلد لازم است. می‌توانید
پس از نصب آن را حذف کنید.

> **هشدار دربارهٔ نصب خودکار (شایع‌ترین خطا).**
> اگر `go version` پیام `Command 'go' not found` می‌دهد و اوبونتو پیشنهاد
> می‌دهد `apt install golang-go` یا `snap install go`، بدانید که این بسته‌ها
> در بسیاری از نسخه‌ها **Go ۱.۱۸** هستند. پروژه به **۱.۲۴ یا بالاتر** نیاز
> دارد (`galene/go.mod`)، پس آن‌ها را نصب نکنید و از بستهٔ رسمی در پایین
> استفاده کنید.
> اگر قبلاً نصب کرده‌اید، حذفش کنید:
> `sudo apt remove -y golang-go` یا `sudo snap remove go`

### روش پیشنهادی — بستهٔ رسمی

```sh
cd /tmp
VER=1.24.13
curl -fsSLO https://go.dev/dl/go${VER}.linux-amd64.tar.gz
ls -l go${VER}.linux-amd64.tar.gz    # باید حدود ۷۰ مگابایت باشد
sudo rm -rf /usr/local/go
sudo tar -C /usr/local -xzf go${VER}.linux-amd64.tar.gz
echo 'export PATH=$PATH:/usr/local/go/bin' | sudo tee /etc/profile.d/go.sh
export PATH=$PATH:/usr/local/go/bin
go version
```

خروجی باید چیزی شبیه `go version go1.24.13 linux/amd64` باشد. (هر نسخهٔ
۱.۲۴.x یا بالاتر پذیرفته است؛ ۱.۲۴.۱۳ آخرین وصلهٔ شاخهٔ ۱.۲۴ است.)

> اگر `curl` با خطای `403` یا timeout شکست خورد، ادامهٔ همین بخش
> («اگر `go.dev` در دسترس نیست») را بخوانید.

اگر معماری سرور amd64 نیست، آن را با `uname -m` بررسی کنید و بستهٔ
متناظر (مثلاً `linux-arm64.tar.gz`) را از
[go.dev/dl](https://go.dev/dl/) بگیرید.

### نکتهٔ مهم: `sudo` و `PATH`

دستور `sudo` فایل‌های `/etc/profile.d` را نمی‌خواند، بنابراین هنگام اجرای
`sudo ./install.sh` ممکن است باز هم `go` پیدا نشود. یک لینک بسازید تا در
همهٔ حالت‌ها پیدا شود:

```sh
sudo ln -sf /usr/local/go/bin/go    /usr/local/bin/go
sudo ln -sf /usr/local/go/bin/gofmt /usr/local/bin/gofmt
sudo go version        # باید go1.24.x را چاپ کند
```

(اسکریپت `install.sh` خودش هم `/usr/local/go/bin` را جست‌وجو می‌کند، اما
ساختن این لینک مطمئن‌تر است.)

### اگر `go.dev` در دسترس نیست

روی برخی سرورها `go.dev` (و `dl.google.com`) مسدود است و خطای
`403` یا timeout می‌دهد. بهترین جایگزین، **آینهٔ رسمی روی GitHub** است —
همان باینری‌ها، فقط با نام فایل متفاوت:

```sh
sudo curl -fsSLo /tmp/go.tgz \
  https://github.com/actions/go-versions/releases/download/1.24.13-21696107652/go-1.24.13-linux-x64.tar.gz
ls -l /tmp/go.tgz                  # باید حدود ۷۰ مگابایت باشد
tar tzf /tmp/go.tgz | head -3      # فایل سالم است؟
```

نام پوشهٔ درون این فایل (`go/`، `go-1.24.13/` یا چیز دیگر) ممکن است با
بستهٔ رسمی فرق کند، بنابراین از این روش استفاده کنید که هر سه حالت را
درست مدیریت می‌کند:

```sh
TMP=$(mktemp -d)
sudo tar -C "$TMP" -xzf /tmp/go.tgz
GOBIN=$(find "$TMP" -type f -path '*/bin/go' | head -1)
GOROOT=$(dirname "$(dirname "$GOBIN")")
sudo rm -rf /usr/local/go
sudo mv "$GOROOT" /usr/local/go
sudo rm -rf "$TMP"
sudo /usr/local/go/bin/go version        # باید go1.24.13 را چاپ کند
```

سپس دستورهای `ln -sf` و `export PATH` از بالا را اجرا کنید.

### اگر ترمینال، Paste چندخطی را به هم می‌ریزد

برخی کنسول‌ها هنگام Paste چند خط، دستورها را با خروجیِ قبلی قاطی می‌کنند؛
مثلاً `VER=1.24.13` به شکل `VER=1.24.x86_64` درمی‌آید و بعد `curl` خطای
`404` می‌دهد (در حالی که خودِ نشانی درست است). اگر چنین است، این دو
دستور **تک‌خطی** را یکی‌یکی اجرا کنید — هیچ متغیری ندارند:

```sh
sudo sh -c 'for u in https://go.dev/dl/go1.24.13.linux-amd64.tar.gz https://github.com/actions/go-versions/releases/download/1.24.13-21696107652/go-1.24.13-linux-x64.tar.gz; do echo "trying $u"; curl -fsSL --connect-timeout 20 -o /tmp/go.tgz "$u" && tar tzf /tmp/go.tgz >/dev/null 2>&1 && { echo OK; break; }; rm -f /tmp/go.tgz; done; ls -l /tmp/go.tgz'
```

```sh
sudo sh -c 'set -e; T=$(mktemp -d); tar -C "$T" -xzf /tmp/go.tgz; G=$(find "$T" -type f -path "*/bin/go" | head -1); R=$(dirname "$(dirname "$G")"); rm -rf /usr/local/go; mv "$R" /usr/local/go; rm -rf "$T"; ln -sf /usr/local/go/bin/go /usr/local/bin/go; ln -sf /usr/local/go/bin/gofmt /usr/local/bin/gofmt; /usr/local/go/bin/go version'
```

دستور اول فقط دانلود می‌کند و اندازهٔ فایل را چاپ می‌کند (باید حدود ۷۰
مگابایت باشد)؛ دستور دوم نصب می‌کند و نسخه را چاپ می‌کند. لینک‌های
`/usr/local/bin` در مسیر پیش‌فرضِ همهٔ کاربران — و مهم‌تر از همه، در
مسیر `sudo` — هستند، بنابراین بعد از این دو دستور هم `go version` و هم
`sudo go version` باید `go1.24.13` را چاپ کنند. اگر هیچ منبعی کار نکند،
`/tmp/go.tgz` ساخته نمی‌شود و `ls` پیام `No such file` می‌دهد.

### راه دیگر: بستهٔ snap

اگر snapd روی سرور فعال است، این راه از شبکهٔ Fastly دانلود می‌کند (نه
از دامنه‌های گوگل):

```sh
sudo snap install go --classic --channel=1.24/stable
sudo ln -sf /snap/bin/go /usr/local/bin/go
go version
```

اگر کانال `1.24/stable` را نداشت، `snap info go` کانال‌های موجود را نشان
می‌دهد؛ هر کانالی با نسخهٔ ۱.۲۴ یا بالاتر قابل قبول است.

### راه آخر: انتقال دستی

اگر هیچ‌کدام نشد، فایل را روی رایانهٔ خودتان از
[go.dev/dl](https://go.dev/dl/) دانلود و منتقل کنید:

```sh
scp go1.24.13.linux-amd64.tar.gz root@SERVER:/tmp/
```

و سپس `cp /tmp/go1.24.13.linux-amd64.tar.gz /tmp/go.tgz` را اجرا و همان
مراحل استخراج بالا را تکرار کنید.

---

## ۶. گواهی TLS

مرورگرها برای دسترسی به میکروفون و دوربین **حتماً به HTTPS نیاز دارند**،
بنابراین گواهی TLS اجباری است. سه راه:

### ۶-۱. گواهی رسمی با Certbot (پیشنهادی)

اگر سرور مستقیماً روی اینترنت است و پورت ۸۰ آزاد است:

```sh
# Debian / Ubuntu
sudo apt install -y certbot

sudo systemctl stop bacirat 2>/dev/null || true
sudo certbot certonly --standalone -d class.school.ac.ir
```

گواهی در `/etc/letsencrypt/live/class.school.ac.ir/` قرار می‌گیرد. سپس در
زمان نصب آن را به سرور معرفی می‌کنیم (بخش ۶-۴).

تمدید خودکار:

```sh
sudo certbot renew --dry-run
```

### ۶-۲. پشت reverse proxy

اگر Nginx جلوی سرور است، گواهی را روی Nginx بگذارید و ترافیک را به
`localhost:8443` هدایت کنید. در این حالت Galene را با `-insecure`
(HTTP ساده) روی localhost اجرا کنید.

نمونهٔ تنظیم Nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name class.school.ac.ir;

    ssl_certificate     /etc/letsencrypt/live/class.school.ac.ir/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/class.school.ac.ir/privkey.pem;

    # WebSocket برای سیگنال‌دهی — لازم است
    location /ws {
        proxy_pass http://127.0.0.1:8443;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:8443;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 0;
    }
}
```

> هدایت ترافیک WebSocket برای `/ws` حیاتی است؛ بدون آن کلاس بالا
> نمی‌آید.

### ۶-۳. گواهی self-signed (فقط برای آزمایش)

اگر چیزی تنظیم نکنید، سرور در اولین اجرا یک گواهی self-signed می‌سازد و
مرورگر هشدار می‌دهد. برای آزمایش قابل قبول است، برای استفادهٔ واقعی نه.

### ۶-۴. معرفی گواهی به سرور

در صورت استفاده از روش ۶-۱، پس از نصب، گواهی را جایگزین کنید:

```sh
sudo cp /etc/letsencrypt/live/class.school.ac.ir/fullchain.pem \
        /opt/bacirat/data/cert.pem
sudo cp /etc/letsencrypt/live/class.school.ac.ir/privkey.pem \
        /opt/bacirat/data/key.pem
sudo chown bacirat:bacirat /opt/bacirat/data/cert.pem /opt/bacirat/data/key.pem
sudo chmod 600 /opt/bacirat/data/key.pem
sudo systemctl restart bacirat
```

---

## ۷. PHP (فقط برای همگام‌سازی با سیستم مدیریت مدرسه)

اگر می‌خواهید کلاس‌ها را مستقیماً از پایگاه مدرسه بیاورید:

```sh
# Debian / Ubuntu
sudo apt install -y php-cli php-mysql

# Alma / Rocky
sudo dnf install -y php-cli php-mysqlnd
```

بررسی:

```sh
php -v
php -m | grep -i pdo_mysql
```

اگر `php` نصب نبود (`Command 'php' not found`)، همان دستور بالا آن را
نصب می‌کند. اگر پس از نصب باز هم `pdo_mysql` در فهرست نبود (گاهی بستهٔ
`php-mysql` با نسخهٔ پیش‌فرض PHP تطبیق نمی‌کند)، ماژولِ همان نسخه را نصب
کنید:

```sh
sudo apt install -y "php$(php -r 'echo PHP_MAJOR_VERSION . "." . PHP_MINOR_VERSION;')-mysql"
php -m | grep -i pdo_mysql
```

نسخهٔ PHP اهمیت زیادی ندارد؛ ۸.۱ (پیش‌فرض اوبونتو ۲۲٫۰۴) به بالا کافی است.

> بدون این بخش هم سرور کامل کار می‌کند؛ فقط همگام‌سازی خودکار کلاس‌ها را
> نخواهید داشت و کلاس‌ها را دستی می‌سازید.

---

## ۸. آماده‌سازی پایگاه دادهٔ مدرسه (فقط برای همگام‌سازی)

**یک کاربر فقط‌خواندنی** بسازید. خروجی‌گیر به چیزی جز خواندن نیاز ندارد و
این کار خطر را به حداقل می‌رساند.

روی سرور پایگاه داده:

```sql
CREATE USER 'bacirat'@'<نشانی سرور کلاس>' IDENTIFIED BY 'یک رمز قوی';
GRANT SELECT ON school.students   TO 'bacirat'@'<نشانی سرور کلاس>';
GRANT SELECT ON school.classes    TO 'bacirat'@'<نشانی سرور کلاس>';
GRANT SELECT ON school.teachers   TO 'bacirat'@'<نشانی سرور کلاس>';
GRANT SELECT ON school.class_schedules TO 'bacirat'@'<نشانی سرور کلاس>';
GRANT SELECT ON school.settings   TO 'bacirat'@'<نشانی سرور کلاس>';
GRANT SELECT ON school.academic_years  TO 'bacirat'@'<نشانی سرور کلاس>';
FLUSH PRIVILEGES;
```

نام پایگاه (`school`) را با نام واقعی جایگزین کنید. بررسی از روی سرور
کلاس:

```sh
mysql -h <نشانی پایگاه> -u bacirat -p school -e "SELECT COUNT(*) FROM students;"
```

> این کاربر فقط `SELECT` دارد، بنابراین حتی در صورت افشای رمز، اطلاعات
> مدرسه تغییر نمی‌کند.

---

## ۹. تنظیمات سیستم

### ۹-۱. تعداد فایل‌های باز

سرور به ازای هر اتصال از چند توصیف‌گر فایل استفاده می‌کند و اگر محدودیت
کم باشد هشدار می‌دهد.

```sh
cat > /etc/security/limits.d/bacirat.conf <<'EOF'
bacirat soft nofile 65535
bacirat hard nofile 65535
EOF
```

واحد systemd نصب‌شده خودش `LimitNOFILE=65535` را تنظیم می‌کند، بنابراین
این مورد بیشتر برای اطمینان از بیلد و اجرای دستی است.

بررسی پس از نصب:

```sh
systemctl show bacirat | grep LimitNOFILE
```

### ۹-۲. زمان و منطقهٔ زمانی

تاریخ شمسی و زمان‌بندی کلاس‌ها به ساعت سرور وابسته است:

```sh
sudo timedatectl set-timezone Asia/Tehran
timedatectl
```

و همگام‌سازی زمان:

```sh
sudo apt install -y chrony && sudo systemctl enable --now chronyd   # Debian
sudo dnf install -y chrony && sudo systemctl enable --now chronyd   # Alma
```

---

## ۱۰. چک‌لیست نهایی

این اسکریپت همهٔ موارد بالا را بررسی می‌کند:

```sh
cat > /tmp/ready.sh <<'EOF'
#!/bin/sh
ok=0; bad=0
chk() { if eval "$2" >/dev/null 2>&1; then echo "  [ok]   $1"; ok=$((ok+1));
        else echo "  [FAIL] $1"; bad=$((bad+1)); fi; }

echo "Go:"
chk "go installed" "command -v go"
if command -v go >/dev/null 2>&1; then
  # Compare major and minor separately: Go 1.9 is older than 1.24
  # even though 1.9 is the larger number.
  v=$(go version | sed 's/.*go\([0-9]*\.[0-9]*\).*/\1/')
  maj=$(echo "$v" | cut -d. -f1)
  min=$(echo "$v" | cut -d. -f2)
  chk "version $v >= 1.24" \
      "[ $maj -gt 1 ] || { [ $maj -eq 1 ] && [ $min -ge 24 ]; }"
fi
if ! command -v go >/dev/null 2>&1 ||
   ! { [ "${maj:-0}" -gt 1 ] 2>/dev/null ||
       { [ "${maj:-0}" -eq 1 ] && [ "${min:-0}" -ge 24 ]; }; }; then
  echo "         -> بخش ۵ از PREPARE.md را اجرا کنید"
  echo "            (بستهٔ apt/snap معمولاً Go ۱.۱۸ است و کافی نیست)"
fi

echo "Network:"
chk "public IPv4 present" "ip -4 addr show scope global | grep -q inet"

echo "Firewall:"
if command -v ufw >/dev/null 2>&1; then
  chk "ufw allows 8443/tcp"    "ufw status | grep -q '8443/tcp'"
  chk "ufw allows 1194 (tcp+udp)" "ufw status | grep -q '1194'"
  chk "ufw allows UDP media range" "ufw status | grep -q '40000:50000/udp'"
elif command -v firewall-cmd >/dev/null 2>&1; then
  chk "firewalld allows 8443/tcp" "firewall-cmd --list-ports | grep -q 8443/tcp"
  chk "firewalld allows 1194"     "firewall-cmd --list-ports | grep -q 1194"
  chk "firewalld allows UDP range" "firewall-cmd --list-ports | grep -q 40000-50000/udp"
fi

echo "System:"
chk "timezone is Asia/Tehran" "timedatectl show | grep -q 'Timezone=Asia/Tehran'"
chk "nofile limit configured" "[ -f /etc/security/limits.d/bacirat.conf ]"

echo "Optional (school sync):"
chk "php installed"        "command -v php"
chk "pdo_mysql available"  "php -m 2>/dev/null | grep -qi pdo_mysql"

echo
echo "passed=$ok  failed=$bad"
EOF
sh /tmp/ready.sh
```

تفسیر:

- موارد **Go** و **Network** باید همگی `ok` باشند.
- مورد **Firewall** فقط زمانی بررسی می‌شود که ufw یا firewalld نصب باشد؛
  اگر از فایروال بیرونی (سطح پنل ابری) استفاده می‌کنید، آن را دستی
  بررسی کنید.
- موارد **Optional** فقط برای همگام‌سازی مدرسه لازم‌اند.

### بررسی دستی پورت‌ها

از یک رایانهٔ دیگر روی شبکه:

```sh
nc -vz <نشانی سرور> 8443
nc -vzu <نشانی سرور> 1194
```

یا از بیرون، برای پورت UDP رسانه، یک تست دقیق‌تر لازم است؛ ساده‌ترین
راه این است که پس از نصب یک کلاس واقعی برگزار کنید و از بخش
«آمار/لاگ» اتصال را ببینید.

---

## ۱۱. آماده‌اید

اگر همهٔ موارد ضروری `ok` بود، بروید به:

```sh
git clone https://github.com/Maxess/bacirat-function.git
cd bacirat-function
sudo ./install.sh
```

و سپس [`INSTALL.md`](INSTALL.md) را دنبال کنید.

در صورت استفاده از بازهٔ UDP، پارامتر `-udp-range 40000-50000` را به
فرمان اجرا اضافه کنید؛ اگر از اسکریپت نصب استفاده می‌کنید، آن را در
`/etc/systemd/system/bacirat.service` وارد کنید و سپس:

```sh
sudo systemctl daemon-reload
sudo systemctl restart bacirat
```
