#!/usr/bin/env bash
# Maintainer/CI only: install the actual delivered tarball in a fresh Ubuntu VM,
# with QEMU SLIRP set to restrict=on. In auto mode a separate local CA
# actually validates the TLS-ALPN-01 challenge without contacting the Internet.
set -euo pipefail
archive=$(realpath "$1")
mode=${2:-files}
ram=${3:-4096}
[[ "$mode" == files || "$mode" == auto ]]
[[ "$ram" == 512 || "$ram" == 4096 ]]
cpus=2
profile=full
if [[ "$ram" == 512 ]]; then cpus=1; profile=live; fi
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
work=$(mktemp -d)
port=22131
vm_pid=
cleanup() {
    local rc=$?
    trap - EXIT
    if (( rc != 0 )); then
        echo 'VM rehearsal failed. Diagnostic tail (synthetic environment only):' >&2
        tail -20 "$work/serial.log" 2>/dev/null || true
        if [[ -n "$vm_pid" ]]; then
            vm 'tail -6 rejected.log preflight.log 2>/dev/null || true; grep -E "^(MemTotal|MemAvailable|SwapTotal):" /proc/meminfo; grep ^oom_kill /proc/vmstat; sudo journalctl -k --no-pager -g "oom-kill|Out of memory" -o cat; sudo journalctl --no-pager -n 16 -u "school-classroom-*" -o cat; sudo journalctl --no-pager -n 12 -u baci-qa-acme -o cat; sudo journalctl --no-pager -n 8 -u baci-qa-pebble -o cat' || true
        fi
    fi
    if [[ -n "$vm_pid" ]]; then
        kill "$vm_pid" 2>/dev/null || true
        wait "$vm_pid" 2>/dev/null || true
    fi
    rm -rf "$work"
    exit "$rc"
}
trap cleanup EXIT
vm() { ssh -i "$work/ssh" -F /dev/null -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=4 -p "$port" vpsqa@127.0.0.1 "$@"; }
send() { scp -i "$work/ssh" -F /dev/null -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -P "$port" "$@" vpsqa@127.0.0.1:; }
wait_vm() {
    for attempt in $(seq 1 60); do
        if vm 'cloud-init status --wait >/dev/null' 2>/dev/null; then return; fi
        sleep 2
    done
    return 1
}
image=ubuntu-24.04-minimal-cloudimg-amd64.img
base=https://cloud-images.ubuntu.com/minimal/releases/noble/release
curl -fSsL --retry 3 "$base/SHA256SUMS" -o "$work/images.sha256"
curl -fSsL --retry 3 "$base/$image" -o "$work/base.img"
expected=$(awk -v name="*$image" '$2 == name {print $1}' "$work/images.sha256")
[[ "$expected" =~ ^[a-f0-9]{64}$ ]]
printf '%s  %s\n' "$expected" "$work/base.img" | sha256sum --check
printf 'VM baseline Ubuntu 24.04 minimal SHA-256: %s\n' "$expected"
ssh-keygen -q -t ed25519 -N '' -f "$work/ssh"
cat > "$work/user-data" <<EOF_USER
#cloud-config
users:
  - name: vpsqa
    shell: /bin/bash
    lock_passwd: true
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - $(cat "$work/ssh.pub")
package_update: false
package_upgrade: false
EOF_USER
printf 'instance-id: baci-offline-qa\nlocal-hostname: classroom-qa\n' > "$work/meta-data"
cloud-localds "$work/seed.img" "$work/user-data" "$work/meta-data"
qemu-img create -q -f qcow2 -F qcow2 -b "$work/base.img" "$work/vm.img" 24G
accel=(-accel tcg -cpu max)
if [[ -r /dev/kvm && -w /dev/kvm ]]; then accel=(-accel kvm -cpu host); fi
qemu-system-x86_64 "${accel[@]}" -m "$ram" -smp "$cpus" -display none -monitor none -serial file:"$work/serial.log" \
    -drive file="$work/vm.img",if=virtio,format=qcow2 -drive file="$work/seed.img",if=virtio,format=raw,readonly=on \
    -netdev user,id=nic,restrict=on,hostfwd=tcp:127.0.0.1:$port-:22 -device virtio-net-pci,netdev=nic &
vm_pid=$!
# These synthetic keys are never bundled or published.
openssl req -x509 -newkey rsa:2048 -nodes -days 4 -subj '/CN=Baci offline QA root only' \
    -keyout "$work/ca.key" -out "$work/qa.crt" >/dev/null 2>&1
