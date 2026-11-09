#!/usr/bin/env bash
# auto-ssl.sh — معادل «auto_ssl» ی PersianMeet برای LiveClass (صدور و تمدید خودکار HTTPS)
#
# استفاده:
#   sudo DOMAIN=class.example.com ./scripts/auto-ssl.sh
#   sudo DOMAIN=class.example.com LIVEKIT_DOMAIN=livekit.class.example.com ./scripts/auto-ssl.sh
#   sudo LETSENCRYPT_EMAIL=me@example.com DOMAIN=class.example.com ./scripts/auto-ssl.sh
#   sudo STAGED=1 DOMAIN=class.example.com ./scripts/auto-ssl.sh    # تست با محیط آزمایشی Let's Encrypt (بدون مصرف سهمیه)
#
# اسکریپت idempotent است: هر بار اجرا کنید، چه گواهی موجود باشد چه نباشد، خروجی نهایی یکی است:
#   HTTP→HTTPS redirect + گواهی معتبر + HSTS + secure cookie + wss:// برای LiveKit + تمدید خودکار.
set -euo pipefail

log()  { printf '\033[1;34m[auto-ssl]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[  ok    ]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[  error ]\033[0m %s\n' "$*" >&2; exit 1; }

DOMAIN="${DOMAIN:-}"
LIVEKIT_DOMAIN="${LIVEKIT_DOMAIN:-}"
EMAIL="${LETSENCRYPT_EMAIL:-}"
WEBROOT=/var/www/letsencrypt

[ "$(id -u)" -ne 0 ] && die "با root اجرا کنید: sudo DOMAIN=… $0"
command -v apt-get >/dev/null || die "فقط Ubuntu/Debian پشتیبانی می‌شود."
[ -n "$DOMAIN" ] || die "DOMAIN را بدهید: sudo DOMAIN=class.example.com $0"
[ -z "$LIVEKIT_DOMAIN" ] && LIVEKIT_DOMAIN="livekit.${DOMAIN}"

# ---------- وابستگی‌ها ----------
export DEBIAN_FRONTEND=noninteractive
apt-get update -y >/dev/null
apt-get install -y -qq nginx certbot ca-certificates >/dev/null
mkdir -p "$WEBROOT"

