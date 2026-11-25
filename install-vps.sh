#!/usr/bin/env bash
# Online entry point for the same reviewed archive; do not build software on the VPS.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
unset BASH_ENV ENV CDPATH TAR_OPTIONS
umask 077

# Update this pin only after the exact VM-tested archive is public.
readonly VERSION=1.2.0
readonly SHA256=9a79d791d38e2277b5fb29f9d05ae223338bb4c25ae30ab83ffb7c9d148c1cb4
readonly BUNDLE=baci-classroom-vps-offline
readonly ARCHIVE="${BUNDLE}-v${VERSION}-ubuntu24.04-amd64.tar.gz"
readonly URL="https://github.com/Maxess/bacirat-function/releases/download/vps-offline-v${VERSION}/${ARCHIVE}"

fail() { printf 'Baci VPS: %s\n' "$*" >&2; exit 1; }
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    cat <<HELP
Baci VPS $VERSION from GitHub Releases (fresh Ubuntu 24.04 LTS amd64 only).
  bash install-vps.sh --profile live --tls auto  # 512 MiB VPS, no local recording
  bash install-vps.sh --tls auto --check
  bash install-vps.sh --tls files --cert ./fullchain.pem --key ./privkey.pem
Omit --profile to select live or full automatically; full also installs local recording.
All installer flags are forwarded, including --site, --media and --ipv4.
Run as your SSH user; sudo is used only after the archive is verified and unpacked.
HELP
    exit 0
fi

grep -qx 'ID=ubuntu' /etc/os-release && grep -Eq '^VERSION_ID="?24\.04"?$' /etc/os-release \
    && [[ $(uname -m) == x86_64 && $(dpkg --print-architecture) == amd64 ]] \
    || fail 'اوبونتوی تازهٔ Ubuntu 24.04 LTS amd64 لازم است.'
command -v curl >/dev/null || fail 'ابتدا curl را از مخزن رسمی Ubuntu نصب کن، یا از روش انتقال آرشیو استفاده کن.'
if (( EUID != 0 )); then
    command -v sudo >/dev/null || fail 'این حساب SSH باید دسترسی sudo داشته باشد.'
fi

work=$(mktemp -d -- "${TMPDIR:-/var/tmp}/baci-vps-github.XXXXXXXX") \
    || fail 'ساخت پوشهٔ موقت ممکن نیست؛ فضای خالی و دسترسی را بررسی کن.'
trap 'rm -rf -- "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
progress=(--silent)
if [[ -t 2 ]]; then progress=(--progress-bar); fi

printf 'دریافت بستهٔ VPS نسخهٔ %s از GitHub…\n' "$VERSION"
curl --disable "${progress[@]}" --show-error --fail --location --proto '=https' --proto-redir '=https' \
    --tlsv1.2 --connect-timeout 15 --max-time 1200 --retry 2 --retry-max-time 1200 \
    --output "$work/$ARCHIVE" "$URL" \
    || fail 'دانلود GitHub کامل نشد؛ دسترسی HTTPS به GitHub Releases و فضای دیسک را بررسی کن.'
(
    cd -- "$work"
    printf '%s  %s\n' "$SHA256" "$ARCHIVE" | sha256sum --check --strict --status
) || fail 'SHA-256 بسته با نسخهٔ تأییدشده برابر نیست؛ هیچ نصبی اجرا نشد.'
printf 'archive_sha256=ok version=%s\n' "$VERSION"
tar -xzf "$work/$ARCHIVE" -C "$work" --no-same-owner --no-same-permissions \
    || fail 'باز کردن بسته ممکن نشد؛ فضای دیسک را بررسی کن.'
installer="$work/$BUNDLE/install.sh"
[[ -f "$installer" && ! -L "$installer" ]] || fail 'نصب‌گر مورد انتظار داخل بسته نیست.'

# Keep the caller's directory for relative certificate paths and the same SSH identity for export.
if (( EUID == 0 )); then
    /bin/bash "$installer" "$@"
else
    sudo -- /bin/bash "$installer" "$@"
fi