targets=(school)
if [[ "$mode" == auto ]]; then targets+=(acme); else targets+=(media); fi
for target in "${targets[@]}"; do
    name="$target.example.test"
    if [[ "$target" == acme ]]; then name=acme-v02.api.letsencrypt.org; fi
    openssl req -new -newkey rsa:2048 -nodes -subj "/CN=$name" \
        -keyout "$work/$target.key" -out "$work/$target.csr" >/dev/null 2>&1
    printf 'subjectAltName=DNS:%s\nextendedKeyUsage=serverAuth\n' "$name" > "$work/$target.ext"
    openssl x509 -req -days 3 -in "$work/$target.csr" -CA "$work/qa.crt" -CAkey "$work/ca.key" -CAcreateserial \
        -extfile "$work/$target.ext" -out "$work/$target.pem" >/dev/null 2>&1
done
wait_vm
send "$archive" "$work/qa.crt" "$work/school.pem" "$work/school.key" "$root/tests/fixtures/vps-offline-peer.py"
if [[ -n ${4:-} ]]; then
    install -m 0755 "$4" "$work/vps-live-media"
    send "$work/vps-live-media"
fi
if [[ "$mode" == files ]]; then
    send "$work/media.pem" "$work/media.key"
else
    # These test CAs and the DNS responder are never shipped in the bundle.
    pebble_version=v2.10.3
    for item in pebble pebble-challtestsrv; do
        name="$item-linux-amd64.tar.gz"
        url="https://github.com/letsencrypt/pebble/releases/download/$pebble_version/$name"
        case "$item" in
          pebble) digest=7e33cf3e426b67e9e647c44e8cc91683caa8ab331f7a99e5e27fcee291c4c7e2 ;;
          pebble-challtestsrv) digest=00b2c5ef525cd8ad56cda898a7cac6169a10b0fd3f2a7456fe0cec92e7b36cb6 ;;
        esac
        curl -fSsL --retry 3 "$url" -o "$work/$name"
        printf '%s  %s\n' "$digest" "$work/$name" | sha256sum --check
        mkdir "$work/$item-payload"
        tar xzf "$work/$name" -C "$work/$item-payload"
        binary=$(find "$work/$item-payload" -type f -name "$item" -print -quit)
        if [[ -z "$binary" ]]; then binary=$(find "$work/$item-payload" -type f -name "$item-linux-amd64" -print -quit); fi
        [[ -n "$binary" ]]
        install -m 755 "$binary" "$work/$item"
    done
    send "$work/pebble" "$work/pebble-challtestsrv" "$work/acme.pem" "$work/acme.key" "$root/tests/fixtures/vps-acme-gateway.py"
fi
vm "ARCHIVE=$(basename "$archive") MODE=$mode RAM=$ram PROFILE=$profile bash -s" <<'GUEST'
set -Eeuo pipefail
trap 'echo "VM guest failure at line $LINENO: $BASH_COMMAND" >&2' ERR
read -r total swap < <(awk '/^MemTotal:/{m=$2} /^SwapTotal:/{s=$2} END {print m,s}' /proc/meminfo)
printf 'VM memory: provisioned=%s MiB visible=%s KiB swap=%s KiB\n' "$RAM" "$total" "$swap"
if [[ "$RAM" == 512 ]]; then
    test "$total" -le 524288
    test "$swap" -eq 0
fi
awk '$1 == "oom_kill" {print $2}' /proc/vmstat > oom.before
test "$(cat oom.before)" = 0
python3 - <<'PY'
import socket
try:
    with socket.create_connection(('1.1.1.1', 443), 2):
        raise SystemExit('VM must not have outbound network access')
except OSError:
    print('No VM Internet egress before install')
