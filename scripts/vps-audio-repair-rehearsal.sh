#!/usr/bin/env bash
# Sourced by the disposable VPS harness; no production rooms or jobs.
run_audio_repair_rehearsal() {
    send "$root/artifacts/baci-recorder-audio-repair-v0.4.23-r2.py"
    vm 'sudo bash -s' <<'AUDIO_REPAIR'
set -euo pipefail
repair=(/usr/bin/python3 -IB ./baci-recorder-audio-repair-v0.4.23-r2.py)
replay=/opt/school-classroom-recorder/releases/0.4.23/replay.js
units=(school-classroom-{sfu,admission,https,recorder,postprocess}.service)
systemctl show -p InvocationID "${units[@]}" > audio-invocations.before
sha256sum /etc/school-classroom/{offline-vps.json,site-pairing.json,admission.json,livekit.yaml} \
    /etc/school-classroom-recorder.json /var/lib/school-classroom-recorder/worker.json > audio-config.before
sha256sum "$replay" > audio-stock.before
"${repair[@]}" --check-only | tee audio-preflight.log
grep -Fq 'installed=stock rooms_and_queue=idle' audio-preflight.log
grep -Fxq 'check_only=yes no_changes=yes' audio-preflight.log
sha256sum -c --quiet audio-stock.before
# Three signed idle queries surround the cutover. A late refusal must undo it.
touch /run/baci-qa-deny-third-maintenance
if "${repair[@]}" --apply > audio-late-refusal.log 2>&1; then exit 1; fi
rm /run/baci-qa-deny-third-maintenance
grep -F 'Previous replay file restored or unchanged.' audio-late-refusal.log
sha256sum -c --quiet audio-stock.before
"${repair[@]}" --apply | grep -Fx 'audio_repair=ok installed=r2 mp4_check=ok services=unchanged'
sha256sum "$replay" > audio-r2.sha
test "$(cut -d' ' -f1 audio-stock.before)" != "$(cut -d' ' -f1 audio-r2.sha)"
"${repair[@]}" --apply | grep -Fx 'audio_repair=ok installed=r2 already_selected=yes no_changes=yes'
"${repair[@]}" --rollback | grep -Fx 'audio_repair=ok installed=stock mp4_check=ok services=unchanged'
sha256sum -c --quiet audio-stock.before
"${repair[@]}" --apply | grep -Fx 'audio_repair=ok installed=r2 mp4_check=ok services=unchanged'
sha256sum -c --quiet audio-r2.sha
sha256sum -c --quiet audio-config.before
systemctl show -p InvocationID "${units[@]}" | cmp audio-invocations.before -
echo 'QA_AUDIO repair and rollback verified in the running renderer; settings, keys and all five service invocations unchanged.'
AUDIO_REPAIR
}
