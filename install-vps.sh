#!/usr/bin/env bash
# Fetch or copy the same verified release; no building and no online dependencies on the VPS.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
unset BASH_ENV ENV CDPATH TAR_OPTIONS
umask 077

# This pin names the exact archive that passed the packaged classroom rehearsal.
readonly VERSION=1.3.0
readonly SHA256=282f266d3df3d613a5209be41798764b61fb7d42dc33efdc76001f29ca720255
readonly BUNDLE=baci-classroom-vps-offline
readonly ARCHIVE="${BUNDLE}-v${VERSION}-ubuntu24.04-amd64.tar.gz"
readonly URL="https://github.com/Maxess/bacirat-function/releases/download/vps-offline-v${VERSION}/${ARCHIVE}"

fail() { printf 'Baci VPS: %s\n' "$*" >&2; exit 1; }
help() {
    cat <<HELP
Baci VPS $VERSION — Ubuntu 24.04 LTS amd64, systemd, cgroup v2.
  bash install-vps.sh --bundle ./$ARCHIVE --check
  bash install-vps.sh --bundle ./$ARCHIVE
  bash install-vps.sh --mirror 'https://mirror.example.ir/path/$ARCHIVE'
  bash install-vps.sh --profile compact --tls auto
  bash install-vps.sh --tls files --cert ./fullchain.pem --key ./privkey.pem
A local archive named $ARCHIVE in the current directory is used before GitHub.
The same SHA-256 is required for a local file, a mirror, and GitHub Releases.
Default (--profile auto): install recording — full at 1792 MiB visible RAM, otherwise compact.
Compact needs 448 MiB visible RAM; below 896 MiB it also needs 512 MiB active swap.
Choose --profile live explicitly only if you want a live-only install without recording.
TLS defaults to the local PEM pair when present, otherwise public ACME on TCP 443.
A failed certificate check never falls back to self-signed or insecure TLS.
Installer flags such as --site, --media, --ipv4 and --add-recorder are forwarded.
Run as the SSH user; sudo is invoked only for the verified local installer.
HELP
}

bundle= mirror= profile=auto has_profile=false
args=()
while (( $# )); do
    case $1 in
        --help|-h) help; exit 0 ;;
        --bundle|--mirror|--profile)
            flag=$1
            (( $# >= 2 )) && [[ -n "$2" && "$2" != --* ]] || fail "Missing value for $flag."
            value=$2
            shift 2 ;;
        --bundle=*|--mirror=*|--profile=*)
            flag=${1%%=*}; value=${1#*=}; shift
            [[ -n "$value" ]] || fail "Missing value for $flag." ;;
        *) args+=("$1"); shift; continue ;;
    esac
    case $flag in
        --bundle) [[ -z "$bundle" ]] || fail 'Specify --bundle only once.'; bundle=$value ;;
        --mirror) [[ -z "$mirror" ]] || fail 'Specify --mirror only once.'; mirror=$value ;;
        --profile)
            [[ "$has_profile" == false ]] || fail 'Specify --profile only once.'
            case $value in
                live|compact|full|auto) profile=$value; has_profile=true ;;
                *) fail 'Choose --profile live, compact, full or auto.' ;;
            esac ;;
    esac
