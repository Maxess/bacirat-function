#!/usr/bin/env bash
# backup.sh — بکاپ از config.yml و داده‌های PersianMeet (فایل‌ها/ضبط‌ها)
# استفاده: sudo ./deploy/backup.sh [مقصد]     # پیش‌فرض /var/backups/persianmeet
set -euo pipefail
INSTALL_DIR="${INSTALL_DIR:-/opt/persianmeet}"
DEST="${1:-/var/backups/persianmeet}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
OUT="$DEST/persianmeet-backup-$STAMP.tar.gz"

[ -d "$INSTALL_DIR" ] || { echo "پوشه‌ی نصب $INSTALL_DIR وجود ندارد." >&2; exit 1; }

echo "[backup] ساخت $OUT …"
# کل پوشه شامل config و wwwroot/files؛ حجم ممکن است زیاد باشد
tar -czf "$OUT" -C "$(dirname "$INSTALL_DIR")" "$(basename "$INSTALL_DIR")"
echo "[backup] انجام شد: $OUT"
echo "[backup] برای بازگردانی: tar -xzf $OUT -C /opt && systemctl restart persianmeet"
