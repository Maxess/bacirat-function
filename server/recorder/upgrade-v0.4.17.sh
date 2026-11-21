#!/usr/bin/env bash
# Upgrade the already working stock 0.4.16 Recorder, after class and delivery have ended.
set -euo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

readonly unit=school-classroom-recorder.service
readonly target=/opt/school-classroom-recorder
readonly spool=/var/lib/school-classroom-recorder
readonly unit_file="/etc/systemd/system/$unit"
readonly credential_dropin="$unit_file.d/10-recorder-credential.conf"
readonly archive=baci-classroom-recorder-v0.4.17.zip
readonly revision=d506bcc1c3eac4c12e93cc0fb76512b893dac3e1
readonly digest=6929be4f172b08a88709c1601b4351e7f6ea13574d7299126fcc482b1787dfaa
readonly ready='recorder: compositor and encoder available (0.4.17)'
readonly -a old_files=(capture.py recording.py view.html view.css view.js README-fa.md)
readonly -a new_files=("${old_files[@]}" frame.js)
work='' restart=0 rollback=0

fail() { printf '%s\n' "$*" >&2; exit 1; }

cleanup() {
    local result=$? restored=1 name
    trap - EXIT
    if (( rollback )); then
        printf 'The updated compositor did not confirm startup. Restoring the previous files.\n' >&2
        if systemctl stop "$unit"; then
            for name in "${old_files[@]}"; do
                install -T -o root -g root -m 0644 -- "$work/previous/$name" "$target/$name" || restored=0
            done
            rm -f -- "$target/frame.js" || restored=0
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
(( $# <= 1 )) || fail 'Usage: upgrade-v0.4.17.sh [local-recorder-zip]'
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
    printf 'Recorder 0.4.17 is already installed. No restart was needed.\n'
    exit 0
fi
[[ ! -e "$target/frame.js" && ! -L "$target/frame.js" ]] || fail 'The installed files are not the stock 0.4.16 release.'
mkdir -m 0700 -- "$work/previous"
for name in "${old_files[@]}"; do
    [[ -f "$target/$name" ]] && protected "$target/$name" || fail 'Found an unexpected installed Recorder file.'
    cp -- "$target/$name" "$work/previous/$name"
done
(cd "$work/previous" && sha256sum -c --status - <<'OLD'
e5ec00360f239c0d069cbfaddbf4f37221db85f49db15e0649fa53dc21cf5c34  capture.py
d6e47b29ebd94d68152e176bd4d64205a0faae77b92c6c10a432788869a6aedd  recording.py
61fbee5070d070e733b4feb0fd015c95455d30a52e537000bf5c9544d3a22f48  view.html
e857e15821f52e5b153e633cbe19a6ee7c6ce077f1d64db82a87bde6e8eab80d  view.css
adabcfc03ef9cbc59e19ff10038dcf0e2cb294d57e60761ead99c4a7154cbc3b  view.js
235c71c1778025df78750d72f065e9d324758bd214fb43bb5e5a5ed17ce7875a  README-fa.md
OLD
) || fail 'The installed files do not match the stock 0.4.16 release. No changes were made.'

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
wait_ready || fail 'Recorder 0.4.17 was not ready. See the Recorder service journal.'
rollback=0 restart=0
printf 'Recorder 0.4.17 is ready. Site settings, browser, LiveKit and existing spool were not modified by the upgrade.\n'