done
[[ -z "$bundle" || -z "$mirror" ]] || fail 'Use either --bundle or --mirror, not both.'
if [[ -n "$mirror" ]]; then
    [[ "$mirror" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?/[^[:space:]@#?]+$ ]] \
        || fail 'Give the complete HTTPS URL of the archive, without credentials, query or fragment.'
fi

grep -qx 'ID=ubuntu' /etc/os-release && grep -Eq '^VERSION_ID="?24\.04"?$' /etc/os-release \
    && [[ $(uname -m) == x86_64 && $(dpkg --print-architecture) == amd64 ]] \
    || fail 'Ubuntu 24.04 LTS amd64 لازم است؛ این آرشیو برای سیستم یا معماری دیگری نیست.'
if (( EUID != 0 )); then
    command -v sudo >/dev/null || fail 'این حساب SSH باید دسترسی sudo داشته باشد.'
fi

if [[ "$profile" == auto ]]; then
    # Do not silently install live-only on a small VPS. That would hide an unmet
    # requirement from a school expecting recording and group delivery.
    read -r ram swap < <(awk '/^MemTotal:/ {r=$2} /^SwapTotal:/ {s=$2} END {print r+0,s+0}' /proc/meminfo)
    if (( ram >= 1835008 )); then
        profile=full
    elif (( ram >= 458752 )); then
        profile=compact
        if (( ram < 917504 && swap < 512000 )); then
            fail 'ضبط روی این RAM به ۵۱۲ MiB swap فعال نیاز دارد. راهنمای بسته را ببین؛ ضبط به‌طور خودکار حذف نشد.'
        fi
    else
        fail 'برای ضبط حداقل ۴۴۸ MiB RAM قابل‌مشاهده لازم است. اگر فقط کلاس زنده می‌خواهی، --profile live را صریح بزن.'
    fi
    printf 'پروفایل ضبط انتخاب شد: %s (MemTotal=%s KiB، SwapTotal=%s KiB)\n' "$profile" "$ram" "$swap"
fi
args+=(--profile "$profile")

if [[ -z "$bundle" && -z "$mirror" && ( -e "$ARCHIVE" || -L "$ARCHIVE" ) ]]; then
    bundle="./$ARCHIVE"
fi
work=$(mktemp -d -- "${TMPDIR:-/var/tmp}/baci-vps-github.XXXXXXXX") \
    || fail 'ساخت پوشهٔ موقت ممکن نیست؛ فضای خالی و دسترسی را بررسی کن.'
trap 'rm -rf -- "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ -n "$bundle" ]]; then
    [[ -f "$bundle" && ! -L "$bundle" && -r "$bundle" ]] || fail 'فایل عادی آرشیو محلی پیدا نشد یا قابل خواندن نیست.'
    cp --reflink=auto -- "$bundle" "$work/$ARCHIVE" || fail 'کپی بستهٔ محلی نشد؛ فضای دیسک را بررسی کن.'
    printf 'بستهٔ محلی نسخهٔ %s؛ دانلودی روی VPS انجام نمی‌شود.\n' "$VERSION"
else
    command -v curl >/dev/null || fail 'برای دانلود curl لازم است؛ آرشیو معتبر را با SFTP منتقل کن و --bundle بده.'
    progress=(--silent)
    if [[ -t 2 ]]; then progress=(--progress-bar); fi
    printf 'دریافت بستهٔ VPS نسخهٔ %s…\n' "$VERSION"
    curl --disable "${progress[@]}" --show-error --fail --location --proto '=https' --proto-redir '=https' \
        --tlsv1.2 --connect-timeout 12 --max-time 1200 --retry 2 --retry-max-time 1200 \
        --output "$work/$ARCHIVE" "${mirror:-$URL}" \
        || fail 'دانلود GitHub/آینه کامل نشد؛ همان آرشیو را از رایانه با SFTP منتقل کن و --bundle بده.'
fi
(
    cd -- "$work"
    printf '%s  %s\n' "$SHA256" "$ARCHIVE" | sha256sum --check --strict --status
) || fail 'SHA-256 بسته با نسخهٔ تأییدشده برابر نیست؛ هیچ نصبی اجرا نشد.'
printf 'archive_sha256=ok version=%s\n' "$VERSION"
tar -xzf "$work/$ARCHIVE" -C "$work" --no-same-owner --no-same-permissions \
    || fail 'باز کردن بسته ممکن نشد؛ فضای دیسک را بررسی کن.'
installer="$work/$BUNDLE/install.sh"
[[ -f "$installer" && ! -L "$installer" ]] || fail 'نصب‌گر مورد انتظار داخل بسته نیست.'

# Keep the working directory for relative PEM paths and SSH identity for export.
if (( EUID == 0 )); then
    /bin/bash "$installer" "${args[@]}"
else
    sudo -- /bin/bash "$installer" "${args[@]}"
fi
