#!/usr/bin/bash
# Stage verified release files privately. Run from the directory containing the two TLS inputs.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
unset BASH_ENV ENV CDPATH PYTHONPATH PYTHONHOME APT_CONFIG
umask 077
fail() { printf 'Baci VPS: %s\n' "$*" >&2; exit 1; }
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    cat <<'HELP'
Fresh, dedicated Ubuntu 24.04 LTS amd64 VPS only. Read START-HERE-fa.md first.
  sudo bash baci-classroom-vps-offline/install.sh
  sudo bash baci-classroom-vps-offline/install.sh --check
Inputs are prompted if omitted: --site https://school.example.ir[/school]
  --media media.example.ir --ipv4 PUBLIC_IPV4
  --cert ./fullchain.pem --key ./privkey.pem
After install: sudo baci-vps doctor [--online] or sudo baci-vps export
HELP
    exit
fi
(( EUID == 0 )) || fail 'Run this command with sudo.'
# No Bash sourcing of os-release, and no application/apt writes on an unsupported OS.
grep -qx 'ID=ubuntu' /etc/os-release && grep -Eq '^VERSION_ID="?24\.04"?$' /etc/os-release \
    && [[ $(uname -m) == x86_64 && $(dpkg --print-architecture) == amd64 ]] \
    || fail 'Select an official Ubuntu 24.04 LTS (64-bit/amd64) image from your VPS provider.'
[[ -r /sys/fs/cgroup/cgroup.controllers && -d /run/systemd/system ]] \
    && systemctl show -p Version --value >/dev/null \
    && unshare --net -- /bin/true \
    || fail 'A real VPS with systemd, cgroup v2 and network namespaces is required, not shared hosting or an unprivileged container.'

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ -f "$here/SHA256SUMS" ]] || fail 'Unpack the entire verified VPS archive; files are missing.'
[[ "$here" != / && "$here" != /var/tmp && /var/tmp/ != "$here/"* ]] || fail 'Extract into a dedicated directory, not an OS directory.'
[[ -z $(find "$here" ! -type d ! -type f -print -quit) ]] || fail 'Unexpected links or special files in the extracted bundle.'
work=$(mktemp -d /var/tmp/baci-vps.XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
cp -R -- "$here/." "$work/"
[[ -z $(find "$work" ! -type d ! -type f -print -quit) ]] || fail 'Unexpected links or special files in the bundle.'
# Require an exact inventory; checksum input cannot point outside the staged directory.
(
    cd "$work"
    [[ -s SHA256SUMS ]] && ! grep -qEv '^[a-f0-9]{64}  [a-zA-Z0-9_+./~:-]+$' SHA256SUMS \
        && cmp -s <(cut -c67- SHA256SUMS | sort) <(find . -type f ! -path './SHA256SUMS' -printf '%P\n' | sort) \
        && sha256sum --strict --check --quiet SHA256SUMS
) || fail 'Bundle integrity check failed. Verify the archive, extract again, and keep TLS and other files OUTSIDE its folder.'

# On official VPS images Python is normally installed by cloud-init. In a base
# without Python, install only its bundled closure before running the full preflight.
# Serialize that bootstrap with the installer itself.
exec 9>/run/baci-vps-install.lock
flock -n 9 || fail 'Another Baci VPS installer is already running.'
if [[ ! -x /usr/bin/python3 ]]; then
    # The full Python preflight runs again before the runtime packages are touched.
    for path in /opt/school-classroom* /etc/school-classroom* /var/lib/school-classroom* \
        /etc/systemd/system/school-classroom* /usr/local/sbin/baci-vps; do
        [[ ! -e "$path" && ! -L "$path" ]] || fail 'Existing classroom installation found; nothing was overwritten.'
    done
    for arg in "$@"; do [[ "$arg" != --check ]] || fail 'Read-only preflight requires the Ubuntu base Python; full install can bootstrap it offline.'; done
    /bin/bash "$work/apt-local.sh" "$work" python3
fi
/usr/bin/python3 -IB "$work/setup.py" "$@"
