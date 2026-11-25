#!/usr/bin/bash
# Private file-only APT configuration; never read or modify the VPS' sources.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
umask 077
bundle=$1
shift
no_upgrades=false
if [[ ${1:-} == --no-upgrades ]]; then
    no_upgrades=true
    shift
    export NEEDRESTART_SUSPEND=1
fi
[[ "$bundle" =~ ^/var/tmp/baci-vps\.[a-zA-Z0-9]+$ && -s "$bundle/apt/Packages" ]]
work=$(mktemp -d "$bundle/apt-state.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/lists/partial" "$work/archives/partial"
printf 'deb [trusted=yes arch=amd64] file:%s/apt ./\n' "$bundle" > "$work/sources.list"
export APT_CONFIG="$work/apt.conf"
cat > "$APT_CONFIG" <<EOF_APT
Dir::Etc::main "/dev/null";
Dir::Etc::parts "-";
Dir::Etc::sourcelist "$work/sources.list";
Dir::Etc::sourceparts "-";
Dir::Etc::preferences "/dev/null";
Dir::Etc::preferencesparts "-";
Dir::State::lists "$work/lists";
Dir::Cache::archives "$work/archives";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
APT::Sandbox::User "root";
APT::Install-Recommends "false";
APT::Install-Suggests "false";
APT::Get::Always-Include-Phased-Updates "true";
DPkg::Lock::Timeout "60";
Dpkg::Options { "--force-confold"; };
EOF_APT
if (( $# == 0 )); then
    mapfile -t goals < <(grep -Ev '^#|^$' "$bundle/ubuntu-packages.txt")
else
    goals=("$@")
fi
(( ${#goals[@]} > 0 ))
for package in "${goals[@]}"; do [[ "$package" =~ ^[a-z0-9][a-z0-9.+-]+$ ]]; done
# unshare also prevents maintainer subprocesses from relying on Internet access.
offline=(/usr/bin/unshare --net -- /usr/bin/apt-get)
"${offline[@]}" -qq -o APT::Update::Error-Mode=any update
if [[ "$no_upgrades" == true ]]; then
    # Freeze installed versions for the real solver too, not just the dry run.
    # This may refuse on a host needing Ubuntu security/package maintenance;
    # an add-on must not upgrade base packages or restart the running classroom.
    dpkg-query -W -f='${db:Status-Status}\t${binary:Package}\t${Version}\n' | awk -F '\t' '
        $1 == "installed" {sub(/:amd64$/, "", $2); printf "Package: %s\nPin: version %s\nPin-Priority: 1001\n\n", $2, $3}
    ' > "$work/frozen.pref"
    args=(-o "Dir::Etc::preferences=$work/frozen.pref" --no-upgrade)
    "${offline[@]}" -q --simulate --no-remove "${args[@]}" install "${goals[@]}" > "$work/plan"
    if grep -Eq '^(Inst [^ ]+ \[|Remv |Purg )' "$work/plan"; then
        printf 'Recorder dependencies would change installed Ubuntu packages; nothing was installed. Maintain Ubuntu separately.\n' >&2
        exit 1
    fi
    "${offline[@]}" -qy --no-remove "${args[@]}" install "${goals[@]}"
else
    "${offline[@]}" -qq --simulate --no-remove install "${goals[@]}"
    "${offline[@]}" -qy --no-remove install "${goals[@]}"
fi
