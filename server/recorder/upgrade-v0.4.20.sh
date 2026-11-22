#!/usr/bin/env bash
# Upgrade a working stock 0.4.18 Recorder after class, encoding, and delivery end.
set -euo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

readonly unit=school-classroom-recorder.service
readonly target=/opt/school-classroom-recorder
readonly spool=/var/lib/school-classroom-recorder
readonly unit_file="/etc/systemd/system/$unit"
readonly credential_dropin="$unit_file.d/10-recorder-credential.conf"
readonly archive=baci-classroom-recorder-v0.4.20.zip
readonly digest=56f0dc55978c50d0600b3a8349cbd25ea5c9220bf763407b6d6ee463f675c98d
readonly ready='recorder: compositor and encoder available (0.4.20)'
readonly -a program=(README-fa.md capture.py frame.js recording.py view.css view.js)
readonly -a assets=(board.js livekit-client.umd.js pdf-resources.zip requirements.txt transport.py view.html)
readonly -a fonts=(fonts/OFL.txt fonts/README.md fonts/Vazirmatn-Bold.woff2 fonts/Vazirmatn-Regular.woff2)
work='' restart=0 rollback=0

fail() { printf '%s\n' "$*" >&2; exit 1; }

cleanup() {
    local result=$? restored=1 name
    trap - EXIT
    if (( rollback )); then
        printf 'The updated compositor did not confirm startup. Restoring the previous files.\n' >&2
        if systemctl stop "$unit"; then
            for name in "${program[@]}"; do
                install -T -o root -g root -m 0644 -- "$work/previous/$name" "$target/$name" || restored=0
            done
            if [[ -d "$target/fonts" ]]; then
                for name in "${fonts[@]}"; do rm -f -- "$target/$name" || restored=0; done
                rmdir -- "$target/fonts" || restored=0
            fi
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
(( $# <= 1 )) || fail 'Usage: upgrade-v0.4.20.sh [local-recorder-zip]'
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
        || fail 'The installed browser/transport assets are not the stock release. No changes were made.'
done
identical=0
if [[ -d "$target/fonts" ]] && protected "$target/fonts"; then
    identical=1
    for name in "${program[@]}" "${fonts[@]}"; do
        if [[ ! -f "$target/$name" ]] || ! protected "$target/$name" || ! cmp -s -- "$target/$name" "$work/new/$name"; then
            identical=0
            break
        fi
    done
fi
if (( identical )); then
    printf 'Recorder 0.4.20 is already installed. No restart was needed.\n'
    exit 0
fi
[[ ! -e "$target/fonts" && ! -L "$target/fonts" ]] || fail 'Found an unexpected font directory. No changes were made.'
mkdir -m 0700 -- "$work/previous"
for name in "${program[@]}"; do
    [[ -f "$target/$name" ]] && protected "$target/$name" || fail 'Found an unexpected installed Recorder file.'
    cp -- "$target/$name" "$work/previous/$name"
done
(cd "$work/previous" && sha256sum -c --status - <<'V18'
b7ccfce2799eea52bf5588bf13f3f1637ad50de36d6844457f1d941021c2a5ab  README-fa.md
5e48adeb6962b744626e8917c1b3d1b3aab3ce1ab6c87b4b08f7b07dedb3a343  capture.py
37caffd26f719498914c8a418be326a51d94eaab075c2e7c661797c3a0a0980f  frame.js
b1cc71e5d85a0a452cd9255386845bc299ef0a42b9b916f4701fb6151eb47bcc  recording.py
fc1814e72364484ae461b830dba5687d256a16e328d49befadb870677d2c3426  view.css
838a990731725289f9cfbea4d9f88b9aa664867e893acebf8abb36905ad3c4bd  view.js
V18
) || fail 'The installed files do not match the stock 0.4.18 release. No changes were made.'

check_idle
restart=1
systemctl stop "$unit"
# A recording could have started while the new files were being staged.
check_idle
rollback=1
install -d -o root -g root -m 0755 -- "$target/fonts"
for name in "${program[@]}" "${fonts[@]}"; do
    install -T -o root -g root -m 0644 -- "$work/new/$name" "$target/$name"
done
systemctl start "$unit"
wait_ready || fail 'Recorder 0.4.20 was not ready. See the Recorder service journal.'
rollback=0 restart=0
printf 'Recorder 0.4.20 is ready. Site settings, browser, LiveKit and existing spool were not modified by the upgrade.\n'
