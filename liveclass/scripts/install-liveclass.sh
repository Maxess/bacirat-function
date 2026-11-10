#!/usr/bin/env bash
# install-liveclass.sh — نصب یک‌دستوری LiveClass روی Ubuntu/Debian (مشابه نصب PersianMeet)
# پکیج zip آماده‌ی نصب از ریپوی GitHub:
#   curl -fL -O https://raw.githubusercontent.com/Maxess/bacirat-function/arena/977de953-bacirat-function/liveclass-install.zip
#   unzip liveclass-install.zip && cd liveclass && sudo DOMAIN=… ./scripts/install-liveclass.sh
#
#   sudo ./scripts/install-liveclass.sh                          # نصب روی localhost/شبکه‌ی داخلی (بدون TLS)
#   sudo DOMAIN=class.example.com LIVEKIT_DOMAIN=livekit.class.example.com ./scripts/install-liveclass.sh
#   sudo ./scripts/install-liveclass.sh --domain class.example.com --livekit-domain livekit.class.example.com
#
# بعد از نصب:  https://DOMAIN  (یا http://IP:80 در حالت بدون دامنه)
set -euo pipefail

log()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[  ok   ]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[ error ]\033[0m %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # پوشه‌ی liveclass در همین ریپو
INSTALL_DIR="${INSTALL_DIR:-/opt/liveclass}"
DOMAIN="${DOMAIN:-}"
LIVEKIT_DOMAIN="${LIVEKIT_DOMAIN:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
OPEN_UFW="${OPEN_UFW:-1}"
WITH_LIVEKIT="${WITH_LIVEKIT:-1}"

while [ $# -gt 0 ]; do
  case "$1" in
    --domain)         DOMAIN="${2:?}"; shift 2 ;;
    --livekit-domain) LIVEKIT_DOMAIN="${2:?}"; shift 2 ;;
    --dir)            INSTALL_DIR="${2:?}"; shift 2 ;;
    --no-livekit)     WITH_LIVEKIT=0; shift ;;
    --help|-h)        grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "آرگومان ناشناخته: $1" ;;
  esac
done

[ "$(id -u)" -ne 0 ] && die "با root اجرا کنید: sudo $0"
command -v apt-get >/dev/null || die "فقط Ubuntu/Debian پشتیبانی می‌شود."

if [ -n "$DOMAIN" ] && [ -z "$LIVEKIT_DOMAIN" ]; then
  LIVEKIT_DOMAIN="livekit.${DOMAIN}"     # مثلاً livekit.class.example.com
fi

# ---------- وابستگی‌ها ----------
log "نصب وابستگی‌های سیستمی…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip curl ufw ca-certificates rsync nginx
[ -n "$DOMAIN" ] && apt-get install -y certbot python3-certbot-nginx
ok "وابستگی‌ها نصب شدند."

# ---------- کپی فایل‌ها (بدون داده و محیط مجازی) ----------
[ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ] \
  && die "$INSTALL_DIR از قبل پر است. برای به‌روزرسانی: scripts/update-liveclass.sh"
log "کپی برنامه به $INSTALL_DIR …"
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
  --exclude '.venv' --exclude 'web/node_modules' --exclude 'server/data' \
  --exclude 'server/*.db' --exclude 'server/*.db-wal' --exclude 'server/*.db-shm' \
  "$SRC_DIR/" "$INSTALL_DIR/"
[ -f "$INSTALL_DIR/web/dist/index.html" ] || die "web/dist در ریپو نیست — یک‌بار روی یک دستگاه با node اجرا کنید: cd web && npm ci && npm run build و نتایج را commit کنید."
ok "فایل‌ها کپی شدند."

# ---------- محیط مجازی پایتون ----------
log "ساخت venv و نصب requirements…"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/server/requirements.txt"
ok "venv آماده شد."

# ---------- ادمین اولیه ----------
DB_PATH="$INSTALL_DIR/server/data/liveclass.db"
if [ -f "$DB_PATH" ]; then
  log "دیتابیس از قبل هست ($DB_PATH) — ساخت ادمین رد شد."
else
  if [ -z "$ADMIN_PASSWORD" ]; then
    if [ -t 0 ]; then
      read -rsp "رمز ادمین اولیه (پیش‌فرض: admin): " ADMIN_PASSWORD; echo
    fi
    [ -z "$ADMIN_PASSWORD" ] && ADMIN_PASSWORD="$(head -c 9 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 12)" \
      && log "رمز تصادفی ادمین ساخته شد (فقط همین‌جا نمایش داده می‌شود): $ADMIN_PASSWORD"
  fi
  "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/server/run.py" init-admin --username admin --password "$ADMIN_PASSWORD" --full-name "مدیر"
  ok "ادمین ساخته شد."
fi

# ---------- LiveKit ----------
LK_KEY="devkey"; LK_SECRET="secret-secret-secret-secret-secret-12"
if [ "$WITH_LIVEKIT" = "1" ]; then
  log "نصب LiveKit Server…"
  bash "$SRC_DIR/scripts/install-livekit.sh"
  if [ -f /etc/liveclass/livekit.yaml ]; then
    LK_KEY="$(awk '/^keys:/{f=1;next} f && /^[[:space:]]/{print $1; exit}' /etc/liveclass/livekit.yaml)"
    LK_SECRET="$(awk '/^keys:/{f=1;next} f && /^[[:space:]]/{print $2; exit}' /etc/liveclass/livekit.yaml)"
  fi
  ok "LiveKit نصب شد (کلید: $LK_KEY)."
