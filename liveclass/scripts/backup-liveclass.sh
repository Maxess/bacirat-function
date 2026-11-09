#!/usr/bin/env bash
# backup-liveclass.sh — بکاپ از دیتابیس، برند و تنظیمات LiveClass
# استفاده: sudo ./scripts/backup-liveclass.sh [مقصد]     # پیش‌فرض /var/backups/liveclass
set -euo pipefail
INSTALL_DIR="${INSTALL_DIR:-/opt/liveclass}"
DEST="${1:-/var/backups/liveclass}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
OUT="$DEST/liveclass-backup-$STAMP.tar.gz"

[ -d "$INSTALL_DIR" ] || { echo "پوشه‌ی نصب $INSTALL_DIR وجود ندارد." >&2; exit 1; }

echo "[backup] ساخت $OUT …"
tar -czf "$OUT" \
  -C "$INSTALL_DIR" server/data \
  -C /etc liveclass 2>/dev/null || tar -czf "$OUT" -C "$INSTALL_DIR" server/data
echo "[backup] انجام شد: $OUT"
echo "[backup] بازگردانی: systemctl stop liveclass && tar -xzf $OUT -C / && systemctl start liveclass"
