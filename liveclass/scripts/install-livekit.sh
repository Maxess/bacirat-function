#!/usr/bin/env bash
# نصب LiveKit Server روی سرور اوبونتو/دبیان (از GitHub Releases)
# استفاده: sudo ./scripts/install-livekit.sh
set -euo pipefail

VERSION="${VERSION:-1.13.11}"
INSTALL_DIR="${INSTALL_DIR:-/opt/liveclass}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[ "$(id -u)" -ne 0 ] && { echo "با root اجرا کنید: sudo $0"; exit 1; }
mkdir -p "$INSTALL_DIR/bin" /etc/liveclass

URL="https://github.com/livekit/livekit/releases/download/v${VERSION}/livekit_${VERSION}_linux_amd64.tar.gz"
echo "[livekit] دانلود $URL"
curl -fL --retry 3 -o /tmp/livekit.tar.gz "$URL"
tar xzf /tmp/livekit.tar.gz -C /tmp livekit-server
install -m 0755 /tmp/livekit-server "$INSTALL_DIR/bin/livekit-server"

# کلید/سِکرت واقعی اگر وجود ندارد بساز
KEY_FILE=/etc/liveclass/livekit.yaml
if [ ! -f "$KEY_FILE" ]; then
  API_KEY="lk_$(head -c 8 /dev/urandom | base64 | tr -dc a-z0-9 | head -c 12)"
  API_SECRET="$(head -c 36 /dev/urandom | base64 | tr -dc a-z0-9A-Z | head -c 48)"
  cat > "$KEY_FILE" <<YAML
# LiveKit — ساخته‌شده توسط install-livekit.sh
port: 7880
rtc:
  port_range_start: 50000
  port_range_end: 60000
  use_external_ip: true
keys:
  ${API_KEY}: ${API_SECRET}
YAML
  chmod 600 "$KEY_FILE"
  echo "[livekit] کلید جدید ساخته شد ($KEY_FILE)"
  echo "[livekit] API_KEY=$API_KEY"
  echo "[livekit] API_SECRET=$API_SECRET"
  echo "[livekit] این دو مقدار را در /etc/liveclass/liveclass.env بنویسید."
else
  echo "[livekit] $KEY_FILE از قبل هست؛ دست‌نخورده می‌ماند."
fi

cat > /etc/systemd/system/livekit.service <<UNIT
[Unit]
Description=LiveKit SFU (LiveClass)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$INSTALL_DIR/bin/livekit-server --config $KEY_FILE
Restart=always
RestartSec=3
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now livekit.service
echo "[livekit] وضعیت: $(systemctl is-active livekit.service)"
echo "[livekit] پورت‌ها: ss -tlnp | grep -E '7880|7881'"

# ============================================================================
# ضبط جلسه (LiveKit Egress) — اختیاری و با فلگ --with-egress
# نکات حیاتی:
#   - Egress فقط در کانتینر داکر کار می‌کند (Chrome + GStreamer داخل ایمیج هست)
#   - Redis الزامی است (هم‌زمان بین LiveKit و Egress مشترک است)
#   - پیش‌فرض در LiveClass خاموش است: LIVECLASS_EGRESS_ENABLE=1 را در env بگذارید
#   - روی سرور ضعیف ضبط توصیه نمی‌شود (حدود ۳ برابر CPU بیشتر؛ حداقل ۲ هسته‌ی آزاد)
# ============================================================================
if printf '%s' "$*" | grep -q -- '--with-egress'; then
  echo "[egress] نصب اختیاری ضبط جلسه…"
  command -v docker >/dev/null || { echo "[egress] خطا: داکر نصب نیست. اول داکر را نصب کنید."; exit 1; }
  if ! command -v redis-server >/dev/null; then
    echo "[egress] نصب redis…"
    apt-get update -qq && apt-get install -y -qq redis-server
  fi
  systemctl enable --now redis-server

  # LiveKit را با redis راه‌اندازی مجدد کن (اگر از قبل redis ندارد)
  if ! grep -q '^redis:' "$KEY_FILE"; then
    printf 'redis:\n  address: 127.0.0.1:6379\n' >> "$KEY_FILE"
    echo "[egress] redis به $KEY_FILE اضافه شد؛ livekit را با redis بالا می‌آوریم…"
    systemctl restart livekit.service
  fi

  # کلیدها را از livekit.yaml بخوان
  LIVEKIT_KEY=$(awk '/^keys:/ {getline; print $1}' "$KEY_FILE" | tr -d ':')
  LIVEKIT_SECRET=$(awk '/^keys:/ {getline; print $2}' "$KEY_FILE")
  RECORD_DIR="${LIVECLASS_RECORDINGS_DIR:-$INSTALL_DIR/recordings}"
  mkdir -p "$RECORD_DIR" && chmod 0777 "$RECORD_DIR"

  cat > /etc/liveclass/egress.yaml <<YAML
# LiveKit Egress — ساخته‌شده توسط install-livekit.sh
api_key: ${LIVEKIT_KEY}
api_secret: ${LIVEKIT_SECRET}
ws_url: ws://127.0.0.1:7880
insecure: true
redis:
  address: 127.0.0.1:6379
log_level: info
YAML
  chmod 600 /etc/liveclass/egress.yaml

  docker pull livekit/egress:latest
  docker rm -f liveclass-egress 2>/dev/null || true
  # RECORD_DIR در همان مسیر داخل کانتینر ماونت می‌شود تا filepath درخواست عیناً معتبر باشد
  # network host → همان 127.0.0.1 زنده است؛ redis فقط روی لوپ‌بک می‌ماند (ایمن)
  docker run -d --name liveclass-egress --restart unless-stopped \
      --network host \
      -e EGRESS_CONFIG_FILE=/out/egress.yaml \
      -v /etc/liveclass:/out:ro -v "$RECORD_DIR":"$RECORD_DIR" \
      livekit/egress:latest

  cat > /etc/liveclass/recordings.env <<ENV
# دو خط زیر را در /etc/liveclass/liveclass.env بگذارید و liveclass را restart کنید:
LIVECLASS_EGRESS_ENABLE=1
LIVECLASS_RECORDINGS_DIR=$RECORD_DIR
ENV
  echo "[egress] آماده است. ضبط‌ها در $RECORD_DIR ذخیره می‌شوند."
  echo "[egress] مهم: محتوای /etc/liveclass/recordings.env را به /etc/liveclass/liveclass.env"
  echo "         اضافه و سپس: systemctl restart liveclass"
  echo "[egress] redis و livekit روی همان سرور هستند و فقط شبکه‌ی محلی را می‌بینند."
  echo "[egress] نیازمند CPU آزاد؛ روی سرورهای ضعیف ضبط توصیه نمی‌شود."
else
  echo "[egress] رد شد (برای نصب ضبط: $0 --with-egress). ضبط پیش‌فرض خاموش است."
fi
