#!/usr/bin/env bash
# Upgrade a working stock 0.4.16 or 0.4.17 Recorder after class and delivery have ended.
set -euo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

readonly unit=school-classroom-recorder.service
readonly target=/opt/school-classroom-recorder
readonly spool=/var/lib/school-classroom-recorder
readonly unit_file="/etc/systemd/system/$unit"
readonly credential_dropin="$unit_file.d/10-recorder-credential.conf"
readonly archive=baci-classroom-recorder-v0.4.18.zip
readonly revision=513abeb9ebd590b75e40be3a1fed36ba3559dc44
readonly digest=e25950993723b8a3441c8f1acc86dca8756e9a935d42b716ffc0bfc0eeaebad0
readonly ready='recorder: compositor and encoder available (0.4.18)'
readonly -a old_files=(capture.py recording.py view.html view.css view.js README-fa.md board.js)
readonly -a new_files=("${old_files[@]}" frame.js)
work='' restart=0 rollback=0 had_frame=0

fail() { printf '%s\n' "$*" >&2; exit 1; }

cleanup() {
    local result=$? restored=1 name
    trap - EXIT
    if (( rollback )); then
        printf 'The updated compositor did not confirm startup. Restoring the previous files.\n' >&2
        if systemctl stop "$unit"; then
            for name in "${new_files[@]}"; do
                if [[ $name == frame.js ]] && (( !had_frame )); then
                    rm -f -- "$target/$name" || restored=0
                else
                    install -T -o root -g root -m 0644 -- "$work/previous/$name" "$target/$name" || restored=0
                fi
            done
        else
            restored=0
        fi
    fi
    if (( restart && restored )); then
        systemctl start "$unit" || restored=0
    fi
    if (( restored )); then
        if [[ -n "$work" ]]; then rm -rf -- "$work"; fi
    else
        printf 'Automatic recovery could not finish. Backup of program files only: %s/previous\n' "$work" >&2
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

protected() {
    local mode
    [[ ! -L "$1" && $(stat -c '%u' -- "$1") == 0 ]] || return 1
    mode=$(stat -c '%a' -- "$1") || return 1
    (( (8#$mode & 0022) == 0 ))
}

check_idle() {
    local path
    for path in "$spool"/*; do
        [[ ${path##*/} =~ ^[a-f0-9]{32}$ ]] || continue
        [[ -d "$path" && ! -L "$path" && ! -e "$path/receipt.json" && ! -L "$path/receipt.json" \
            && ( -f "$path/.retained" || -f "$path/.cleaned" ) ]] \
            || fail 'A recording or delivery is pending. Let it finish before upgrading; no stored recording was deleted.'
    done
}

wait_ready() {
    local state current last='' since=0 logs deadline=$((SECONDS+40))
    while (( SECONDS < deadline )); do
        state=$(systemctl show -p SubState --value "$unit")
        current=$(systemctl show -p InvocationID --value "$unit")
        if [[ $state == running && $current =~ ^[a-f0-9]{32}$ ]]; then
            if [[ $last != "$current" ]]; then
                logs=$(journalctl --no-pager -o cat -n 30 "_SYSTEMD_INVOCATION_ID=$current") || logs=''
                if grep -Fxq -- "$ready" <<< "$logs"; then
                    last=$current since=$SECONDS
                fi
            fi
            if [[ $last == "$current" ]] && (( SECONDS-since >= 8 )); then return 0; fi
        else
            last=''
            [[ $state != failed && $state != dead ]] || break
        fi
        sleep 1
    done
    return 1
}

(( EUID == 0 )) || fail 'Run as root on the existing Recorder VPS.'
(( $# <= 1 )) || fail 'Usage: upgrade-v0.4.18.sh [local-recorder-zip]'
[[ -d "$target" && $(readlink -f -- "$target") == "$target" ]] && protected "$target" \
    || fail 'The protected installed Recorder directory was not found.'
[[ -d "$spool" && ! -L "$spool" ]] || fail 'The existing Recorder state directory was not found.'
# Serialize local upgrades. The Recorder itself writes only to its separate, private spool.
exec 9< "$target"
flock -n 9 || fail 'Another Recorder upgrade is running.'
[[ -f "$unit_file" ]] && protected "$unit_file" && printf '%s  %s\n' \
    'cd3543f13478fccddf934d9d15fc1fe79f6f863753c3d7ae27e555c540ec93e0' "$unit_file" | sha256sum -c --status - \
    || fail 'Expected the stock Recorder service. No changes were made.'
dropins=$(systemctl show -p DropInPaths --value "$unit")
[[ $(systemctl show -p FragmentPath --value "$unit") == "$unit_file" && ( -z "$dropins" || $dropins == "$credential_dropin" ) ]] \
    || fail 'This Recorder has a custom service configuration. No changes were made.'
systemctl is-active --quiet "$unit" || fail 'The existing Recorder is not active. Do not use this upgrade to repair startup.'
check_idle

work=$(mktemp -d)
if (( $# )); then
    [[ -f "$1" ]] || fail 'Local Recorder ZIP was not found.'
    cp -- "$1" "$work/$archive"
else
    curl -fsSL --proto '=https' --proto-redir '=https' --retry 2 --connect-timeout 12 --max-time 120 \
        -H 'Accept: application/vnd.github.raw+json' \
        "https://api.github.com/repos/Maxess/bacirat-function/contents/artifacts/$archive?ref=$revision" -o "$work/$archive"
fi
printf '%s  %s\n' "$digest" "$work/$archive" | sha256sum -c --status - || fail 'Recorder ZIP did not match the reviewed release.'
unzip -q "$work/$archive" -d "$work/new"

identical=1
for name in "${new_files[@]}"; do
    if [[ ! -f "$target/$name" ]] || ! protected "$target/$name" || ! cmp -s -- "$target/$name" "$work/new/$name"; then
        identical=0
        break
    fi
done
if (( identical )); then
    printf 'Recorder 0.4.18 is already installed. No restart was needed.\n'
    exit 0
fi
mkdir -m 0700 -- "$work/previous"
for name in "${old_files[@]}"; do
    [[ -f "$target/$name" ]] && protected "$target/$name" || fail 'Found an unexpected installed Recorder file.'
    cp -- "$target/$name" "$work/previous/$name"
done
if [[ -e "$target/frame.js" || -L "$target/frame.js" ]]; then
    [[ -f "$target/frame.js" ]] && protected "$target/frame.js" || fail 'Found an unexpected installed compositor.'
    cp -- "$target/frame.js" "$work/previous/frame.js"
    had_frame=1
    (cd "$work/previous" && sha256sum -c --status - <<'V17'
5e48adeb6962b744626e8917c1b3d1b3aab3ce1ab6c87b4b08f7b07dedb3a343  capture.py
ce2fad97cfcf442468c7cc0dd6197b8439c7904f179605409f40ac5381dae7da  recording.py
43a9687165cb5f8c463f335096cf320ff4304040ff8aedf9a7fb8bab5783b21c  view.html
fc1814e72364484ae461b830dba5687d256a16e328d49befadb870677d2c3426  view.css
b7a8a3c32c3e95bc88d5c78eaf3691eda461c2ca3e4a219c02488380c22d6d7f  view.js
67a4de9f800210e47d2bbc65f3ead178c686e0d1dd291c3132d4adff193d3031  README-fa.md
47647454f0d5d495e906fee6fbc0f428fa356177a0f0d6111bb05470daaeafa3  board.js
25b98973b5c335c78d12cec2f01acb1df939d1ce16405924b375ea794d13fd61  frame.js
V17
    ) || fail 'The installed files do not match the stock 0.4.17 release. No changes were made.'
else
    (cd "$work/previous" && sha256sum -c --status - <<'V16'
e5ec00360f239c0d069cbfaddbf4f37221db85f49db15e0649fa53dc21cf5c34  capture.py
d6e47b29ebd94d68152e176bd4d64205a0faae77b92c6c10a432788869a6aedd  recording.py
61fbee5070d070e733b4feb0fd015c95455d30a52e537000bf5c9544d3a22f48  view.html
e857e15821f52e5b153e633cbe19a6ee7c6ce077f1d64db82a87bde6e8eab80d  view.css
adabcfc03ef9cbc59e19ff10038dcf0e2cb294d57e60761ead99c4a7154cbc3b  view.js
235c71c1778025df78750d72f065e9d324758bd214fb43bb5e5a5ed17ce7875a  README-fa.md
47647454f0d5d495e906fee6fbc0f428fa356177a0f0d6111bb05470daaeafa3  board.js
V16
    ) || fail 'The installed files do not match the stock 0.4.16 release. No changes were made.'
fi

check_idle
restart=1
systemctl stop "$unit"
# Never replace files if the spool changed while the service was stopping.
check_idle
rollback=1
for name in "${new_files[@]}"; do
    install -T -o root -g root -m 0644 -- "$work/new/$name" "$target/$name"
done
systemctl start "$unit"
wait_ready || fail 'Recorder 0.4.18 was not ready. See the Recorder service journal.'
rollback=0 restart=0
printf 'Recorder 0.4.18 is ready. Site settings, browser, LiveKit and existing spool were not modified by the upgrade.\n'
