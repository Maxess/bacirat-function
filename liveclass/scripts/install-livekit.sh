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