PY
sudo install -m 0644 qa.crt /usr/local/share/ca-certificates/baci-qa.crt
sudo update-ca-certificates >/dev/null
# No public DNS. The synthetic CA sees the advertised IPv4 through a private route.
media=media.example.test
if [[ "$MODE" == auto ]]; then media=classroom-new.example.com; fi
printf '%s\n' "$media" > qa-media
sudo ip address add 192.0.0.9/32 dev lo
printf '192.0.0.9 %s\n' "$media" | sudo tee -a /etc/hosts >/dev/null
sudo ip netns add schoolqa
sudo ip link add vpsqa0 type veth peer name schoolqa0 netns schoolqa
sudo ip addr add 172.30.123.1/29 dev vpsqa0
sudo ip link set vpsqa0 up
sudo ip netns exec schoolqa ip addr add 172.30.123.2/29 dev schoolqa0
sudo ip netns exec schoolqa ip link set schoolqa0 up
sudo ip netns exec schoolqa ip link set lo up
sudo ip netns exec schoolqa ip route add 192.0.0.9/32 via 172.30.123.1
printf '172.30.123.2 school.example.test\n' | sudo tee -a /etc/hosts >/dev/null
chmod 600 school.key
sha256sum /etc/apt/sources.list.d/* > apt.before
sysctl -n kernel.apparmor_restrict_unprivileged_userns > apparmor.before
tar xzf "$ARCHIVE"
installer=(sudo bash baci-classroom-vps-offline/install.sh --site https://school.example.test/campus --media "$media" --ipv4 192.0.0.9)
if [[ "$PROFILE" == live ]]; then
    if "${installer[@]}" --check --profile full > rejected.log 2>&1; then exit 1; fi
    grep -Fq 'use --profile live' rejected.log
    dpkg-query -W -f='${binary:Package} ${db:Status-Abbrev}\n' ffmpeg python3-venv > render-debs.before 2>/dev/null || true
fi
if [[ "$MODE" == files ]]; then
    mv media.pem fullchain.pem
    mv media.key privkey.pem
    chmod 600 privkey.pem
else
    test ! -e fullchain.pem && test ! -e privkey.pem
    # Refuse wrong DNS or unreachable ACME before any application path is written.
    if "${installer[@]}" --check --ipv4 192.0.0.10 > rejected.log 2>&1; then exit 1; fi
    grep -Fq 'Media DNS is not ready' rejected.log
    sudo ip netns exec schoolqa ip addr add 172.30.123.3/29 dev schoolqa0
    printf '172.30.123.3 acme-v02.api.letsencrypt.org\n' | sudo tee -a /etc/hosts >/dev/null
    if "${installer[@]}" --check > rejected.log 2>&1; then exit 1; fi
    grep -Fq 'Cannot reach the trusted Let' rejected.log
    test ! -e /etc/school-classroom
    # The production Let's Encrypt endpoint is mapped to a disposable CA only
    # inside this guest. No ACME validation bypass, no insecure HTTPS flags.
    chmod 700 pebble pebble-challtestsrv
    chmod 600 acme.key
    cat > pebble.json <<'ACME'
{"pebble":{"listenAddress":"127.0.0.1:14000","managementListenAddress":"172.30.123.3:15000","certificate":"acme.pem","privateKey":"acme.key","httpPort":80,"tlsPort":443,"retryAfter":{"authz":1,"order":1},"profiles":{"default":{"description":"isolated VPS QA","validityPeriod":2592000}}}}
ACME
    ip_bin=$(command -v ip)
    sudo systemd-run --quiet --unit=baci-qa-dns --property=WorkingDirectory="$PWD" "$ip_bin" netns exec schoolqa \
        "$PWD/pebble-challtestsrv" -http01= -https01= -tlsalpn01= -doh= -dnsserver=127.0.0.1:8053 \
        -management=127.0.0.1:8055 -defaultIPv4=192.0.0.9 -defaultIPv6=
    sudo systemd-run --quiet --unit=baci-qa-pebble --property=WorkingDirectory="$PWD" --setenv=PEBBLE_VA_NOSLEEP=1 \
        "$ip_bin" netns exec schoolqa "$PWD/pebble" -config pebble.json -dnsserver 127.0.0.1:8053
    sudo systemd-run --quiet --unit=baci-qa-acme --property=WorkingDirectory="$PWD" "$ip_bin" netns exec schoolqa \
        /usr/bin/python3 "$PWD/vps-acme-gateway.py" qa.crt acme.pem acme.key
    python3 - <<'PY_QA'
import json, ssl, time, urllib.request
from pathlib import Path
ctx = ssl.create_default_context(cafile='qa.crt')
client = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))
endpoint = 'https://acme-v02.api.letsencrypt.org'
for attempt in range(18):
    try:
        # Wait for both listeners; one becoming ready does not imply the other is ready.
        with client.open(endpoint + ':15000/roots/0', timeout=2) as reply:
            data = reply.read(8192)
        with client.open(endpoint + '/directory', timeout=2) as reply:
            directory = json.load(reply)
        assert data.startswith(b'-----BEGIN CERTIFICATE-----')
        assert directory['newOrder'].startswith(endpoint + '/')
        Path('pebble-issuer.crt').write_bytes(data)
        print('Test-only ACME directory and issuer root are ready.')
        break
    except (OSError, ValueError) as error:
        if attempt == 17:
            raise SystemExit('Test CA did not become ready: ' + str(error))
        time.sleep(1)
PY_QA
    sudo install -m 0644 pebble-issuer.crt /usr/local/share/ca-certificates/baci-qa-acme-issuer.crt
    sudo update-ca-certificates >/dev/null
fi
"${installer[@]}" --check | tee preflight.log
grep -Fq "preflight=ok tls=$MODE profile=$PROFILE ram_kib=$total" preflight.log
test ! -e /var/lib/school-classroom-https
if [[ "$MODE" == auto ]] && sudo journalctl --no-pager -u baci-qa-acme -o cat | grep -F '"POST '; then
    echo 'Read-only ACME preflight unexpectedly submitted a write.' >&2
    exit 1
fi
cp baci-classroom-vps-offline/requirements.lock original.lock
printf '\ncorrupted\n' >> baci-classroom-vps-offline/requirements.lock
if "${installer[@]}" > rejected.log 2>&1; then exit 1; fi
grep -Fq 'Bundle integrity check failed' rejected.log
mv original.lock baci-classroom-vps-offline/requirements.lock
if [[ "$MODE" == files ]]; then
    if "${installer[@]}" --check --key school.key > rejected.log 2>&1; then exit 1; fi
    grep -Fq 'does not match' rejected.log
else
    if "${installer[@]}" --check --tls files > rejected.log 2>&1; then exit 1; fi
    grep -Fq 'TLS files are incomplete' rejected.log
fi
sudo mkdir /opt/school-classroom
printf 'do not modify' | sudo tee /opt/school-classroom/foreign.txt >/dev/null
if "${installer[@]}" > rejected.log 2>&1; then exit 1; fi
sudo grep -Fq 'do not modify' /opt/school-classroom/foreign.txt
sudo rm /opt/school-classroom/foreign.txt
sudo rmdir /opt/school-classroom
"${installer[@]}" | tee installation.log
grep -Fq "installation=ok tls=$MODE profile=$PROFILE" installation.log
sha256sum --check apt.before
sysctl -n kernel.apparmor_restrict_unprivileged_userns | cmp apparmor.before -
test "$(stat -c '%a:%U' Baci-VPS-pairing.json)" = 600:vpsqa
test "$(sudo stat -c '%a:%U' /etc/school-classroom/site-pairing.json)" = 600:root
sudo cmp /etc/school-classroom/site-pairing.json Baci-VPS-pairing.json
units=(school-classroom-{sfu,https,admission}.service)
private=(/etc/school-classroom/{livekit.yaml,admission.json,site-pairing.json,offline-vps.json})
if [[ "$PROFILE" == full ]]; then
    grep -Fq renderer_mp4=ok installation.log
    sudo -u school-recorder test ! -r /etc/school-classroom/site-pairing.json
    sudo -u school-recorder test ! -r /etc/school-classroom/tls/privkey.pem
    sudo -u school-classroom-tls test ! -r /etc/school-classroom-recorder.json
    units+=(school-classroom-recorder.service)
    private+=(/etc/school-classroom-recorder.json)
else
    grep -Fq recorder=not_installed installation.log
    for path in /opt/school-classroom-recorder /var/lib/school-classroom-recorder /etc/school-classroom-recorder.json \
        /etc/apparmor.d/school-classroom-chromium /etc/systemd/system/school-classroom-{recorder,postprocess}.service; do
        sudo test ! -e "$path"
    done
    if getent passwd school-recorder >/dev/null; then exit 1; fi
    dpkg-query -W -f='${binary:Package} ${db:Status-Abbrev}\n' ffmpeg python3-venv > render-debs.after 2>/dev/null || true
    cmp render-debs.before render-debs.after
fi
if [[ "$MODE" == auto ]]; then
    test ! -e fullchain.pem && test ! -e privkey.pem
    test ! -d /etc/school-classroom/tls
    sudo test "$(sudo stat -c '%a:%U' /var/lib/school-classroom-https)" = 700:school-classroom-tls
    # Port 80 is still free after the real TLS-ALPN-01 issuance.
    sudo python3 -c 'import socket; s=socket.socket(); s.bind(("0.0.0.0", 80))'
    sudo journalctl --no-pager -u school-classroom-https -o cat | grep -F 'tls-alpn-01' > acme-validation.log
    sudo journalctl --no-pager -u baci-qa-pebble -o cat | grep -F 'set VALID by completed challenge' >> acme-validation.log
    openssl s_client -connect 192.0.0.9:443 -servername "$media" -verify_hostname "$media" -verify_return_error \
        -CAfile pebble-issuer.crt -no-CApath -no-CAstore </dev/null 2>/dev/null \
        | openssl x509 -noout -fingerprint -sha256 > issued.before
    echo 'Real ACME TLS-ALPN-01 challenge on TCP 443 passed without user PEM inputs or TCP 80.'
fi
sudo sha256sum "${private[@]}" > install.before
sudo systemctl show -p InvocationID --value school-classroom-sfu > sfu.before
"${installer[@]}"
sudo sha256sum --check install.before
sudo systemctl show -p InvocationID --value school-classroom-sfu | cmp sfu.before -
sudo baci-vps doctor
if sudo baci-vps export; then exit 1; fi
sudo cmp /etc/school-classroom/site-pairing.json Baci-VPS-pairing.json
# Verify signed controls with the private synthetic school; no messenger is ever contacted.
sudo systemd-run --quiet --unit=baci-fixture-peer --property=WorkingDirectory="$PWD" \
    "$(command -v ip)" netns exec schoolqa /usr/bin/python3 "$PWD/vps-offline-peer.py" school.pem school.key
sudo /usr/bin/python3 vps-offline-peer.py --exercise
sudo baci-vps doctor --online | tee online.log
grep -Fq 'school_webhook=ok' online.log
if [[ "$PROFILE" == full ]]; then
    grep -Fq 'school_recorder=ready' online.log
    grep -Fq 'profile=full recorder=ok renderer=ok' online.log
else
    grep -Fq 'profile=live recorder=not_installed renderer=not_installed' online.log
    test ! -e /run/baci-fixture-beat.json
fi
systemctl is-enabled "${units[@]}"
if [[ -x ./vps-live-media ]]; then
    if [[ "$MODE" == auto ]]; then
        # After issuance the classroom doesn't need the disposable CA processes.
        sudo systemctl stop baci-qa-acme baci-qa-pebble baci-qa-dns
    fi
    # The two test peers cannot fall back to TCP ICE (or use a TURN relay).
    sudo ip netns exec schoolqa ip rule add ipproto tcp dport 7881 prohibit
    sudo cat /etc/school-classroom/site-pairing.json \
        | sudo ip netns exec schoolqa timeout 70 ./vps-live-media
    sudo baci-vps doctor
fi
if [[ "$PROFILE" == full ]]; then units+=(school-classroom-postprocess.service); fi
for unit in "${units[@]}"; do
    test "$(systemctl show -p NRestarts --value "$unit")" -eq 0
    printf 'VM service %s %s\n' "$unit" "$(systemctl show -p MemoryCurrent -p MemoryPeak "$unit" | paste -sd ' ')"
done
awk '$1 == "oom_kill" {print $2}' /proc/vmstat | cmp oom.before -
if [[ "$RAM" == 512 ]]; then
    awk '/^SwapTotal:/ {exit ($2 != 0)}' /proc/meminfo
fi
printf 'VM before reboot: OOM kills=0; %s\n' "$(grep '^MemAvailable:' /proc/meminfo)"
cat /proc/sys/kernel/random/boot_id > boot.before
GUEST
vm 'sudo reboot' || true
for attempt in $(seq 1 60); do
    if vm 'test "$(cat /proc/sys/kernel/random/boot_id)" != "$(cat boot.before)" && grep -Eq "^oom_kill +0$" /proc/vmstat && sudo baci-vps doctor' 2>/dev/null; then
        if [[ "$mode" == auto ]]; then
            vm 'set -o pipefail; media=$(cat qa-media); openssl s_client -connect 127.0.0.1:443 -servername "$media" -verify_hostname "$media" -verify_return_error </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256 | cmp issued.before -'
        fi
        echo "VM passed: ${ram} MiB profile=$profile tls=$mode; trusted HTTPS, signed API/webhooks and reboot recovery."
        if [[ "$profile" == full ]]; then echo 'VM full test: sandboxed MP4 and Recorder heartbeat passed.'; fi
        exit 0
    fi
    sleep 2
done
exit 1
