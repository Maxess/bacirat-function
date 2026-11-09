#!/usr/bin/env bash
# update-liveclass.sh — به‌روزرسانی نصب موجود با نسخه‌ی فعلیِ همین ریپو (بدون node، چون web/dist در git است)
# استفاده: sudo ./scripts/update-liveclass.sh [--dir /opt/liveclass]
set -euo pipefail
log() { printf '\033[1;34m[update]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[ error ]\033[0m %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="${1:-/opt/liveclass}"
[ "${1:-}" = "--dir" ] && INSTALL_DIR="${2:?}"
[ "$(id -u)" -ne 0 ] && die "با root اجرا کنید."
[ -d "$INSTALL_DIR" ] || die "$INSTALL_DIR نصب نیست؛ اول install-liveclass.sh را اجرا کنید."

STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p /var/backups/liveclass
log "بکاپ سریع قبل از به‌روزرسانی…"
tar -czf "/var/backups/liveclass/pre-update-$STAMP.tar.gz" -C "$INSTALL_DIR" server/data 2>/dev/null || true

log "توقف سرویس…"
systemctl stop liveclass.service || true

log "همگام‌سازی فایل‌ها (داده حفظ می‌شود)…"
rsync -a --delete \
  --exclude '.venv' --exclude 'web/node_modules' --exclude 'server/data' \
  --exclude 'server/*.db' --exclude 'server/*.db-wal' --exclude 'server/*.db-shm' \
  "$SRC_DIR/" "$INSTALL_DIR/"

log "نصب وابستگی‌های پایتون…"
"$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/server/requirements.txt"
chown -R www-data:www-data "$INSTALL_DIR"

log "راه‌اندازی دوباره…"
systemctl start liveclass.service
systemctl is-active --quiet liveclass.service && log "به‌روزرسانی کامل شد ✔ (بکاپ: /var/backups/liveclass/pre-update-$STAMP.tar.gz)" \
  || die "سرویس بالا نیامد؛ لاگ: journalctl -u liveclass -e"
