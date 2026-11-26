#!/usr/bin/env bash
# Read-only diagnostic for the published v1.3.0 recording bundle. No sudo,
# network, package downloads, system APT settings or package changes required.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
unset APT_CONFIG TAR_OPTIONS BASH_ENV ENV CDPATH
umask 077

readonly NAME=baci-classroom-vps-offline
readonly ARCHIVE="$NAME-v1.3.0-ubuntu24.04-amd64.tar.gz"
readonly SHA256=282f266d3df3d613a5209be41798764b61fb7d42dc33efdc76001f29ca720255
fail() { printf 'Baci APT check: %s\n' "$*" >&2; exit 1; }
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    printf 'Usage: bash %s [path/to/%s]\nOnly simulates the recording dependency plan.\n' "${0##*/}" "$ARCHIVE"
    exit 0
fi
(( $# <= 1 )) || fail 'Pass at most one archive path.'
archive=${1:-$ARCHIVE}
[[ -f "$archive" && ! -L "$archive" && -r "$archive" ]] || fail 'Run in the download directory or pass the path to the v1.3.0 archive.'
[[ $(sha256sum < "$archive") == "$SHA256  -" ]] || fail 'The archive SHA-256 does not match the published v1.3.0 bundle.'

work=$(mktemp -d /var/tmp/baci-apt-check.XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Unpack only the package index and the exact recording goals, not the .debs.
tar -xzf "$archive" -C "$work" --no-same-owner --no-same-permissions -- "$NAME/apt/Packages" "$NAME/ubuntu-packages.txt"
mkdir -p "$work/empty" "$work/lists/partial" "$work/archives/partial" "$work/log"
printf 'deb [trusted=yes arch=amd64] file:%s/%s/apt ./\n' "$work" "$NAME" > "$work/sources.list"
export APT_CONFIG="$work/apt.conf"
cat > "$APT_CONFIG" <<EOF_APT
Dir::Etc::main "/dev/null";
Dir::Etc::parts "$work/empty";
Dir::Etc::sourcelist "$work/sources.list";
Dir::Etc::sourceparts "$work/empty";
Dir::Etc::preferences "/dev/null";
Dir::Etc::preferencesparts "$work/empty";
Dir::State::status "/var/lib/dpkg/status";
Dir::State::lists "$work/lists";
Dir::Cache::archives "$work/archives";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
Dir::Log "$work/log";
APT::Sandbox::User "root";
APT::Install-Recommends "false";
APT::Install-Suggests "false";
APT::Get::Always-Include-Phased-Updates "true";
Acquire::Languages "none";
EOF_APT
mapfile -t goals < <(grep -Ev '^#|^$' "$work/$NAME/ubuntu-packages.txt")
(( ${#goals[@]} )) || fail 'No package goals found.'
for package in "${goals[@]}"; do [[ "$package" =~ ^[a-z0-9][a-z0-9.+-]+$ ]] || fail 'Invalid package goal.'; done
# The only source is the temporary file index. The real package database is
# read to solve against the host's installed/held versions; it is never written.
/usr/bin/apt-get -qq -o APT::Update::Error-Mode=any update
if ! /usr/bin/apt-get -q --simulate -o Debug::pkgProblemResolver=true install "${goals[@]}" > "$work/plan" 2>&1; then
    printf 'apt_plan=unresolved (simulation only)\n'
    tail -60 "$work/plan"
    exit 1
fi
if grep -Eq '^(Remv |Purg |The following (packages will be DOWNGRADED|held packages will be changed):)' "$work/plan"; then
    printf 'apt_plan=blocked (simulation only; the installer would refuse this plan)\n'
    grep -E '^(Broken |Remv |Purg |Inst [^ ]+ \[|The following (packages will be DOWNGRADED|held packages will be changed):)' "$work/plan"
    exit 1
fi
printf 'apt_plan=ok (simulation only; no package removals or downgrades proposed)\n'
