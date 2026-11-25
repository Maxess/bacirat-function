#!/usr/bin/env bash
# Sourced by test-vps-offline-vm.sh. Peers, school and provider run outside the
# VM; the guest runs just the installed VPS services (plus QA sampling).
run_recording_rehearsal() (
    set -euo pipefail
    local q="$work/recording" client=$1 net=baci-vps-recording
    mkdir -m 700 "$q"
    cp "$work/qa.crt" "$work/school.pem" "$work/school.key" "$q/"
    vm 'sudo cat /etc/school-classroom/site-pairing.json' > "$q/pair.json"
    chmod 600 "$q/pair.json"
    python3 "$root/tests/fixtures/vps-record-inputs.py" "$q"
    local php="$root/tests/fixtures/vps-record-school.php"
    in_net() { sudo ip netns exec "$net" "$@"; }
    as_runner() { in_net runuser -u "$(id -un)" -- env "BACI_QA_DIR=$q" "SSL_CERT_FILE=$q/qa.crt" "$@"; }
    school() { as_runner php -d "curl.cainfo=$q/qa.crt" "$php" "$@"; }
    stop_fixture() {
        local code=$?
        if (( code != 0 )); then
            tail -10 "$q/load.log" "$q/php.log" "$q/tls.log" 2>/dev/null || true
            vm 'sudo journalctl --no-pager -n 12 -u school-classroom-recorder -u school-classroom-postprocess -o cat' || true
            school status || true
        fi
        local pids
        pids=$(sudo ip netns pids "$net")
        if [[ -n "$pids" ]]; then sudo kill $pids 2>/dev/null || true; fi
        exit "$code"
    }
    trap stop_fixture EXIT
    # Disposable /29 WAN with no route to the Internet. Block ICE/TCP to prove UDP.
    in_net ip route add 192.0.0.9/32 via 172.30.124.1
    in_net ip rule add ipproto tcp dport 7881 prohibit
    vm 'sudo bash -s' <<'GUEST'
set -euo pipefail
iface=$(grep -lix '52:54:00:12:34:57' /sys/class/net/*/address | cut -d/ -f5)
test -n "$iface"
ip addr add 172.30.124.1/29 dev "$iface"
ip link set "$iface" up
sed -i -E 's/^172\.30\.123\.2 school\.example\.test$/172.30.124.2 school.example.test/' /etc/hosts
systemctl stop baci-fixture-peer
GUEST
    school init
    as_runner php -S 127.0.0.1:18391 "$php" > "$q/php.log" 2>&1 &
    in_net python3 "$root/tests/fixtures/vps-record-tls.py" "$q" > "$q/tls.log" 2>&1 &
    # Wait for the new site's heartbeat before opening a normal bell.
    vm 'for n in $(seq 1 24); do if sudo baci-vps doctor --online 2>&1 | grep -q "^school_recorder=ready"; then exit; fi; sleep 1; done; exit 1'
    vm 'sudo systemd-run --quiet --unit=baci-qa-sample /bin/bash -c '\''while true; do grep -E "^MemAvailable:|^SwapTotal:" /proc/meminfo | xargs; sleep 1; done'\'''
    as_runner timeout 125 env GOMAXPROCS=3 "$client" --fixture "$q/camera.ivf" "$q/screen.ivf" \
        "$q/teacher.opus" "$q/pupil.opus" "$q/live.json" < "$q/pair.json" > "$q/load.log" 2>&1 &
    load_pid=$!
    for n in $(seq 1 70); do
        if [[ -s "$q/live.json" ]]; then break; fi
        kill -0 "$load_pid" && sleep 1
    done
    test -s "$q/live.json"
    school start
    for n in $(seq 1 30); do
        if school status | grep -Fq '"live":true'; then break; fi
        sleep 1
    done
    school status | grep -Fq '"live":true'
    school layout
    vm "sudo test -s /var/lib/school-classroom-recorder/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/doc-cccccccccccccccccccccccccccccccc.pdf || sleep 2; sudo test -s /var/lib/school-classroom-recorder/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/doc-cccccccccccccccccccccccccccccccc.pdf"
    # Rendering must not occur while the ten pupils are receiving A/V.
    vm 'test -z "$(sudo find /var/lib/school-classroom-recorder -type f -name "*.mp4" -print -quit)"; ! pgrep -u school-recorder -x chrome-headless >/dev/null'
    wait "$load_pid"
    cat "$q/load.log"
    test "$(grep -c '^QA_LOAD window_end_s=' "$q/load.log")" -eq 7
    school close
    test ! -e "$q/received.mp4"
    # The school clock is real: a one-minute delay after confirmed closure.
    for n in $(seq 1 220); do
        status=$(school status)
        if grep -Fq '"delivered":true' <<<"$status"; then break; fi
        if grep -Eq '"state":"(failed|discarded|uncertain)"' <<<"$status"; then echo "QA_RECORD $status" >&2; exit 1; fi
        sleep 2
    done
    school status | grep -Fq '"delivered":true'
    # Decode received bytes, not the check_encoder MP4 or a filesystem-only export.
    python3 "$root/tests/fixtures/vps-record-verify.py" "$q/received.mp4"
    vm 'sudo systemctl stop baci-qa-sample; sudo journalctl -u baci-qa-sample --no-pager -o cat | awk '\''$1=="MemAvailable:" { if (!min || $2 < min) min=$2; if ($5 != 0) bad=1; n++ } END { print "QA_RAM samples=" n " minimum_MemAvailable_KiB=" min " swap_KiB=0"; if (n < 60 || min < 32768 || bad) exit 1 }'\'''
    vm 'for u in school-classroom-{sfu,admission,https,recorder,postprocess}; do
        path=/sys/fs/cgroup/system.slice/$u.service
        awk "\$1 ~ /^oom/ && \$2 != 0 {exit 1}" "$path/memory.events" || exit
        test "$(systemctl show -p NRestarts --value "$u")" = 0 || exit
        echo "QA_RAM unit=$u peak_bytes=$(cat "$path/memory.peak")"
    done
    grep -Eq "^oom_kill +0$" /proc/vmstat'
    echo "QA_RECORD received and decoded an approved MP4; one $ram MiB guest, teacher + 10 pupils, UDP media only."
)
