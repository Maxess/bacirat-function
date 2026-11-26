#!/usr/bin/env bash
# Optional, offline preparation for compact recording on a dedicated Ubuntu VPS.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
unset BASH_ENV ENV CDPATH
umask 077

fail() { printf 'Baci VPS swap: %s\n' "$*" >&2; exit 1; }
case "${1:---check}" in
    --check|--apply) action=${1:---check} ;;
    -h|--help)
        printf '%s\n' 'Usage: sudo bash baci-prepare-swap.sh [--check | --apply]' \
            '--check is read-only. --apply creates one dedicated 512 MiB disk swapfile only if compact needs it.' \
            'Requires a dedicated Ubuntu 24.04 amd64 VPS and ext4 or XFS; does not edit fstab or kernel settings.'
        exit 0 ;;
    *) fail 'Use --check or --apply.' ;;
esac
(( $# <= 1 )) || fail 'Use exactly one action.'
grep -qx 'ID=ubuntu' /etc/os-release && grep -Eq '^VERSION_ID="?24\.04"?$' /etc/os-release \
    && [[ $(uname -m) == x86_64 && $(dpkg --print-architecture) == amd64 && -d /run/systemd/system ]] \
    && [[ -r /sys/fs/cgroup/cgroup.controllers ]] \
    || fail 'Requires Ubuntu 24.04 amd64 with systemd and cgroup v2.'
if systemd-detect-virt --quiet --container; then
    fail 'A container cannot provision host swap. Ask the VPS provider.'
fi

# These names are fixed; never touch the administrator's existing swaps.
parent=/var/lib/baci-vps-swap
file=$parent/swapfile
unit=$(systemd-escape --path --suffix=swap "$file")
unit_path=/etc/systemd/system/$unit
check() {
    read -r ram swap < <(awk '/^MemTotal:/ {r=$2} /^SwapTotal:/ {s=$2} END {print r+0,s+0}' /proc/meminfo)
    (( ram >= 458752 )) || fail 'Compact requires at least 448 MiB visible RAM; swap does not replace it.'
    if (( ram >= 917504 || swap >= 512000 )); then
        printf 'swap=unchanged ram_kib=%s active_swap_kib=%s (no new swap needed)\n' "$ram" "$swap"
        exit 0
    fi
    if [[ -e "$parent" || -L "$parent" || -e "$unit_path" || -L "$unit_path" ]] \
        || [[ $(systemctl show -p LoadState --value "$unit") != not-found ]]; then
        fail 'A path or unit already exists. No existing swap was replaced; check its status with the VPS provider.'
    fi
    for path in /var/lib /etc/systemd/system; do
        [[ $(realpath -e "$path") == "$path" && $(stat -c '%u' "$path") == 0 ]] \
            || fail 'Unexpected system directories; use your provider’s swap option instead.'
        perms=$(stat -c '%a' "$path")
        (( (8#$perms & 8#022) == 0 )) || fail 'Unexpected writable system directory.'
    done
    case $(findmnt -n -o FSTYPE --target /var/lib) in
        ext4|xfs) ;;
        *) fail 'Automatic disk swap here is supported only on ext4 or XFS. Use provider-managed swap.' ;;
    esac
    free_kib=$(df -Pk /var/lib | awk 'END {print $4+0}')
    (( free_kib >= 8912896 )) || fail 'Keep at least 8 GiB free after allocating the 512 MiB swapfile.'
}
if [[ "$action" == --check ]]; then
    check
    printf 'swap=would_create size_mib=512 (read-only; use --apply to approve it)\n'
    exit 0
fi
(( EUID == 0 )) || fail 'Run --apply with sudo.'
exec 9>/run/baci-vps-swap.lock
flock -n 9 || fail 'Another swap preparation is running.'
check

dir_created=false
unit_created=false
committed=false
rollback() {
    local rc=$?
    trap - EXIT INT TERM
    if [[ "$committed" == false && "$dir_created" == true ]]; then
        if [[ "$unit_created" == true ]]; then systemctl disable --now "$unit" >/dev/null 2>&1 || true; fi
        if ! awk -v file="$file" 'NR > 1 && $1 == file {found=1} END {exit found}' /proc/swaps; then
            printf '%s\n' 'Baci VPS swap: swap may still be active; its files were retained. Do not delete them.' >&2
            exit 1
        fi
        if [[ "$unit_created" == true ]]; then rm -f -- "$unit_path"; systemctl daemon-reload || true; fi
        rm -f -- "$file"
        rmdir -- "$parent"
        printf '%s\n' 'Baci VPS swap: incomplete preparation rolled back.' >&2
    fi
    exit "$rc"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -m 0700 -- "$parent"
dir_created=true
# Write real blocks rather than creating a sparse file; no fstab/sysctl edits.
dd if=/dev/zero of="$file" bs=1M count=512 conv=excl,fsync status=none
chmod 0600 -- "$file"
mkswap --quiet "$file"
(set -o noclobber; cat > "$unit_path" <<UNIT
[Unit]
Description=Baci dedicated disk swap for compact recording
[Swap]
What=$file
[Install]
WantedBy=swap.target
UNIT
)
unit_created=true
chmod 0644 -- "$unit_path"
systemctl daemon-reload
if ! systemctl enable --now "$unit"; then
    fail 'The VPS kernel refused swap. Ask the provider whether disk swap is allowed.'
fi
systemctl is-enabled --quiet "$unit" && systemctl is-active --quiet "$unit" \
    && awk -v file="$file" '$1 == file && $3 >= 512000 {found=1} END {exit !found}' /proc/swaps \
    || fail 'Could not confirm the new active and persistent swapfile.'
committed=true
printf 'swap=ready size_mib=512 persistent=yes\n'
