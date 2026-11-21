#!/usr/bin/env bash
# Install the pinned Recorder ZIP on the existing LiveKit VPS. No site or LiveKit changes.
set -euo pipefail
umask 022

readonly unit=school-classroom-recorder.service
readonly target=/opt/school-classroom-recorder
readonly config=/etc/school-classroom-recorder.json
readonly unit_file="/etc/systemd/system/$unit"
readonly credential_dropin="$unit_file.d/10-recorder-credential.conf"
readonly spool=/var/lib/school-classroom-recorder
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

check_startup_inputs() {
    local uid encoders
    uid=$(id -u school-recorder) || fail 'The school-recorder service account is missing.'
    [[ $uid =~ ^[1-9][0-9]*$ ]] || fail 'The recorder must run under a non-root service account.'
    python3 -I - "$config" "$spool" "$uid" <<'PY'
import json
from pathlib import Path
import re
import stat
import sys

config, spool = map(Path, sys.argv[1:3])
try:
    info = config.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise SystemExit("The original recorder configuration must be a root-owned regular file with mode 0600.")
    if not 0 < info.st_size <= 8192:
        raise ValueError
    obj = json.loads(config.read_bytes())
    if not isinstance(obj, dict) or set(obj) != {"site", "secret"}:
        raise ValueError
    if not re.fullmatch(r"https://(?:[A-Za-z0-9-]+\.)+[A-Za-z0-9-]+(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63})*", obj["site"]):
        raise ValueError
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", obj["secret"]):
        raise ValueError
except (OSError, ValueError, TypeError):
    raise SystemExit("Recorder configuration is missing or invalid. Check the HTTPS site and the independent key privately.") from None

try:
    if spool.exists() or spool.is_symlink():
        info = spool.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != int(sys.argv[3]) or spool.resolve() != spool:
            raise SystemExit("Recorder state directory must be private, owned by school-recorder, and not a symlink. Nothing was deleted.")
        worker = spool / "worker.json"
        if worker.exists() or worker.is_symlink():
            if worker.is_symlink() or not worker.is_file() or worker.stat().st_size > 8192:
                raise ValueError
            if not re.fullmatch(r"[a-f0-9]{32}", json.loads(worker.read_bytes())["id"]):
                raise ValueError
except (OSError, ValueError, KeyError, TypeError, RuntimeError):
    raise SystemExit("Could not validate the recorder state or worker identity. Nothing was deleted.") from None
print("Private configuration and recorder state checks passed (no credential values printed).")
PY
    encoders=$(/usr/bin/ffmpeg -hide_banner -encoders 2>/dev/null) || fail 'FFmpeg could not list its installed encoders.'
    [[ $encoders == *' libx264 '* && $encoders == *' aac '* ]] || fail 'The installed FFmpeg needs libx264 and aac encoders.'
}

write_credential_dropin() {
    # systemd can grant a named-user ACL, reflected in st_mode as a group mask.
    # .16 accepts owner-only mode, so make a 0600 copy in a 0700 volatile service directory.
    [[ ! -L "$unit_file.d" && ! -L "$credential_dropin" ]] || fail 'Unexpected symlink at the recorder drop-in path.'
    cat > "$work/credential.conf" <<'UNIT'
[Service]
RuntimeDirectory=school-classroom-recorder-credential
RuntimeDirectoryMode=0700
ExecStartPre=/usr/bin/install -T -m 0600 ${CREDENTIALS_DIRECTORY}/config.json %t/school-classroom-recorder-credential/config.json
ExecStart=
ExecStart=/opt/school-classroom-recorder/venv/bin/python -Esu /opt/school-classroom-recorder/recording.py %t/school-classroom-recorder-credential/config.json
UNIT
    if [[ -e "$credential_dropin" ]]; then
        cmp -s -- "$work/credential.conf" "$credential_dropin" || fail 'An existing recorder drop-in was modified; it was not overwritten.'
        return
    fi
    install -d -m 0755 -- "$unit_file.d"
    install -T -m 0644 -- "$work/credential.conf" "$credential_dropin"
}

wait_for_startup() {
    local deadline=$((SECONDS + 25)) status invocation logs
    stage='wait for local recorder startup'
    while (( SECONDS < deadline )); do
        status=$(systemctl show -p SubState --value "$unit")
        case "$status" in dead|failed|auto-restart|auto-restart-queued) break ;; esac
        invocation=$(systemctl show -p InvocationID --value "$unit")
        if [[ $status == running && $invocation =~ ^[a-f0-9]{32}$ ]]; then
            logs=$(journalctl --no-pager -n 25 -o cat "_SYSTEMD_INVOCATION_ID=$invocation")
            if grep -Fxq 'recorder: compositor and encoder available' <<< "$logs"; then
                printf '\nRecorder compositor started. Check school readiness in the recording settings.\n'
                return 0
            fi
        fi
        sleep 1
    done
    journalctl -u "$unit" -n 15 --no-pager -l || true
    fail 'The recorder did not confirm local startup. See the current journal above.'
}

repair_startup() {
    local status existing
    stage='check the installed 0.4.16 recorder'
    [[ -f "$unit_file" && ! -L "$unit_file" && -f "$target/recording.py" && ! -L "$target/recording.py" && ! -L "$target" ]] \
        || fail 'The expected installed recorder unit and program were not found.'
    # Do not replace a custom unit or a different release.
    printf '%s  %s\n' 'cd3543f13478fccddf934d9d15fc1fe79f6f863753c3d7ae27e555c540ec93e0' "$unit_file" \
        'd6e47b29ebd94d68152e176bd4d64205a0faae77b92c6c10a432788869a6aedd' "$target/recording.py" | sha256sum -c -
    [[ $(systemctl show -p FragmentPath --value "$unit") == "$unit_file" ]] || fail 'The loaded unit is not the stock recorder service.'
    existing=$(systemctl show -p DropInPaths --value "$unit")
    [[ -z "$existing" || $existing == "$credential_dropin" ]] || fail 'Custom service drop-ins exist; no changes were made.'
    status=$(systemctl show -p SubState --value "$unit")
    case "$status" in dead|failed|auto-restart|auto-restart-queued) ;; *) fail 'The recorder may be running. Do not repair it during recording.' ;; esac
    check_startup_inputs
    stage='repair the private runtime credential'
    work=$(mktemp -d)
    write_credential_dropin
    systemctl daemon-reload
    systemctl restart "$unit"
    wait_for_startup
}

(( EUID == 0 )) || fail 'Run this installer as root on the existing LiveKit VPS.'
if [[ ${1:-} == --repair-startup && $# -eq 1 ]]; then
    repair_startup
    exit 0
fi
chromium='playwright'
local_browser=''
if (( $# > 0 )); then
    [[ $1 == --chromium-npm && $# -le 2 ]] || fail 'Usage: install-v0.4.16.sh [--chromium-npm [chromium-153.0.0.tgz] | --repair-startup]'
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
existing=$(systemctl show -p DropInPaths --value "$unit")
[[ -z "$existing" || $existing == "$credential_dropin" ]] || fail 'Custom service drop-ins exist; no changes were made.'
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
check_startup_inputs
write_credential_dropin
install -T -m 0644 "$work/pkg/$unit" "$unit_file"
systemctl daemon-reload
systemctl enable --now "$unit"
wait_for_startup
