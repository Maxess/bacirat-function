#!/usr/bin/env bash
# Sourced by test-vps-offline-vm.sh, only in the disposable resilience test.
run_tcp_rehearsal() (
    set -Eeuo pipefail
    q="$work/tcp"
    mkdir -m 700 "$q" "$q/.pki" "$q/.pki/nssdb"
    (umask 077; vm 'sudo cat /etc/school-classroom/site-pairing.json' > "$q/pair.json")
    python3 - "$root/artifacts/baci-classroom-site-pairing-v0.1.31.zip" "$q" <<'PY'
from io import BytesIO
from pathlib import Path
import sys
from zipfile import ZipFile

with ZipFile(sys.argv[1]) as pair, ZipFile(BytesIO(pair.read('school-classroom-plugin-v0.4.24.zip'))) as plugin:
    (Path(sys.argv[2]) / 'livekit-client.umd.js').write_bytes(plugin.read('assets/livekit-client.umd.js'))
PY
    certutil -N --empty-password -d "sql:$q/.pki/nssdb"
    certutil -A -d "sql:$q/.pki/nssdb" -n 'Isolated Baci QA CA' -t 'C,,' -i "$work/qa.crt"
    sudo ip netns exec "$test_network" ip route add 192.0.0.9/32 via 172.30.124.1
    sudo ip netns exec "$test_network" ip rule add ipproto udp dport 7882 prohibit
    vm 'sudo bash -s' <<'GUEST'
set -euo pipefail
iface=$(grep -lix '52:54:00:12:34:57' /sys/class/net/*/address | cut -d/ -f5)
test -n "$iface"
ip addr add 172.30.124.1/29 dev "$iface"
ip link set "$iface" up
ss -Hlnpt 'sport = :7881' | grep -F livekit-server
GUEST
    sudo ip netns exec "$test_network" runuser -u "$(id -un)" -- env HOME="$q" NODE_EXTRA_CA_CERTS="$work/qa.crt" \
        BACI_QA_PLAYWRIGHT="$BACI_QA_PLAYWRIGHT" BACI_QA_CHROMIUM="$BACI_QA_CHROMIUM" \
        timeout 100 node "$root/tests/fixtures/vps-ice-tcp.cjs" "$q"
)
