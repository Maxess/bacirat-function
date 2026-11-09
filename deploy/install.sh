#!/usr/bin/env bash
# install.sh — نصب خودکار PersianMeet روی Ubuntu/Debian (مطابق مستندات رسمی)
# استفاده:
#   sudo DOMAIN=class.example.com ./deploy/install.sh
#   sudo ./deploy/install.sh --from-file /path/to/persianmeet-linux-v0.26.3.zip
set -euo pipefail

log()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[  ok   ]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[ error ]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- پارامترها ----------
VERSION="${VERSION:-0.26.3}"
DOMAIN="${DOMAIN:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/persianmeet}"
FROM_FILE=""
OPEN_UFW="${OPEN_UFW:-1}"
DOWNLOAD_URL="${DOWNLOAD_URL:-}"   # اگر خالی باشد، از VERSION ساخته می‌شود
SECRET="${SECRET:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --from-file) FROM_FILE="${2:?need a file}"; shift 2 ;;
    --domain)    DOMAIN="${2:?need a domain}"; shift 2 ;;
    --version)   VERSION="${2:?need a version}"; shift 2 ;;
    --dir)       INSTALL_DIR="${2:?need a dir}"; shift 2 ;;
    --url)       DOWNLOAD_URL="${2:?need a url}"; shift 2 ;;
    --help|-h)   grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "آرگومان ناشناخته: $1" ;;
  esac
done

# اگر کاربر URL دستی نداده، از VERSION بساز
[ -z "$DOWNLOAD_URL" ] && DOWNLOAD_URL="https://app2.learn100.ir/upload/dl/persianmeet-linux-v${VERSION}.zip"

[ "$(id -u)" -ne 0 ] && die "این اسکریپت را با root یا sudo اجرا کنید."
command -v apt-get >/dev/null || die "این اسکریپت فقط برای Ubuntu/Debian است."

# ---------- وابستگی‌ها ----------
log "بروزرسانی apt و نصب وابستگی‌ها (wget nano unzip mupdf-tools curl ufw)…"
apt-get update -y
apt-get install -y wget nano unzip mupdf-tools curl ufw ca-certificates
ok "وابستگی‌ها نصب شدند."

# ---------- هشدار در صورت نصب قبلی ----------
if [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
  die "پوشه‌ی $INSTALL_DIR از قبل پر است. برای به‌روزرسانی از deploy/update.sh استفاده کنید."
fi

# ---------- آماده‌سازی SECRET ----------
if [ -z "$SECRET" ]; then
  SECRET="$(head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32)"
fi

# ---------- دانلود یا پذیرش فایل محلی ----------
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
ZIP_PATH="$WORKDIR/persianmeet-linux-v${VERSION}.zip"

if [ -n "$FROM_FILE" ]; then
  [ -f "$FROM_FILE" ] || die "فایل پیدا نشد: $FROM_FILE"
  cp "$FROM_FILE" "$ZIP_PATH"
  log "استفاده از فایل محلی: $FROM_FILE"
else
  log "دانلود $DOWNLOAD_URL"
  if ! wget -q --show-progress -O "$ZIP_PATH" "$DOWNLOAD_URL"; then
    log "لینک app2.learn100.ir جواب نداد؛ تلاش با app.learn100.ir …"
    wget -q --show-progress -O "$ZIP_PATH" "https://app.learn100.ir/upload/dl/persianmeet-linux-v${VERSION}.zip" \
      || die "دانلود از هر دو لینک رسمی ناموفق بود. پکیج را دستی دانلود و با --from-file بدهید."
  fi
fi
unzip -t "$ZIP_PATH" >/dev/null || die "فایل zip خراب است."
ok "پکیج آماده است ($(du -h "$ZIP_PATH" | cut -f1))."

# ---------- استخراج ----------
mkdir -p "$INSTALL_DIR"
TMP_EXTRACT="$(mktemp -d)"
unzip -q "$ZIP_PATH" -d "$TMP_EXTRACT"
# پوشه‌ی سطح‌بالای داخل zip (معمولاً persianmeet/) را پیدا کن و محتوایش را به INSTALL_DIR کپی کن
INNER="$(find "$TMP_EXTRACT" -mindepth 1 -maxdepth 1 -type d | head -1)"
if [ -n "$INNER" ] && [ -f "$INNER/persianmeet" ]; then
  cp -a "$INNER/." "$INSTALL_DIR/"
else
  cp -a "$TMP_EXTRACT/." "$INSTALL_DIR/"
fi
rm -rf "$TMP_EXTRACT"
cd "$INSTALL_DIR"

[ -f "$INSTALL_DIR/persianmeet" ] || die "فایل باینری 'persianmeet' بعد از استخراج پیدا نشد — ساختار zip تغییر کرده؛ با --dir و محتویات واقعی تنظیم کنید."
chmod +x persianmeet || true
[ -f "app/optimized" ] && chmod +x app/optimized || true
ok "استخراج در $INSTALL_DIR انجام شد."

# ---------- config.yml ----------
if [ ! -f "$INSTALL_DIR/config.yml" ]; then
  log "ساخت config.yml …"
  cat > "$INSTALL_DIR/config.yml" <<YAML
# PersianMeet configuration — ساخته‌شده توسط bacirat-function/deploy/install.sh
# مستندات: docs/00-official-docs.md و deploy/config.yml.example
auto_ssl: true
secret: "$SECRET"
YAML
  chmod 600 "$INSTALL_DIR/config.yml"
  ok "config.yml ساخته شد (auto_ssl: true، secret تصادفی تنظیم شد)."
else
  log "config.yml از قبل وجود داشت؛ دست‌نخورده نگه داشته شد."
fi

# ---------- systemd ----------
if command -v systemctl >/dev/null; then
  log "نصب سرویس systemd …"
  sed -e "s|/opt/persianmeet|$INSTALL_DIR|g" \
      "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/persianmeet.service" \
      > /etc/systemd/system/persianmeet.service
  systemctl daemon-reload
  systemctl enable --now persianmeet.service
  ok "سرویس persianmeet.service فعال شد (status: $(systemctl is-active persianmeet.service))."
else
  log "systemd پیدا نشد؛ برنامه را دستی اجرا کنید: cd $INSTALL_DIR && ./persianmeet &"
fi

# ---------- فایروال ----------
if [ "$OPEN_UFW" = "1" ] && command -v ufw >/dev/null; then
  log "تنظیم ufw …"
  ufw allow 22/tcp   || true
  ufw allow 80/tcp   || true
  ufw allow 443/tcp  || true
  # WebRTC/SFU روی UDP؛ بعد از نصب با: ss -ulpn  پورت‌های دقیق را ببینید و اینجا اضافه کنید
  # ufw allow 5000:5200/udp || true
  ok "ufw تنظیم شد. (بازه‌ی UDP لازم را بعد از مشاهده‌ی پورت‌ها با ss -ulpn اضافه کنید)"
fi

cat <<MSG

$(ok "نصب کامل شد.")

قدم‌های بعدی:
  1) رکورد A دامنه را به این سرور بزنید:  ${DOMAIN:-<class.example.com>} → $(curl -s -4 ifconfig.me 2>/dev/null || echo '<IP-سرور>')
  2) لاگ صدور گواهی را ببینید:          journalctl -u persianmeet -f
  3) وارد پنل شوید:                      https://${DOMAIN:-<class.example.com>}   (admin / 1111)
  4) فوراً رمز ادمین را عوض کنید.
  5) secret API شما (برای MeetList/ActiveMeets و ...):
     ${SECRET}
     (داخل ${INSTALL_DIR}/config.yml هم هست)
MSG
