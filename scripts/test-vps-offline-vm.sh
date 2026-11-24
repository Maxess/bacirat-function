#!/usr/bin/env bash
# Maintainer/CI only: install the actual delivered tarball in a fresh Ubuntu VM,
# with QEMU SLIRP set to restrict=on (no public network egress during setup).
set -euo pipefail
archive=$(realpath "$1")
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
work=$(mktemp -d)
port=22131
vm_pid=
cleanup() {
    local rc=$?
    trap - EXIT
    if (( rc != 0 )); then
        echo 'VM rehearsal failed. Diagnostic tail (synthetic environment only):' >&2
        tail -35 "$work/serial.log" 2>/dev/null || true
        if [[ -n "$vm_pid" ]]; then
            vm 'sudo journalctl --no-pager -n 30 -u "school-classroom-*" -o cat' || true
        fi
    fi
    [[ -z "$vm_pid" ]] || kill "$vm_pid" 2>/dev/null || true
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
qemu-system-x86_64 "${accel[@]}" -m 4096 -smp 2 -display none -monitor none -serial file:"$work/serial.log" \
    -drive file="$work/vm.img",if=virtio,format=qcow2 -drive file="$work/seed.img",if=virtio,format=raw,readonly=on \
    -netdev user,id=nic,restrict=on,hostfwd=tcp:127.0.0.1:$port-:22 -device virtio-net-pci,netdev=nic &
vm_pid=$!
# These synthetic keys are never bundled or published.
openssl req -x509 -newkey rsa:2048 -nodes -days 4 -subj '/CN=Baci offline QA root only' \
    -keyout "$work/ca.key" -out "$work/qa.crt" >/dev/null 2>&1
for target in media school; do
    openssl req -new -newkey rsa:2048 -nodes -subj "/CN=$target.example.test" \
        -keyout "$work/$target.key" -out "$work/$target.csr" >/dev/null 2>&1
    printf 'subjectAltName=DNS:%s.example.test\nextendedKeyUsage=serverAuth\n' "$target" > "$work/$target.ext"
    openssl x509 -req -days 3 -in "$work/$target.csr" -CA "$work/qa.crt" -CAkey "$work/ca.key" -CAcreateserial \
        -extfile "$work/$target.ext" -out "$work/$target.pem" >/dev/null 2>&1
done
wait_vm
send "$archive" "$work/qa.crt" "$work/media.pem" "$work/media.key" "$work/school.pem" "$work/school.key" "$root/tests/fixtures/vps-offline-peer.py"
vm "ARCHIVE=$(basename "$archive") bash -s" <<'GUEST'
set -euo pipefail
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
# Set up only IPv4/localhost fixtures; no school endpoint is running yet.
sudo ip address add 192.0.0.9/32 dev lo
printf '192.0.0.9 media.example.test\n' | sudo tee -a /etc/hosts >/dev/null
mv media.pem fullchain.pem
mv media.key privkey.pem
chmod 600 privkey.pem school.key
sha256sum /etc/apt/sources.list.d/* > apt.before
sysctl -n kernel.apparmor_restrict_unprivileged_userns > apparmor.before
tar xzf "$ARCHIVE"
installer=(sudo bash baci-classroom-vps-offline/install.sh --site https://school.example.test/campus --media media.example.test --ipv4 192.0.0.9)
"${installer[@]}" --check
cp baci-classroom-vps-offline/requirements.lock original.lock
printf '\ncorrupted\n' >> baci-classroom-vps-offline/requirements.lock
if "${installer[@]}" > rejected.log 2>&1; then exit 1; fi
grep -Fq 'Bundle integrity check failed' rejected.log
mv original.lock baci-classroom-vps-offline/requirements.lock
if "${installer[@]}" --check --key school.key > rejected.log 2>&1; then exit 1; fi
grep -Fq 'does not match' rejected.log
sudo mkdir /opt/school-classroom
printf 'do not modify' | sudo tee /opt/school-classroom/foreign.txt >/dev/null
if "${installer[@]}" > rejected.log 2>&1; then exit 1; fi
sudo grep -Fq 'do not modify' /opt/school-classroom/foreign.txt
sudo rm /opt/school-classroom/foreign.txt
sudo rmdir /opt/school-classroom
"${installer[@]}"
sha256sum --check apt.before
sysctl -n kernel.apparmor_restrict_unprivileged_userns | cmp apparmor.before -
test "$(stat -c '%a:%U' Baci-VPS-pairing.json)" = 600:vpsqa
test "$(sudo stat -c '%a:%U' /etc/school-classroom/site-pairing.json)" = 600:root
sudo cmp /etc/school-classroom/site-pairing.json Baci-VPS-pairing.json
sudo -u school-recorder test ! -r /etc/school-classroom/site-pairing.json
sudo -u school-recorder test ! -r /etc/school-classroom/tls/privkey.pem
sudo -u school-classroom-tls test ! -r /etc/school-classroom-recorder.json
sudo sha256sum /etc/school-classroom/{livekit.yaml,admission.json,site-pairing.json} /etc/school-classroom-recorder.json > install.before
sudo systemctl show -p InvocationID --value school-classroom-sfu > sfu.before
"${installer[@]}"
sudo sha256sum --check install.before
sudo systemctl show -p InvocationID --value school-classroom-sfu | cmp sfu.before -
sudo baci-vps doctor
if sudo baci-vps export; then exit 1; fi
sudo cmp /etc/school-classroom/site-pairing.json Baci-VPS-pairing.json
# Verify outgoing controls after offline provisioning, with a local private
# synthetic web host, in a second network namespace and no default route.
sudo ip netns add schoolqa
sudo ip link add vpsqa0 type veth peer name schoolqa0 netns schoolqa
sudo ip addr add 172.30.123.1/30 dev vpsqa0
sudo ip link set vpsqa0 up
sudo ip netns exec schoolqa ip addr add 172.30.123.2/30 dev schoolqa0
sudo ip netns exec schoolqa ip link set schoolqa0 up
sudo ip netns exec schoolqa ip link set lo up
printf '172.30.123.2 school.example.test\n' | sudo tee -a /etc/hosts >/dev/null
sudo systemd-run --quiet --unit=baci-fixture-peer --property=WorkingDirectory="$PWD" \
    "$(command -v ip)" netns exec schoolqa /usr/bin/python3 "$PWD/vps-offline-peer.py" school.pem school.key
sudo /usr/bin/python3 vps-offline-peer.py --exercise
sudo baci-vps doctor --online | tee online.log
grep -Fq 'school_webhook=ok' online.log
grep -Fq 'school_recorder=ready' online.log
systemctl is-enabled school-classroom-{sfu,https,admission,recorder}.service
cat /proc/sys/kernel/random/boot_id > boot.before
GUEST
vm 'sudo reboot' || true
for attempt in $(seq 1 60); do
    if vm 'test "$(cat /proc/sys/kernel/random/boot_id)" != "$(cat boot.before)" && sudo baci-vps doctor' 2>/dev/null; then
        echo 'VM passed: offline install, TLS, signed API and native webhooks, sandboxed MP4, Recorder heartbeat, and service recovery after reboot.'
        exit 0
    fi
    sleep 2
done
exit 1
