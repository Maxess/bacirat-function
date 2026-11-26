#!/usr/bin/bash
# Private file-only APT configuration; never read or modify the VPS' sources.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
umask 077
bundle=$1
shift
no_upgrades=false
check_only=false
while (( $# )); do
    case $1 in
        --no-upgrades) no_upgrades=true; export NEEDRESTART_SUSPEND=1; shift ;;
        --check) check_only=true; shift ;;
        *) break ;;
    esac
done
[[ "$bundle" =~ ^/var/tmp/baci-vps\.[a-zA-Z0-9]+$ && -s "$bundle/apt/Packages" ]]
work=$(mktemp -d "$bundle/apt-state.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/empty" "$work/lists/partial" "$work/archives/partial"
printf 'deb [trusted=yes arch=amd64] file:%s/apt ./\n' "$bundle" > "$work/sources.list"
export APT_CONFIG="$work/apt.conf"
cat > "$APT_CONFIG" <<EOF_APT
Dir::Etc::main "/dev/null";
Dir::Etc::parts "$work/empty";
Dir::Etc::sourcelist "$work/sources.list";
Dir::Etc::sourceparts "$work/empty";
Dir::Etc::preferences "/dev/null";
Dir::Etc::preferencesparts "$work/empty";
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
Acquire::Languages "none";
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
args=()
if [[ "$no_upgrades" == true ]]; then
    # Freeze installed versions for the real solver too, not just the dry run.
    # This may refuse on a host needing Ubuntu security/package maintenance;
    # an add-on must not upgrade base packages or restart the running classroom.
    dpkg-query -W -f='${db:Status-Status}\t${binary:Package}\t${Version}\n' | awk -F '\t' '
        $1 == "installed" {sub(/:amd64$/, "", $2); printf "Package: %s\nPin: version %s\nPin-Priority: 1001\n\n", $2, $3}
    ' > "$work/frozen.pref"
    args=(-o "Dir::Etc::preferences=$work/frozen.pref" --no-upgrade)
fi
# Let the *simulation only* describe removals. APT --no-remove can refuse
# before emitting the Remv lines, which hides the reason on a provider image.
if ! "${offline[@]}" -q --simulate -o Debug::pkgProblemResolver=true "${args[@]}" install "${goals[@]}" > "$work/plan" 2>&1; then
    tail -60 "$work/plan" >&2
    printf 'apt_plan=unresolved (no package install was attempted)\n' >&2
    exit 1
fi
conflicts='^(Remv |Purg |The following (packages will be DOWNGRADED|held packages will be changed):)'
if grep -Eq "$conflicts" "$work/plan" || { [[ "$no_upgrades" == true ]] && grep -Eq '^Inst [^ ]+ \[' "$work/plan"; }; then
    grep -E '^(Remv |Purg |Broken |Inst [^ ]+ \[|The following)' "$work/plan" >&2
    printf '%s\n' 'apt_plan=blocked (no package install was attempted).' \
        'The bundle cannot be installed with the current Ubuntu package versions without a removal, downgrade, held change or disallowed upgrade.' \
        'Do not force the resolver. Use a compatible stock Ubuntu image or resolve the specific package versions with your provider.' >&2
    exit 1
fi
if [[ "$check_only" == true ]]; then
    printf 'apt_plan=ok (read-only; no packages were changed)\n'
    exit 0
fi
# Retain APT's own guards for the real transaction even if the plan was clean.
"${offline[@]}" -qy --no-remove -o APT::Get::allow-downgrades=false "${args[@]}" install "${goals[@]}"
