#!/usr/bin/env bash
# Sourced only by the isolated QEMU harness. No changes to a deployed VPS.
run_school_rehearsal() (
    set -Eeuo pipefail
    local q="$work/school-capacity" client=$1 count=$2 quality=$3 net=baci-vps-recording
    local php="$root/tests/fixtures/vps-school-record.php"
    local spool=/var/lib/school-classroom-recorder
    local results=${BACI_QA_SCHOOL_RESULTS:?}
    local seconds=${BACI_QA_SCHOOL_SECONDS:-60}
    [[ "$seconds" =~ ^(60|90|120)$ && -d "$results" && "$VM_BENCH_CLIENT_CPUS" != '' ]]
    [[ "$quality" == low || "$quality" == medium ]]
    [[ "$count" == 0 || "$count" == 3 || "$count" == 9 ]]
    mkdir -m 700 "$q"
    cp "$work/qa.crt" "$work/school.pem" "$work/school.key" "$q/"
    vm 'sudo cat /etc/school-classroom/site-pairing.json' > "$q/pair.json"
    chmod 600 "$q/pair.json"
    in_net() { sudo ip netns exec "$net" "$@"; }
    as_runner() {
        in_net runuser -u "$(id -un)" -- env "BACI_QA_DIR=$q" "SSL_CERT_FILE=$q/qa.crt" \
            taskset -c "$VM_BENCH_CLIENT_CPUS" "$@"
    }
    school() { as_runner php -d "curl.cainfo=$q/qa.crt" "$php" "$@"; }
    phase() { vm "printf '%s\\n' '$1' > qa-phase.tmp; mv qa-phase.tmp qa-phase" >/dev/null; }
    stop_fixture() {
        local rc=$?
        trap - EXIT
        # All payloads/keys stay in the disposable fixture; upload only counters and sanitized receipts.
        if [[ -f "$q/counters.jsonl" ]]; then cp "$q/counters.jsonl" "$results/counters.jsonl"; fi
        if [[ -f "$q/load.log" ]]; then grep '^QA_SCHOOL_' "$q/load.log" > "$results/media.log" || true; fi
        if (( rc != 0 )); then
            echo 'QA_SCHOOL_FAILURE test fixture only; no production VPS was contacted' >&2
            for f in "$q/load.log" "$q/php.log" "$q/tls.log"; do
                if [[ -f "$f" ]]; then tail -12 "$f" | sed "s#^#QA_SCHOOL $(basename "$f"): #"; fi
            done
            vm 'sudo journalctl --no-pager -n 18 -u school-classroom-recorder -u school-classroom-postprocess -o cat' || true
            school status || true
        fi
        phase halt || true
        local pids
        pids=$(sudo ip netns pids "$net")
        if [[ -n "$pids" ]]; then sudo kill $pids 2>/dev/null || true; fi
        exit "$rc"
    }
    trap 'echo "QA_SCHOOL_STAGE failed at line $LINENO: $BASH_COMMAND" >&2' ERR
    trap stop_fixture EXIT
    # No egress from the synthetic clients, no ICE-over-TCP fallback in this test.
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
    # Use the actual plugin ZIP carried by this public VPS archive. Compare the
    # bytes before taking a disposable copy for the test-only cap increase.
    local site=baci-classroom-site-upgrade-v0.4.26.zip plugin=school-classroom-plugin-v0.4.26.zip
    vm "cat baci-classroom-vps-offline/$site" > "$q/$site"
    printf '%s  %s\n' 493b09e48376222001f728616d0fc0445a75c29951bdf488a1e8467acd35bebf "$q/$site" | sha256sum --strict -c
    python3 - "$q" "$site" "$plugin" "$count" <<'PY_SITE'
import io
from pathlib import Path, PurePosixPath
import sys
from zipfile import ZipFile

q, site, plugin, count = sys.argv[1:]
q = Path(q)
with ZipFile(q/site) as companion:
    member, = [p for p in companion.namelist() if PurePosixPath(p).name == plugin]
    with ZipFile(io.BytesIO(companion.read(member))) as packed:
        packed.extractall(q/'site')
if count == '9':
    dest = q/'site/recording.php'
    old = 'const CL_REC_CAPTURE_LIMIT = 3;'
    text = dest.read_text()
    assert text.count(old) == 1
    dest.write_text(text.replace(old, 'const CL_REC_CAPTURE_LIMIT = 9;'))
PY_SITE
    if [[ "$count" == 9 ]]; then
        vm 'sudo /usr/bin/python3 -IB -' <<'PY_GUEST'
from pathlib import Path
import subprocess

cfg = Path('/etc/school-classroom/site-pairing.json')
import json
pair = json.loads(cfg.read_text())
assert pair['signal_url'] == 'wss://media.example.test' and pair['site'] == 'https://school.example.test/campus'
assert Path('/home/vpsqa/qa-media').read_text().strip() == 'media.example.test'
script = Path('/opt/school-classroom-recorder/releases/0.4.23/recording.py')
text = script.read_text()
replacements = [("self.ids(reply, 'capture', 3)", "self.ids(reply, 'capture', 9)"),
                ('[:max(0, 3 - len(self.captures))]', '[:max(0, 9 - len(self.captures))]')]
for old, new in replacements:
    assert text.count(old) == 1
    text = text.replace(old, new)
compile(text, str(script), 'exec')
script.write_text(text)
subprocess.run(['systemctl','restart','school-classroom-recorder.service'],check=True)
print('QA_SCHOOL_VARIANT disposable copies only: site and recorder admit nine; all other limits unchanged')
PY_GUEST
    fi
    python3 "$root/tests/fixtures/vps-record-inputs.py" "$q" "$quality"
    school init
    as_runner php -d "curl.cainfo=$q/qa.crt" -S 127.0.0.1:18391 "$php" > "$q/php.log" 2>&1 &
    in_net taskset -c "$VM_BENCH_CLIENT_CPUS" python3 "$root/tests/fixtures/vps-record-tls.py" "$q" telegram --multi > "$q/tls.log" 2>&1 &
    local n
    ready() {
        in_net curl --disable --noproxy '*' -fsS --max-time 2 --cacert "$q/qa.crt" \
            --resolve school.example.test:443:172.30.124.2 https://school.example.test/readyz >/dev/null 2>&1
    }
    for n in $(seq 1 12); do if ready; then break; fi; sleep 1; done
    ready
    vm 'set -o pipefail; sudo baci-vps doctor --online --wait | tee school-online.log; grep -Fxq school_recorder=ready school-online.log'
    vm "awk -F': ' '/^model name/{print \$2; exit}' /proc/cpuinfo" > "$q/cpu-model.txt"
    send "$root/tests/fixtures/vps-school-sample.py"
    phase idle
    vm 'sudo /usr/bin/python3 -IB ./vps-school-sample.py qa-phase' > "$q/counters.jsonl" &
    local sampler_pid=$!
    sleep 5
    kill -0 "$sampler_pid"
    phase join
    ( umask 077; school tickets > "$q/tickets.json" )
    as_runner bash -c 'ulimit -Sn 8192; export GOMAXPROCS=2; exec "$@"' qa "$client" --fixture "$q" "$quality" > "$q/load.log" 2>&1 &
    local load_pid=$!
    for n in $(seq 1 180); do
        if [[ -f "$q/media-ready" ]]; then break; fi
        kill -0 "$load_pid"
        sleep 1
    done
    test -f "$q/media-ready"
    school open
    school start "$count"
    live_jobs() {
        school status | jq -ce --argjson n "$count" 'select(length==$n and all(.[]; .state=="recording" and .live))'
    }
    for n in $(seq 1 30); do if live_jobs >/dev/null; then break; fi; kill -0 "$load_pid"; sleep 1; done
    live_jobs > "$q/live.json"
    grep '^QA_SCHOOL_JOIN ' "$q/load.log"
    echo "QA_SCHOOL_ACTIVE confirmed captures=$count vp8=$quality steady_window_s=$seconds"
    phase live
    sleep "$seconds"
    kill -0 "$load_pid"
    kill -0 "$sampler_pid"
    live_jobs >/dev/null
    vm 'test -z "$(sudo find /var/lib/school-classroom-recorder \( -name "*.mp4" -o -name .render \) -print -quit)"'
    phase closing
    touch "$q/media-stop"
    for n in $(seq 1 15); do
        if [[ -f "$q/media-paused" ]]; then break; fi
        kill -0 "$load_pid"
        sleep 1
    done
    test -f "$q/media-paused"
    school close
    touch "$q/rooms-closed"
    wait "$load_pid"
    grep '^QA_SCHOOL_MEDIA ' "$q/load.log"
    phase wait
    school status > "$q/closed.json"
    if (( count > 0 )); then
        local eligible
        eligible=$(jq -er '[.[] | .not_before] | min | select(type=="number")' "$q/closed.json")
        for n in $(seq 1 75); do if (( $(date +%s) >= eligible )); then break; fi; sleep 1; done
        phase render
        for n in $(seq 1 220); do
            school status > "$q/closed.json"
            jq -e 'all(.[]; .state != "failed" and .state != "uncertain" and .state != "discarded")' "$q/closed.json" >/dev/null
            if jq -e --argjson n "$count" 'length==$n and all(.[]; .state=="delivered" and .cleaned)' "$q/closed.json" >/dev/null; then break; fi
            sleep 3
        done
        jq -e --argjson n "$count" 'length==$n and all(.[]; .state=="delivered" and .cleaned)' "$q/closed.json" >/dev/null
        test "$(wc -l < "$q/uploads.log")" -eq "$count"
        echo "QA_SCHOOL_RECEIPTS completed=$count after_first_eligible_s=$(($(date +%s)-eligible))"
    else
        test ! -f "$q/uploads.log"
    fi
    phase halt
    wait "$sampler_pid"
    vm "test -z \"\$(sudo find '$spool' \\( -name '*.baci' -o -name '*.mp4' \\) -print -quit)\"; grep -Eq '^oom_kill +0$' /proc/vmstat"
    python3 "$root/tests/fixtures/vps-school-results.py" "$q" "$results/measurement.json" "$count" "$quality" "$seconds"
    echo 'QA_SCHOOL_PASS exact UDP workload and all planned receipts verified'
)
