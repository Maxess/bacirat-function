#!/usr/bin/env bash
# Build the recorded .27/r4 baseline from the immutable 1.3.3 dependency closure.
# QA only: this is not the published upgrade tarball.
set -euo pipefail
original=$(realpath "$1")
output=$(realpath -m "$2")
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
printf '%s  %s\n' 33f90e0fa350078bdfaa487adda3ba66f0182eb272f0f15e1b67a927ad3fe176 "$original" | sha256sum --strict -c
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
tar -xzf "$original" -C "$work"
bundle="$work/baci-classroom-vps-offline"
# Keep the exact 1.3.4 installer, not the working branch's possibly changed defaults.
base=25344f602b81ef24882c9117acf4702875703f98
for f in install.sh setup.py START-HERE-fa.md; do
    git -C "$root" show "$base:server/offline/$f" > "$bundle/$f"
done
git -C "$root" show "$base:server/install.py" > "$bundle/vps_media.py"
site=baci-classroom-site-upgrade-v0.4.27.zip
pack=baci-classroom-recorder-v0.4.23-r4.zip
rec_digest=08da383d01c2d96d596e7d0df9e9bc9d751cf5edc8d8d09f020790c8258fa1bc
printf '%s  %s\n' e984a1c6d9327aee19ad90721775d1865231fd7461068002a6327622fa12a8b1 "$root/artifacts/$site" \
    "$rec_digest" "$root/artifacts/$pack" | sha256sum --strict -c
rm "$bundle/payload/baci-classroom-recorder-v0.4.23-r3.zip" "$bundle/baci-classroom-site-upgrade-v0.4.26.zip"
cp "$root/artifacts/$site" "$bundle/"
cp "$root/artifacts/$pack" "$bundle/payload/"
jq --arg sha "$(git -C "$root" rev-parse "$base")" --arg pack "$pack" --arg digest "$rec_digest" \
    '.version="1.3.4" | .git=$sha | .qa_dependency_base="1.3.3" | del(.pinned_payload_sha256["baci-classroom-recorder-v0.4.23-r3.zip"]) | .pinned_payload_sha256[$pack]=$digest' \
    "$bundle/BUILD.json" > "$work/build.json"
mv "$work/build.json" "$bundle/BUILD.json"
(cd "$bundle"; find . -type f ! -name SHA256SUMS -printf '%P\0' | sort -z | xargs -0 sha256sum > SHA256SUMS)
tar -cf - -C "$work" baci-classroom-vps-offline | gzip -n -1 > "$output"
sha256sum "$output"