# ---------- فاز ۱: فقط HTTP (برای چالش ACME) ----------
write_http_conf() {
  cat > /etc/nginx/sites-available/liveclass.conf <<CONF
# LiveClass — ساخته‌شده توسط auto-ssl.sh (فاز HTTP/ACME)
server {
    listen 80;
    server_name ${DOMAIN} ${LIVEKIT_DOMAIN};
    location /.well-known/acme-challenge/ { root ${WEBROOT}; }
    location / { return 301 https://\$host\$request_uri; }
}
CONF
}

write_http_conf
ln -sf /etc/nginx/sites-available/liveclass.conf /etc/nginx/sites-enabled/liveclass.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
ok "پیکربندی HTTP (چالش ACME) فعال شد."

# ---------- فاز ۲: صدور/بررسی گواهی ----------
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
CERTBOT_ARGS=(certonly --webroot -w "$WEBROOT" -d "$DOMAIN" -d "$LIVEKIT_DOMAIN"
              --non-interactive --agree-tos --keep-until-expiring)
if [ -n "$EMAIL" ]; then CERTBOT_ARGS+=(--email "$EMAIL"); else CERTBOT_ARGS+=(--register-unsafely-without-email); fi
[ "${STAGED:-0}" = "1" ] && CERTBOT_ARGS+=(--staging) && log "حالت آزمایشی (staging) فعال است."

if [ ! -d "$CERT_DIR" ] || ! openssl x509 -checkend 2592000 -noout -in "$CERT_DIR/fullchain.pem" >/dev/null 2>&1; then
  log "صدور گواهی برای ${DOMAIN} و ${LIVEKIT_DOMAIN}…"
  certbot "${CERTBOT_ARGS[@]}"
else
  ok "گواهی موجود و بیش از ۳۰ روز اعتبار دارد — استفاده مجدد."
fi

# ---------- فاز ۳: پیکربندی نهایی HTTPS ----------
write_full_conf() {
  cat > /etc/nginx/sites-available/liveclass.conf <<CONF
# LiveClass — ساخته‌شده توسط auto-ssl.sh — HTTP→HTTPS + HSTS + WebSocket (LiveKit)
# HTTP: فقط برای چالش ACME و ریدایرکت
server {
    listen 80;
    server_name ${DOMAIN} ${LIVEKIT_DOMAIN};
    location /.well-known/acme-challenge/ { root ${WEBROOT}; }
    location / { return 301 https://\$host\$request_uri; }
}

# اپ و API
server {
    listen 443 ssl http2;
    server_name ${DOMAIN};

    ssl_certificate     ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:LCCACHE:10m;
    ssl_session_timeout 1d;

    client_max_body_size 30m;   # PDF تا ۲۵MB

    add_header Strict-Transport-Security "max-age=15768000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    location / {
        proxy_pass http://127.0.0.1:8200;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }
}

# سیگنالینگ LiveKit (WebSocket) — مدیا (UDP) از nginx رد نمی‌شود
server {
    listen 443 ssl http2;
    server_name ${LIVEKIT_DOMAIN};

    ssl_certificate     ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:LCCACHE:10m;
    ssl_session_timeout 1d;

    add_header Strict-Transport-Security "max-age=15768000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;

    location / {
        proxy_pass http://127.0.0.1:7880;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
CONF
}

write_full_conf
nginx -t && systemctl reload nginx
ok "HTTPS فعال شد: https://${DOMAIN}  و  wss://${LIVEKIT_DOMAIN}"

# ---------- فاز ۴: تمدید خودکار ----------
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/liveclass-reload-nginx <<'HOOK'
#!/bin/sh
# با هر تمدید موفق certbot اجرا می‌شود
nginx -t && systemctl reload nginx
HOOK
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/liveclass-reload-nginx
systemctl enable --now certbot.timer >/dev/null 2>&1 || true
ok "تمدید خودکار فعال است (certbot.timer + deploy-hook ری‌لود nginx)."

# ---------- فاز ۵: هماهنگی اپ (secure cookie + wss) ----------
ENV_FILE=/etc/liveclass/liveclass.env
if [ -f "$ENV_FILE" ]; then
  cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%s)"
  set_env() {  # set_env KEY VALUE — جایگزینی خط موجود یا افزودن
    local k="$1" v="$2"
    if grep -q "^${k}=" "$ENV_FILE"; then
      sed -i "s|^${k}=.*|${k}=${v}|" "$ENV_FILE"
    else
      printf '%s=%s\n' "$k" "$v" >> "$ENV_FILE"
    fi
  }
  set_env LIVECLASS_SECURE_COOKIES 1
  set_env LIVECLASS_LIVEKIT_PUBLIC_URL "wss://${LIVEKIT_DOMAIN}"
  chmod 600 "$ENV_FILE"
  ok "liveclass.env: LIVECLASS_SECURE_COOKIES=1 و LIVECLASS_LIVEKIT_PUBLIC_URL=wss://${LIVEKIT_DOMAIN}"
  if systemctl list-unit-files liveclass.service >/dev/null 2>&1; then
    systemctl restart liveclass.service || log "هشدار: ری‌استارت liveclass ناموفق بود؛ دستی اجرا کنید: systemctl restart liveclass"
    ok "سرویس liveclass ری‌استارت شد (cookie امن فعال)."
  fi
else
  log "هشدار: $ENV_FILE موجود نیست؛ اگر نصب انجام نشده، اول install-liveclass.sh را اجرا کنید."
fi

# ---------- اعتبارسنجی ----------
echo
log "بررسی نهایی…"
for U in "http://${DOMAIN}/api/config" "https://${DOMAIN}/api/config"; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$U" || true)
  printf '  %s → %s\n' "$U" "${CODE:-failed}"
done
if [ -d "$CERT_DIR" ]; then
  openssl x509 -in "$CERT_DIR/fullchain.pem" -noout -dates -subject || true
fi
echo
ok "auto-ssl کامل شد. دوربین و میکروفون حالا در https://${DOMAIN} در دسترس مرورگر خواهند بود."
