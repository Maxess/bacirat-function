#!/usr/bin/env bash
# Install the pinned Recorder ZIP on the existing LiveKit VPS. No site or LiveKit changes.
set -euo pipefail
umask 022

readonly unit=school-classroom-recorder.service
readonly target=/opt/school-classroom-recorder
readonly config=/etc/school-classroom-recorder.json
readonly unit_file="/etc/systemd/system/$unit"
readonly archive=baci-classroom-recorder-v0.4.16.zip
readonly ref=9405d1e84bdde0cf1903ac44addec5501948a34f
readonly digest=de02931ed2c0247d0ae387fb4797ea78bb65bdec1db9a50cf05994d81c8e9b28
work=''
stage='preflight'

fail() { printf '%s\n' "$*" >&2; exit 1; }
finish() {
    local status=$?
    if [[ -n "$work" ]]; then rm -rf -- "$work"; fi
    if (( status != 0 )); then
        printf '\nRecorder setup stopped at: %s (exit %s). See the first error above.\n' "$stage" "$status" >&2
    fi
}
trap finish EXIT

(( EUID == 0 )) || fail 'Run this installer as root on the existing LiveKit VPS.'
chromium='playwright'
local_browser=''
if (( $# > 0 )); then
    [[ $1 == --chromium-npm && $# -le 2 ]] || fail 'Usage: install-v0.4.16.sh [--chromium-npm [chromium-153.0.0.tgz]]'
    [[ $(uname -m) == x86_64 ]] || fail 'The pinned npm Chromium package is for Linux x86_64 only.'
    chromium='npm'
    local_browser=${2:-}
    [[ -z "$local_browser" || -f "$local_browser" ]] || fail 'The supplied Chromium archive is not a regular file.'
fi
for command in apt-get curl ss systemctl sha256sum; do
    command -v "$command" >/dev/null || fail "Required command not found: $command"
done
state=$(systemctl show -p ActiveState --value "$unit")
case "$state" in
    active|activating|reloading|deactivating) fail 'The recorder is already running or changing state. Do not interrupt a recording.' ;;
esac
ss -H -ltn 'sport = :7880' | awk '$4 == "127.0.0.1:7880" { found=1 } END { exit !found }' \
    || fail 'LiveKit is not listening on 127.0.0.1:7880. Check that this is the LiveKit VPS before installing.'
[[ ! -L "$target" && ! -L "$config" && ! -L "$unit_file" ]] \
    || fail 'The recorder has a symlink or a masked service at an install path. Review it before installing.'
[[ ! -e "$config" || -f "$config" ]] || fail "Not a regular config file: $config"

stage='download and verify ZIP'
printf '\n== %s ==\n' "$stage"
work=$(mktemp -d)
curl -fsSL --proto '=https' --proto-redir '=https' --retry 2 --connect-timeout 15 --max-time 120 \
    -H 'Accept: application/vnd.github.raw+json' \
    "https://api.github.com/repos/Maxess/bacirat-function/contents/artifacts/$archive?ref=$ref" \
    -o "$work/$archive"
printf '%s  %s\n' "$digest" "$work/$archive" | sha256sum -c -

stage='install Ubuntu packages'
printf '\n== %s ==\n' "$stage"
apt-get update
apt-get install -y --no-upgrade ca-certificates unzip python3-venv ffmpeg fonts-noto-core

stage='install recorder files and Playwright'
printf '\n== %s ==\n' "$stage"
unzip -q "$work/$archive" -d "$work/pkg"
if ! id -u school-recorder >/dev/null 2>&1; then
    useradd --system --user-group --no-create-home --home-dir /var/lib/school-classroom-recorder \
        --shell /usr/sbin/nologin school-recorder
fi
install -d -o root -g root -m 0755 "$target"
install -m 0644 "$work/pkg/"{*.py,*.html,*.css,*.js,*.zip,*.txt,README-fa.md} "$target/"
python3 -m venv "$target/venv"
"$target/venv/bin/pip" install -r "$target/requirements.txt"

if [[ $chromium == npm ]]; then
    stage='install the pinned npm Chromium'
    printf '\n== %s ==\n' "$stage"
    # Use Playwright's Ubuntu library dependencies, not its geographically restricted browser download.
    "$target/venv/bin/playwright" install-deps chromium
    node=$("$target/venv/bin/python" -I -c '
import json
from importlib.metadata import version
from pathlib import Path
import playwright
root = Path(playwright.__file__).parent / "driver"
browsers = json.loads((root / "package/browsers.json").read_text())["browsers"]
if version("playwright") != "1.60.0" or not any(b["name"] == "chromium-headless-shell" and b["revision"] == "1223" for b in browsers):
    raise SystemExit("This Chromium fallback was reviewed for Playwright 1.60.0 (revision 1223) only.")
print(root / "node")
')
    if [[ -n "$local_browser" ]]; then
        # Copy to private scratch before checking it. Never execute an npm installer or package script.
        cp -- "$local_browser" "$work/chromium.tgz"
    else
        curl -fsSL --proto '=https' --proto-redir '=https' --retry 2 --connect-timeout 15 --max-time 300 \
            'https://registry.npmjs.org/@sparticuz/chromium/-/chromium-153.0.0.tgz' -o "$work/chromium.tgz"
    fi
    printf '%s  %s\n' '8e2196a92bcf31dbb8f8ff538a562a011ef21063c41c8371fa50af13f36a34efc41d8cc5ff1d615222394fa8a8548b7b744cbb9fcf1bcb24f4be4f9d9ec810c0' \
        "$work/chromium.tgz" | sha512sum -c -
    tar -xOzf "$work/chromium.tgz" package/bin/chromium.br | "$node" -e '
const { pipeline } = require("node:stream/promises");
const { createBrotliDecompress } = require("node:zlib");
pipeline(process.stdin, createBrotliDecompress(), process.stdout).catch(error => {
    console.error(error.message);
    process.exitCode = 1;
});
' > "$work/chrome-headless-shell"
    printf '%s  %s\n' '53a15d6c3a3d27dfb54c4ba60278b1683136f70cf1e67e989da7dfbd3d451ef0' \
        "$work/chrome-headless-shell" | sha256sum -c -
    # Only the executable is substituted, at the exact Linux x64 slot of the pinned Playwright.
    # Do not use Sparticuz's launcher flags. recording.py still requires Chromium's sandbox.
    shell_dir="$target/browsers/chromium_headless_shell-1223/chrome-headless-shell-linux64"
    install -d -o root -g root -m 0755 "$shell_dir"
    install -T -o root -g root -m 0755 "$work/chrome-headless-shell" "$shell_dir/chrome-headless-shell"
    tar -xOzf "$work/chromium.tgz" package/LICENSE > "$work/npm-LICENSE"
    install -T -m 0644 "$work/npm-LICENSE" "$shell_dir/SPARTICUZ-LICENSE.txt"
else
    stage='download Chromium from Playwright'
    printf '\n== %s ==\n' "$stage"
    PLAYWRIGHT_BROWSERS_PATH="$target/browsers" \
        "$target/venv/bin/playwright" install --with-deps --only-shell chromium
fi

stage='configure the private site credential'
printf '\n== %s ==\n' "$stage"
if [[ -e "$config" ]]; then
    printf 'Keeping existing configuration: %s\n' "$config"
else
    IFS= read -r -p 'School HTTPS URL (not the media server): ' site </dev/tty
    IFS= read -rs -p 'Independent recorder key from the school settings: ' secret </dev/tty
    printf '\n'
    printf '%s\n%s\n' "$site" "$secret" | python3 -c '
import json, os, re, sys
site = sys.stdin.readline().strip().rstrip("/")
secret = sys.stdin.readline().strip()
if not re.fullmatch(r"https://(?:[A-Za-z0-9-]+\.)+[A-Za-z0-9-]+(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63})*", site):
    sys.exit("Invalid school URL: use the canonical HTTPS address, with its subpath if any.")
if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", secret):
    sys.exit("Invalid independent recorder key.")
with os.fdopen(os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as fp:
    json.dump({"site": site, "secret": secret}, fp)
' "$config"
    unset secret
fi
chown root:root "$config"
chmod 0600 "$config"

stage='register and start the recorder service'
printf '\n== %s ==\n' "$stage"
install -T -m 0644 "$work/pkg/$unit" "$unit_file"
systemctl daemon-reload
systemctl enable --now "$unit"
systemctl status --no-pager "$unit"
printf '\nService installed. Check the recording readiness in the school settings.\n'
