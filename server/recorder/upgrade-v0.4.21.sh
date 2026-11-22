#!/usr/bin/env bash
# Upgrade a working stock 0.4.18 or 0.4.20 Recorder after class, encoding, and delivery end.
set -euo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

readonly unit=school-classroom-recorder.service
readonly target=/opt/school-classroom-recorder
readonly spool=/var/lib/school-classroom-recorder
readonly unit_file="/etc/systemd/system/$unit"
readonly credential_dropin="$unit_file.d/10-recorder-credential.conf"
readonly archive=baci-classroom-recorder-v0.4.21.zip
readonly digest=ca64a9057dfbb758502f5ca89850fb9256f4a4c30266a7f8dd09864d08612cfe
readonly ready='recorder: compositor and encoder available (0.4.21)'
readonly -a program=(README-fa.md board.js capture.py frame.js recording.py view.js)
readonly -a assets=(livekit-client.umd.js pdf-resources.zip requirements.txt transport.py view.html)
readonly -a fonts=(fonts/OFL.txt fonts/README.md fonts/Vazirmatn-Bold.woff2 fonts/Vazirmatn-Regular.woff2)
work='' restart=0 rollback=0 from18=0
replace=("${program[@]}")

fail() { printf '%s\n' "$*" >&2; exit 1; }

cleanup() {
    local result=$? restored=1 name
    trap - EXIT
    if (( rollback )); then
        printf 'The updated compositor did not confirm startup. Restoring the previous files.\n' >&2
        if systemctl stop "$unit"; then
            for name in "${replace[@]}"; do
                install -T -o root -g root -m 0644 -- "$work/previous/$name" "$target/$name" || restored=0
            done
            if (( from18 )) && [[ -d "$target/fonts" && ! -L "$target/fonts" ]]; then
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
(( $# <= 1 )) || fail 'Usage: upgrade-v0.4.21.sh [local-recorder-zip]'
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

unchanged=("${assets[@]}")
if [[ ! -e "$target/fonts" && ! -L "$target/fonts" ]]; then
    from18=1
    replace+=(view.css)
else
    [[ -d "$target/fonts" ]] && protected "$target/fonts" || fail 'Unexpected Recorder font directory.'
    unchanged+=(view.css "${fonts[@]}")
fi
for name in "${unchanged[@]}"; do
    [[ -f "$target/$name" ]] && protected "$target/$name" && cmp -s -- "$target/$name" "$work/new/$name" \
        || fail 'The installed browser/font/transport assets are not the stock release. No changes were made.'
done
identical=1
for name in "${replace[@]}"; do
    if [[ ! -f "$target/$name" ]] || ! protected "$target/$name" || ! cmp -s -- "$target/$name" "$work/new/$name"; then
        identical=0
        break
    fi
done
if (( identical && !from18 )); then
    printf 'Recorder 0.4.21 is already installed. No restart was needed.\n'
    exit 0
fi
mkdir -m 0700 -- "$work/previous"
for name in "${replace[@]}"; do
    [[ -f "$target/$name" ]] && protected "$target/$name" || fail 'Found an unexpected installed Recorder file.'
    cp -- "$target/$name" "$work/previous/$name"
done
if (( from18 )); then
    (cd "$work/previous" && sha256sum -c --status - <<'V18'
b7ccfce2799eea52bf5588bf13f3f1637ad50de36d6844457f1d941021c2a5ab  README-fa.md
21ef1674f82b43a1ca2951cc617758e3a90e6df6bf751bc1803edbb7cb74a0c0  board.js
5e48adeb6962b744626e8917c1b3d1b3aab3ce1ab6c87b4b08f7b07dedb3a343  capture.py
37caffd26f719498914c8a418be326a51d94eaab075c2e7c661797c3a0a0980f  frame.js
b1cc71e5d85a0a452cd9255386845bc299ef0a42b9b916f4701fb6151eb47bcc  recording.py
838a990731725289f9cfbea4d9f88b9aa664867e893acebf8abb36905ad3c4bd  view.js
fc1814e72364484ae461b830dba5687d256a16e328d49befadb870677d2c3426  view.css
V18
    ) || fail 'The installed files do not match the stock 0.4.18 release. No changes were made.'
else
    (cd "$work/previous" && sha256sum -c --status - <<'V20'
2480ed69d384dae01d848be2fef8d46df90e5ba324d266603cd18c3c9a1f6904  README-fa.md
21ef1674f82b43a1ca2951cc617758e3a90e6df6bf751bc1803edbb7cb74a0c0  board.js
334302e13bb77d6b9e8b0719b1be7c440ae805c8c342807b4773b4ba942f2319  capture.py
4930aa4ac451f02eca3473a2d89d2c3cde4382b9deef291d1579e673ddd33f7c  frame.js
8ee5179f3939de8c6556665a00c999fbc9d215dd80cf213447407d7c47134456  recording.py
44ecf7d4f95f051dcb3845ea0a6aeae44bb11f4e2d2cf08cf42258db8b6e892e  view.js
V20
    ) || fail 'The installed files do not match the stock 0.4.20 release. No changes were made.'
fi

check_idle
restart=1
systemctl stop "$unit"
# A recording could have started while the new files were being staged.
check_idle
rollback=1
if (( from18 )); then install -d -o root -g root -m 0755 -- "$target/fonts"; fi
for name in "${replace[@]}"; do
    install -T -o root -g root -m 0644 -- "$work/new/$name" "$target/$name"
done
if (( from18 )); then
    for name in "${fonts[@]}"; do
        install -T -o root -g root -m 0644 -- "$work/new/$name" "$target/$name"
    done
fi
systemctl start "$unit"
wait_ready || fail 'Recorder 0.4.21 was not ready. See the Recorder service journal.'
rollback=0 restart=0
printf 'Recorder 0.4.21 is ready. Site settings, browser, LiveKit and existing spool were not modified by the upgrade.\n'
