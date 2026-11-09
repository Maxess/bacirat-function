#!/usr/bin/env bash
# uninstall-liveclass.sh — حذف LiveClass (با بکاپ خودکار)
# استفاده: sudo ./scripts/uninstall-liveclass.sh [--yes] [--dir /opt/liveclass]
set -euo pipefail
CONFIRM=0
INSTALL_DIR="/opt/liveclass"
for arg in "$@"; do
  case "$arg" in
    --yes) CONFIRM=1 ;;
    --dir) shift_dir=1 ;;
    *) [ "${shift_dir:-0}" = "1" ] && INSTALL_DIR="$arg" && shift_dir=0 ;;
  esac
done
[ "$(id -u)" -ne 0 ] && { echo "با root اجرا کنید." >&2; exit 1; }

if [ "$CONFIRM" != "1" ]; then
  read -rp "LiveClass از $INSTALL_DIR و سرویس‌هایش حذف شود؟ (yes/no) " ans
  [ "$ans" = "yes" ] || { echo "لغو شد."; exit 0; }
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p /var/backups/liveclass
tar -czf "/var/backups/liveclass/pre-uninstall-$STAMP.tar.gz" -C "$INSTALL_DIR" server/data 2>/dev/null || true
echo "[uninstall] بکاپ داده: /var/backups/liveclass/pre-uninstall-$STAMP.tar.gz"

systemctl disable --now liveclass.service 2>/dev/null || true
systemctl disable --now livekit.service 2>/dev/null || true
rm -f /etc/systemd/system/liveclass.service /etc/systemd/system/livekit.service
systemctl daemon-reload
rm -f /etc/nginx/sites-enabled/liveclass.conf /etc/nginx/sites-available/liveclass.conf
nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
rm -rf "$INSTALL_DIR"
echo "[uninstall] انجام شد. /etc/liveclass و بکاپ‌ها دست‌نخورده ماندند."
