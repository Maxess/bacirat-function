#!/usr/bin/env bash
# update.sh — به‌روزرسانی PersianMeet با بکاپ خودکار و حفظ config.yml
# استفاده:
#   sudo VERSION=0.27.0 ./deploy/update.sh
#   sudo ./deploy/update.sh --from-file /path/to/persianmeet-linux-v0.27.0.zip
set -euo pipefail

log()  { printf '\033[1;34m[update]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[  ok  ]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error ]\033[0m %s\n' "$*" >&2; exit 1; }

INSTALL_DIR="${INSTALL_DIR:-/opt/persianmeet}"
VERSION="${VERSION:-}"
FROM_FILE=""
DOWNLOAD_URL="${DOWNLOAD_URL:-}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/persianmeet}"

while [ $# -gt 0 ]; do
  case "$1" in
    --from-file) FROM_FILE="${2:?need a file}"; shift 2 ;;
    --version)   VERSION="${2:?need a version}"; shift 2 ;;
    --dir)       INSTALL_DIR="${2:?need a dir}"; shift 2 ;;
    --url)       DOWNLOAD_URL="${2:?need a url}"; shift 2 ;;
    --help|-h)   grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "آرگومان ناشناخته: $1" ;;
  esac
done

[ "$(id -u)" -ne 0 ] && die "با root یا sudo اجرا کنید."
[ -d "$INSTALL_DIR" ] || die "نصبی در $INSTALL_DIR پیدا نشد (ابتدا install.sh را اجرا کنید)."

# --- بکاپ خودکار قبل از هر کاری ---
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_PATH="$BACKUP_DIR/pre-update-$STAMP.tar.gz"
log "بکاپ گرفتن از $INSTALL_DIR → $BACKUP_PATH"
tar -czf "$BACKUP_PATH" -C "$(dirname "$INSTALL_DIR")" "$(basename "$INSTALL_DIR")"
ok "بکاپ ذخیره شد."

# --- دانلود نسخه‌ی جدید ---
if [ -z "$VERSION" ] && [ -z "$FROM_FILE" ] && [ -z "$DOWNLOAD_URL" ]; then
  die "نسخه‌ی جدید را بدهید: VERSION=0.27.0 ./deploy/update.sh  یا  --from-file <zip>"
fi
[ -z "$DOWNLOAD_URL" ] && [ -n "$VERSION" ] && DOWNLOAD_URL="https://app2.learn100.ir/upload/dl/persianmeet-linux-v${VERSION}.zip"

WORKDIR="$(mktemp -d)"; trap 'rm -rf "$WORKDIR"' EXIT
ZIP_PATH="$WORKDIR/new.zip"
if [ -n "$FROM_FILE" ]; then
  cp "$FROM_FILE" "$ZIP_PATH"
else
  log "دانلود $DOWNLOAD_URL"
  wget -q --show-progress -O "$ZIP_PATH" "$DOWNLOAD_URL" \
    || wget -q --show-progress -O "$ZIP_PATH" "https://app.learn100.ir/upload/dl/persianmeet-linux-v${VERSION}.zip" \
    || die "دانلود ناموفق بود؛ پکیج را دستی بگیرید و با --from-file بدهید."
fi
unzip -t "$ZIP_PATH" >/dev/null || die "zip خراب است."

# --- توقف سرویس و جایگذاری (config.yml حفظ می‌شود) ---
if command -v systemctl >/dev/null && systemctl list-unit-files | grep -q '^persianmeet.service'; then
  log "توقف سرویس …"
  systemctl stop persianmeet || true
fi

CONFIG_BAK=""
[ -f "$INSTALL_DIR/config.yml" ] && { CONFIG_BAK="$WORKDIR/config.yml.keep"; cp "$INSTALL_DIR/config.yml" "$CONFIG_BAK"; }

TMP_EXTRACT="$(mktemp -d)"
unzip -q "$ZIP_PATH" -d "$TMP_EXTRACT"
INNER="$(find "$TMP_EXTRACT" -mindepth 1 -maxdepth 1 -type d | head -1)"
SRC="$TMP_EXTRACT"; [ -n "$INNER" ] && [ -f "$INNER/persianmeet" ] && SRC="$INNER"

# فقط فایل‌های برنامه را جایگزین کن، خود پوشه را نگه دار تا مالکیت/مسیرها نشکنند
find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 ! -name 'config.yml' -exec rm -rf {} +
cp -a "$SRC/." "$INSTALL_DIR/"
[ -n "$CONFIG_BAK" ] && cp "$CONFIG_BAK" "$INSTALL_DIR/config.yml"
chmod +x "$INSTALL_DIR/persianmeet" || true
[ -f "$INSTALL_DIR/app/optimized" ] && chmod +x "$INSTALL_DIR/app/optimized" || true

if command -v systemctl >/dev/null && systemctl list-unit-files | grep -q '^persianmeet.service'; then
  systemctl start persianmeet
  ok "سرویس دوباره اجرا شد (status: $(systemctl is-active persianmeet || true))."
else
  log "سرویس systemd پیدا نشد؛ اجرا: cd $INSTALL_DIR && ./persianmeet &"
fi
ok "به‌روزرسانی تمام شد. اگر چیزی خراب شد، بکاپ: $BACKUP_PATH"
