#!/usr/bin/env bash
# Sourced only by the isolated QEMU harness. No changes to a deployed VPS.
run_school_rehearsal() (
    set -Eeuo pipefail
    local q="$work/school-capacity" client=$1 count=$2 quality=$3 net=baci-vps-recording
    local php="$root/tests/fixtures/vps-school-record.php"
    local spool=/var/lib/school-classroom-recorder
    local results=${BACI_QA_SCHOOL_RESULTS:?}
    local seconds=${BACI_QA_SCHOOL_SECONDS:-60} trial=${BACI_QA_SCHOOL_TRIAL:-} candidate=${BACI_QA_SCHOOL_CANDIDATE:-0}
    [[ "$seconds" =~ ^(60|90|120)$ && -d "$results" && "$VM_BENCH_CLIENT_CPUS" != '' ]]
    [[ "$quality" == low || "$quality" == medium || "$quality" == high ]]
    [[ "$count" == 0 || "$count" == 3 || "$count" == 9 ]]
    if [[ -n "$trial" ]]; then
        [[ "$quality" == high && "$count" == 9 && ( "$trial" == baseline || "$trial" == udp-batch ) ]]
    fi
    local udp_ab=${BACI_QA_SCHOOL_UDP_INTERVAL:-} bells=${BACI_QA_SCHOOL_BELLS:-1}
    local site_version=${BACI_QA_SCHOOL_SITE_VERSION:-0.4.27}
    [[ "$bells" == 1 || "$bells" == 2 ]]
    [[ "$site_version" == 0.4.27 || ( "$site_version" == 0.4.28 && "$candidate" == 1 ) ]]
    if [[ "$bells" == 2 ]]; then [[ "$candidate" == 1 && -z "$udp_ab" ]]; fi
    if [[ "$candidate" == 1 ]]; then [[ "$count" == 9 && "$quality" == high && -z "$trial" ]]; fi
    if [[ -n "$udp_ab" ]]; then [[ "$candidate" == 1 && ( "$udp_ab" == 1ms || "$udp_ab" == 500us ) ]]; fi
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
    local stage=prepare
    phase() {
        stage=$1
        printf 'QA_SCHOOL_PHASE %s\n' "$stage"
        vm "printf '%s\\n' '$stage' > qa-phase.tmp; mv qa-phase.tmp qa-phase" >/dev/null
    }
    stop_fixture() {
        local rc=$?
        trap - EXIT
        # All payloads/keys stay in the disposable fixture; upload only counters and sanitized receipts.
        if [[ -f "$q/counters.jsonl" ]]; then cp "$q/counters.jsonl" "$results/counters.jsonl"; fi
        if [[ -f "$q/load.log" ]]; then grep '^QA_SCHOOL_' "$q/load.log" > "$results/media.log" || true; fi
        for f in active-captures.json board.jsonl closed.json udp-probe.json; do
            if [[ -f "$q/$f" ]]; then cp "$q/$f" "$results/$f"; fi
        done
        if (( rc != 0 )); then
            echo "QA_SCHOOL_FAILURE stage=$stage bell=${bell_no:-0}; no production VPS was contacted" >&2
            if [[ -s "$q/udp-probe.json" ]]; then
                jq -c 'del(.stable.bins,.stable.bounds_us,.burst.bins,.burst.bounds_us)' "$q/udp-probe.json" | sed 's/^/QA_SCHOOL_AUDIO_PARTIAL /'
            fi
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
    trap 'echo "QA_SCHOOL_STAGE $stage failed at line $LINENO: $BASH_COMMAND" >&2' ERR
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
    # The historical experiments still use their immutable baseline and their
    # explicitly labelled disposable cap edits. Never patch a candidate package.
    local site=baci-classroom-site-upgrade-v0.4.26.zip plugin=school-classroom-plugin-v0.4.26.zip
    local digest=493b09e48376222001f728616d0fc0445a75c29951bdf488a1e8467acd35bebf
    local patched=$count
    if [[ "$candidate" == 1 ]]; then
        site=baci-classroom-site-upgrade-v$site_version.zip
        plugin=school-classroom-plugin-v$site_version.zip
        digest=$(sha256sum "$root/artifacts/$site" | cut -d ' ' -f 1)
        patched=0
        vm 'sudo grep -Fxq "    batch_size: 64" /etc/school-classroom/livekit.yaml && sudo grep -Fxq "    max_flush_interval: 1ms" /etc/school-classroom/livekit.yaml && sudo grep -Fxq "  udp_port: 7882" /etc/school-classroom/livekit.yaml'
        echo "QA_SCHOOL_VARIANT packaged site $site_version, UDP batching enabled by installer; no cap overrides"
    fi
    vm "cat baci-classroom-vps-offline/$site" > "$q/$site"
    printf '%s  %s\n' "$digest" "$q/$site" | sha256sum --strict -c
    python3 - "$q" "$site" "$plugin" "$patched" <<'PY_SITE'
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
    if [[ "$patched" == 9 ]]; then
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
    if [[ -n "$trial" ]]; then
        # Apply only to the fresh VM with the known synthetic pair. The loopback
        # debugger is a QA instrument, never a production configuration.
        vm "sudo /usr/bin/python3 -IB - '$trial'" <<'PY_SFU'
import json
from pathlib import Path
import subprocess
import sys

variant = sys.argv[1]
assert variant in ('baseline', 'udp-batch')
pair = json.loads(Path('/etc/school-classroom/site-pairing.json').read_text())
assert pair['signal_url'] == 'wss://media.example.test' and pair['site'] == 'https://school.example.test/campus'
assert Path('/home/vpsqa/qa-media').read_text().strip() == 'media.example.test'
config = Path('/etc/school-classroom/livekit.yaml')
body = config.read_text()
anchor = '  udp_port: 7882\n'
assert 'bind_addresses: [127.0.0.1]\n' in body and body.count(anchor) == 1
assert 'batch_io:' not in body and 'debug_handler:' not in body
if variant == 'udp-batch':
    body = body.replace(anchor, anchor + '  batch_io:\n    batch_size: 64\n    max_flush_interval: 1ms\n')
config.write_text(body + '\ndebug_handler:\n  port: 7070\n')
subprocess.run(['systemctl', 'restart', 'school-classroom-sfu.service'],check=True)
print(f'QA_SCHOOL_TRIAL {variant}: UDP 7882, loopback-only profiling in disposable VM')
PY_SFU
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
    stage=doctor
    vm 'set -o pipefail; sudo baci-vps doctor --online --wait | tee school-online.log; grep -Fxq school_recorder=ready school-online.log'
    if [[ -n "$udp_ab" ]]; then
        # Both trials use the packaged .27/r4/LiveKit baseline. Only the
        # interval changes, on a disposable synthetic installation.
        stage=udp_config
        vm "sudo /usr/bin/python3 -IB - '$udp_ab'" <<'PY_BATCH'
import json
from pathlib import Path
import subprocess
import sys

interval = sys.argv[1]
assert interval in ('1ms', '500us')
pair = json.loads(Path('/etc/school-classroom/site-pairing.json').read_text())
assert pair['signal_url'] == 'wss://media.example.test' and pair['site'] == 'https://school.example.test/campus'
config = Path('/etc/school-classroom/livekit.yaml')
body = config.read_text()
anchor = '  batch_io:\n    batch_size: 64\n    max_flush_interval: 1ms\n'
assert body.count(anchor) == 1 and 'debug_handler:' not in body
config.write_text(body.replace(anchor, anchor.replace('1ms', interval)) + '\ndebug_handler:\n  port: 7070\n')
subprocess.run(['systemctl', 'restart', 'school-classroom-sfu.service'],check=True)
print(f'QA_SCHOOL_UDP interval={interval} batch_size=64 profile=loopback')
PY_BATCH
        touch "$q/udp-probe"
        vm 'sudo mount -t tracefs tracefs /sys/kernel/tracing' || true
    fi
    vm "awk -F': ' '/^model name/{print \$2; exit}' /proc/cpuinfo" > "$q/cpu-model.txt"
    send "$root/tests/fixtures/vps-school-sample.py"
    local bell_no live_start tick eligible_first=''
    for bell_no in $(seq 1 "$bells"); do
        printf '%s\n' "$bell_no" > "$q/bell-number.tmp"
        mv "$q/bell-number.tmp" "$q/bell-number"
        phase idle
        vm "sudo /usr/bin/python3 -IB ./vps-school-sample.py qa-phase ${udp_ab:+--sfu-syscalls}" > "$q/counters.jsonl" &
        local sampler_pid=$!
        sleep 5
        kill -0 "$sampler_pid"
        phase join
        ( umask 077; school tickets > "$q/tickets.json" )
        as_runner bash -c 'ulimit -Sn 8192; export GOMAXPROCS=2; exec "$@"' qa "$client" --fixture "$q" "$quality" > "$q/load.log" 2>&1 &
        local load_pid=$!
        for n in $(seq 1 120); do
            if [[ -f "$q/peers-joined" ]]; then break; fi
            kill -0 "$load_pid"
            sleep .25
        done
        test -f "$q/peers-joined"
        phase warmup
        for n in $(seq 1 60); do
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
        live_jobs > "$q/active-captures.json"
        grep '^QA_SCHOOL_JOIN ' "$q/load.log"
        echo "QA_SCHOOL_ACTIVE confirmed captures=$count vp8=$quality steady_window_s=$seconds"
        phase live
        if [[ -n "$udp_ab" ]]; then touch "$q/audio-steady"; fi
        live_start=$SECONDS tick=0
        while (( SECONDS - live_start < seconds )); do
            tick=$((tick+1))
            as_runner curl --disable --noproxy '*' --fail --silent --show-error --max-time 3 \
                --data "$tick" 'http://127.0.0.1:18391/qa-local-board' >> "$q/board.jsonl"
            sleep 2
        done
        echo "QA_SCHOOL_BOARD active_whiteboards=7 synthetic_pen_lines_per_room=$tick sample_delta_checks=$((7*tick))"
        kill -0 "$load_pid"
        kill -0 "$sampler_pid"
        live_jobs >/dev/null
        if [[ "$bell_no" == 2 ]]; then
            test "$(date +%s)" -ge "$eligible_first"
            school status 1 | jq -e 'length == 9 and all(.[]; .state == "queued" and .rendered_at == null and .sent == 0)' >/dev/null
            test ! -f "$q/uploads.log"
            echo 'QA_SCHOOL_BACK_TO_BACK prior nine already eligible, but next live bell still prevents rendering and delivery'
        fi
        vm 'test -z "$(sudo find /var/lib/school-classroom-recorder \( -name "*.mp4" -o -name .render \) -print -quit)"'
        if [[ -n "$udp_ab" ]]; then
            phase reconnect
            # Rejoin two listen-only pupils in each high-video room while all
            # publishers continue. Their subscriptions stimulate new keyframes.
            touch "$q/audio-reconnect"
            for n in $(seq 1 22); do
                if [[ -s "$q/udp-probe.json" ]]; then break; fi
                kill -0 "$load_pid"
                sleep 1
            done
            test -s "$q/udp-probe.json"
            jq -ce 'select(.rejoin_peers == 4 and .stable.matched > 10000 and .burst.matched > 1000)' "$q/udp-probe.json" \
                | sed 's/^/QA_SCHOOL_AUDIO /'
            live_jobs >/dev/null
        fi
        if [[ -n "$trial$udp_ab" ]]; then
            phase profile
            if vm 'curl --disable --noproxy "*" --fail --silent --show-error --max-time 18 "http://127.0.0.1:7070/debug/pprof/profile?seconds=12"' > "$q/sfu.pprof" && [[ -s "$q/sfu.pprof" ]]; then
                cp "$q/sfu.pprof" "$results/sfu.pprof"
            elif [[ -n "$trial" ]]; then
                echo 'QA_SCHOOL_PROFILE legacy profile requested but failed' >&2
                exit 1
            else
                echo 'QA_SCHOOL_PROFILE experimental pprof unavailable; live metrics are independent'
            fi
            live_jobs >/dev/null
        fi
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
        if (( bell_no < bells )); then
            eligible_first=$(school status 1 | jq -er '[.[] | .not_before] | min | select(type=="number")')
            phase halt
            wait "$sampler_pid"
            mkdir "$q/bell-1"
            cp "$q/cpu-model.txt" "$q/bell-1/"
            mv "$q/"{counters.jsonl,load.log,board.jsonl,active-captures.json} "$q/bell-1/"
            rm "$q/"{tickets.json,peers-joined,media-ready,media-stop,media-paused,rooms-closed}
        fi
    done
    phase wait
    school status > "$q/closed.json"
    if (( count > 0 )); then
        local eligible
        eligible=$(jq -er '[.[] | .not_before] | min | select(type=="number")' "$q/closed.json")
        for n in $(seq 1 75); do if (( $(date +%s) >= eligible )); then break; fi; sleep 1; done
        phase render
        all_delivered() {
            local no ok=0
            for no in $(seq 1 "$bells"); do
                local status="$q/closed.json"
                if (( no < bells )); then status="$q/bell-1/closed.json"; fi
                school status "$no" > "$status"
                jq -e 'all(.[]; .state != "failed" and .state != "uncertain" and .state != "discarded")' "$status" >/dev/null || return 2
                if ! jq -e --argjson n "$count" 'length==$n and all(.[]; .state=="delivered" and .cleaned)' "$status" >/dev/null; then ok=1; fi
            done
            return "$ok"
        }
        local wait_checks=220 rc
        if [[ "$bells" == 2 ]]; then wait_checks=320; fi
        for n in $(seq 1 "$wait_checks"); do
            if all_delivered; then break; else rc=$?; fi
            if [[ "$rc" != 1 ]]; then return "$rc"; fi
            sleep 3
        done
        all_delivered
        test "$(wc -l < "$q/uploads.log")" -eq "$((count*bells))"
        if [[ "$bells" == 2 ]]; then
            local closed_second
            closed_second=$(jq -er '[.[] | .closed_at] | max | select(type=="number")' "$q/closed.json")
            jq -e --argjson t "$closed_second" 'all(.[]; .rendered_at >= $t)' "$q/bell-1/closed.json" >/dev/null
            echo 'QA_SCHOOL_BACK_TO_BACK both queues settled once, no first-bell render committed during the second bell'
        fi
        echo "QA_SCHOOL_RECEIPTS completed=$((count*bells)) after_last_bell_first_eligible_s=$(($(date +%s)-eligible))"
    else
        test ! -f "$q/uploads.log"
    fi
    phase halt
    wait "$sampler_pid"
    vm "set -e; test -z \"\$(sudo find '$spool' \\( -name '*.baci' -o -name '*.mp4' \\) -print -quit)\"; grep -Eq '^oom_kill +0$' /proc/vmstat"
    local variant=${trial:-standard}
    if [[ "$candidate" == 1 ]]; then variant=candidate; fi
    if [[ "$bells" == 2 ]]; then
        mkdir -p "$results/bell-1" "$results/bell-2" "$q/bell-2"
        for f in "$q"/received-*-B2.mp4; do name=$(basename "$f"); mv "$f" "$q/bell-2/${name%-B2.mp4}.mp4"; done
        mv "$q"/received-*.mp4 "$q/bell-1/"
        cp "$q/"{counters.jsonl,load.log,board.jsonl,closed.json,active-captures.json,cpu-model.txt} "$q/bell-2/"
        for bell_no in 1 2; do
            local evidence="$results/bell-$bell_no"
            cp "$q/bell-$bell_no/"{counters.jsonl,board.jsonl,closed.json,active-captures.json} "$evidence/"
            grep '^QA_SCHOOL_' "$q/bell-$bell_no/load.log" > "$evidence/media.log"
            echo "QA_SCHOOL_VERIFY bell=$bell_no"
            python3 "$root/tests/fixtures/vps-school-results.py" "$q/bell-$bell_no" "$evidence/measurement.json" "$count" "$quality" "$seconds" "$variant" --bell "$bell_no"
        done
    else
        python3 "$root/tests/fixtures/vps-school-results.py" "$q" "$results/measurement.json" "$count" "$quality" "$seconds" "$variant"
    fi
    echo 'QA_SCHOOL_PASS exact UDP workload and all planned receipts verified'
)
