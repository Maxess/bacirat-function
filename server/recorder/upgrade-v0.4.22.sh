#!/usr/bin/env bash
# Upgrade an idle, working stock Recorder 0.4.21. Does not touch LiveKit or school data.
set -euo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

readonly unit=school-classroom-recorder.service
readonly target=/opt/school-classroom-recorder
readonly spool=/var/lib/school-classroom-recorder
readonly unit_file="/etc/systemd/system/$unit"
readonly credential_dropin="$unit_file.d/10-recorder-credential.conf"
readonly archive=baci-classroom-recorder-v0.4.22.zip
readonly digest=97520e9d5e067e1f432ed4772d84d893606d68d04789aef14c280bdf77316601
readonly ready='recorder: deferred capture and encoder available (0.4.22)'
readonly -a program=(README-fa.md board.js capture.py frame.js recording.py transport.py)
readonly -a added=(archive.py movie.py ingest.html ingest.js encoded.js replay.js)
readonly -a assets=(livekit-client.umd.js pdf-resources.zip requirements.txt view.html view.js view.css
    fonts/OFL.txt fonts/README.md fonts/Vazirmatn-Bold.woff2 fonts/Vazirmatn-Regular.woff2)
work='' restart=0 rollback=0

fail() { printf '%s\n' "$*" >&2; exit 1; }

cleanup() {
    local result=$? restored=1 name
    trap - EXIT
    if (( rollback )); then
        printf 'The updated Recorder did not start. Restoring the previous program.\n' >&2
        if systemctl stop "$unit"; then
            for name in "${program[@]}"; do
                install -T -o root -g root -m 0644 -- "$work/previous/$name" "$target/$name" || restored=0
            done
            for name in "${added[@]}"; do rm -f -- "$target/$name" || restored=0; done
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
        printf 'Automatic recovery did not finish. Previous program: %s/previous\n' "$work" >&2
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
(( $# <= 1 )) || fail 'Usage: upgrade-v0.4.22.sh [local-recorder-zip]'
[[ -d "$target" && $(readlink -f -- "$target") == "$target" ]] && protected "$target" \
    || fail 'The protected installed Recorder directory was not found.'
[[ -d "$spool" && ! -L "$spool" ]] || fail 'The existing Recorder state directory was not found.'
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

source_zip=${1:-"$(dirname -- "${BASH_SOURCE[0]}")/$archive"}
[[ -f "$source_zip" ]] || fail 'Put the reviewed Recorder ZIP beside this script or pass its local path.'
work=$(mktemp -d)
cp -- "$source_zip" "$work/$archive"
printf '%s  %s\n' "$digest" "$work/$archive" | sha256sum -c --status - || fail 'Recorder ZIP did not match the reviewed release.'
unzip -q "$work/$archive" -d "$work/new"

for name in "${assets[@]}"; do
    [[ -f "$target/$name" ]] && protected "$target/$name" && cmp -s -- "$target/$name" "$work/new/$name" \
        || fail 'The installed browser and font assets are not the stock release. No changes were made.'
done
identical=1
for name in "${program[@]}" "${added[@]}"; do
    if [[ ! -f "$target/$name" ]] || ! protected "$target/$name" || ! cmp -s -- "$target/$name" "$work/new/$name"; then
        identical=0
        break
    fi
done
if (( identical )); then
    printf 'Recorder 0.4.22 is already installed. No restart was needed.\n'
    exit 0
fi
mkdir -m 0700 -- "$work/previous"
for name in "${program[@]}"; do
    [[ -f "$target/$name" ]] && protected "$target/$name" || fail 'Found an unexpected installed Recorder file.'
    cp -- "$target/$name" "$work/previous/$name"
done
for name in "${added[@]}"; do
    [[ ! -e "$target/$name" && ! -L "$target/$name" ]] || fail 'Found an unexpected file from a different Recorder release.'
done
(cd "$work/previous" && sha256sum -c --status - <<'V21'
3b8989e1e37650b49bc26c68c85b69c0df38ecd3866bc27e7e6fbd1693497b41  README-fa.md
4b558e92eb7b380884547ce25745f919bc7fe7adc3fc1286b414ef600a781737  board.js
5fb739fa0b893505cfa1dfc86aa0fecab33a037026249815d3828ec4acd3553f  capture.py
69f77380719c97f36f6c61ef7d477e782bd1dcd6339fe231487bd8251f409509  frame.js
492f74816308a0d0ddeb695621b0b57523188c0317b57ed375128aa2a1e7d716  recording.py
6fb6fc1ec3dd17c8df68fe19fadb06e8af356dcc1ac4e241d39bee0bccfc25b9  transport.py
V21
) || fail 'Expected stock Recorder 0.4.21; no changes were made.'

check_idle
restart=1
systemctl stop "$unit"
check_idle
rollback=1
for name in "${program[@]}" "${added[@]}"; do
    install -T -o root -g root -m 0644 -- "$work/new/$name" "$target/$name"
done
systemctl start "$unit"
wait_ready || fail 'Recorder 0.4.22 was not ready. See the systemd journal.'
rollback=0 restart=0
printf 'Recorder 0.4.22 is ready. Check both sides are 0.4.22 before enabling recording.\n'
