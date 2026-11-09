#!/usr/bin/env bash
# uninstall.sh — حذف سرویس PersianMeet (داده‌ها مگر با --purge حذف نمی‌شوند)
set -euo pipefail
INSTALL_DIR="${INSTALL_DIR:-/opt/persianmeet}"
PURGE="${1:-}"

[ "$(id -u)" -ne 0 ] && { echo "با root اجرا کنید." >&2; exit 1; }

if command -v systemctl >/dev/null; then
  systemctl stop persianmeet 2>/dev/null || true
  systemctl disable persianmeet 2>/dev/null || true
  rm -f /etc/systemd/system/persianmeet.service
  systemctl daemon-reload
  echo "[uninstall] سرویس systemd حذف شد."
fi

if [ "$PURGE" = "--purge" ]; then
  read -r -p "پوشه‌ی $INSTALL_DIR (شامل config.yml و همه‌ی فایل‌ها/ضبط‌ها) حذف شود؟ [yes/N] " ans
  if [ "$ans" = "yes" ]; then
    rm -rf "$INSTALL_DIR"
    echo "[uninstall] $INSTALL_DIR حذف شد."
  else
    echo "[uninstall] پوشه نگه داشته شد."
  fi
else
  echo "[uninstall] پوشه‌ی $INSTALL_DIR دست‌نخورده باقی ماند (برای حذف کامل: --purge)."
fi