else
  log "نصب LiveKit رد شد (--no-livekit). کلیدهای dev پیش‌فرض استفاده می‌شود — برای تولید مناسب نیست!"
fi

SECRET="$(head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 40)"
PUBLIC_URL="ws://127.0.0.1:7880"
if [ -n "$DOMAIN" ]; then
  PUBLIC_URL="wss://${LIVEKIT_DOMAIN}"
else
  SERVER_IP="$(curl -s -4 --max-time 5 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
  PUBLIC_URL="ws://${SERVER_IP:-<IP-سرور>}:7880"
fi
mkdir -p /etc/liveclass
cat > /etc/liveclass/liveclass.env <<ENVEOF
# LiveClass — ساخته‌شده توسط install-liveclass.sh ($(date -u +%Y-%m-%dT%H:%M:%SZ))
LIVECLASS_DB=$INSTALL_DIR/server/data/liveclass.db
LIVECLASS_SECRET=$SECRET
LIVECLASS_LIVEKIT_URL=ws://127.0.0.1:7880
LIVECLASS_LIVEKIT_PUBLIC_URL=$PUBLIC_URL
LIVECLASS_LIVEKIT_KEY=$LK_KEY
LIVECLASS_LIVEKIT_SECRET=$LK_SECRET
LIVECLASS_MAX_PUBLISHERS=8
LIVECLASS_ROOM_MAX_PARTICIPANTS=200
ENVEOF
chmod 600 /etc/liveclass/liveclass.env
chown -R www-data:www-data "$INSTALL_DIR"

# ---------- systemd ----------
log "نصب سرویس systemd…"
sed -e "s|/opt/bacirat-function/liveclass|$INSTALL_DIR|g" \
    -e "s|/opt/liveclass|$INSTALL_DIR|g" \
    "$INSTALL_DIR/liveclass.service" > /etc/systemd/system/liveclass.service
systemctl daemon-reload
systemctl enable --now liveclass.service
ok "سرویس liveclass بالا است (status: $(systemctl is-active liveclass.service))."

# ---------- nginx + HTTPS خودکار (auto-ssl) ----------
if command -v nginx >/dev/null; then
  log "پیکربندی اولیه‌ی nginx (فاز HTTP)…"
  cat > /etc/nginx/sites-available/liveclass.conf <<NGINX
server {
    listen 80;
    server_name ${DOMAIN:-_};
    client_max_body_size 30m;  # PDF تا ۲۵MB
    location / {
        proxy_pass http://127.0.0.1:8200;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
NGINX
  if [ -n "$LIVEKIT_DOMAIN" ]; then
    cat >> /etc/nginx/sites-available/liveclass.conf <<NGINX
server {
    listen 80;
    server_name $LIVEKIT_DOMAIN;
    location / {
        proxy_pass http://127.0.0.1:7880;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 3600s;
    }
}
NGINX
  fi
  ln -sf /etc/nginx/sites-available/liveclass.conf /etc/nginx/sites-enabled/liveclass.conf
  rm -f /etc/nginx/sites-enabled/default
  nginx -t && systemctl reload nginx
  ok "nginx پیکربندی شد (HTTP)."

  if [ -n "$DOMAIN" ]; then
    log "صدور گواهی و فعال‌سازی HTTPS (auto-ssl)…"
    if ! DOMAIN="$DOMAIN" LIVEKIT_DOMAIN="$LIVEKIT_DOMAIN" bash "$SCRIPT_DIR/auto-ssl.sh"; then
      log "هشدار: auto-ssl کامل نشد. بعد از رفع DNS/فایروال دوباره بزنید:"
      log "  sudo DOMAIN=$DOMAIN LIVEKIT_DOMAIN=$LIVEKIT_DOMAIN $SCRIPT_DIR/auto-ssl.sh"
    fi
  else
    log "دامنه داده نشد — HTTPS فعال نشد؛ دوربین/میکروفون فقط روی localhost کار می‌کند."
  fi
fi

# ---------- فایروال ----------
if [ "$OPEN_UFW" = "1" ] && command -v ufw >/dev/null; then
  log "تنظیم ufw…"
  ufw allow 22/tcp || true
  ufw allow 80/tcp || true
  ufw allow 443/tcp || true
  ufw allow 7881/tcp || true           # LiveKit TCP fallback
  ufw allow 50000:60000/udp || true    # پورت‌های مدیا WebRTC
  [ -z "$DOMAIN" ] && ufw allow 7880/tcp || true   # فقط حالت بدون دامنه
  ok "ufw تنظیم شد."
fi

cat <<MSG

$(ok "نصب کامل شد.")

  آدرس برنامه :  ${DOMAIN:+https://$DOMAIN}${DOMAIN:-http://$(curl -s -4 ifconfig.me 2>/dev/null || echo '<IP-سرور>')/}
  ادمین       :  admin / ${ADMIN_PASSWORD:-<رمزی که خودتان دادید یا قبلاً ساختید>}
  داده‌ها     :  $INSTALL_DIR/server/data/liveclass.db
  سرویس‌ها   :  systemctl status liveclass livekit
  لاگ‌ها       :  journalctl -u liveclass -f
  بکاپ        :  sudo $SRC_DIR/scripts/backup-liveclass.sh

مهم: دوربین و میکروفون فقط در «زمینه‌ی امن» (HTTPS یا localhost) کار می‌کنند.
برای استفاده‌ی واقعی روی اینترنت حتماً با --domain و گواهی TLS نصب کنید.
MSG
