#!/usr/bin/env bash
# Releases are never rebuilt here; publish the exact successful VM-tested bytes.
set -euo pipefail
manifest=$1
repo=${GITHUB_REPOSITORY:?}
branch=${GITHUB_REF_NAME:?}
version=$(jq -er '.version' "$manifest")
run=$(jq -er '.run_id' "$manifest")
commit=$(jq -er '.commit' "$manifest")
checksums=$(jq -er '.checksums_sha256' "$manifest")
installer=$(jq -er '.installer_sha256' "$manifest")
artifact_sha=$(jq -er '.artifact_sha256' "$manifest")
setup_run=$(jq -er '.setup_run_id' "$manifest")
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && "$run" =~ ^[0-9]+$ && "$setup_run" =~ ^[0-9]+$ && "$commit" =~ ^[a-f0-9]{40}$ \
    && "$checksums" =~ ^[a-f0-9]{64}$ && "$installer" =~ ^[a-f0-9]{64}$ && "$artifact_sha" =~ ^[a-f0-9]{64}$ ]]
name="baci-classroom-vps-offline-v$version-ubuntu24.04-amd64"
tag="vps-offline-v$version"
base="https://github.com/$repo/actions/runs/$run"
setup_evidence="https://github.com/$repo/actions/runs/$setup_run"
guide="baci-vps-install-v$version-fa.md"
case "$version" in
    1.3.3) site_version=0.4.26; manager_version=0.1.33; revision=r3 ;;
    1.3.4) site_version=0.4.27; manager_version=0.1.34; revision=r4 ;;
    *) echo "No reviewed school/Recorder companion for $version" >&2; exit 1 ;;
esac
site=baci-classroom-site-upgrade-v$site_version.zip
recorder=baci-classroom-recorder-v0.4.23-$revision.zip
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
stage=validate-ci-run
release_error() {
    # Preserve useful GitHub API errors even where the Actions log is unavailable.
    while IFS= read -r line; do
        line=${line//'%'/'%25'}
        line=${line//$'\r'/'%0D'}
        printf '::error title=GitHub Release API::%s\n' "${line:0:500}"
    done < <(sed -E 's#(https?://[^[:space:]?]+)\?[^[:space:]"]+#\1?[redacted]#g' "$work/release.log" | tail -10)
    return 1
}
trap 'echo "::error title=VPS release::Failed at $stage; public release not confirmed."' ERR
# Tie the bytes to a completed run of the required workflow on this branch.
gh api "repos/$repo/actions/runs/$run" | jq -e --arg sha "$commit" --arg branch "$branch" '
    .status == "completed" and .conclusion == "success" and .head_sha == $sha and .head_branch == $branch
    and (.path | split("@")[0]) == ".github/workflows/vps-offline.yml"' >/dev/null
stage=verify-live-and-compact-vm-gates
gh api "repos/$repo/actions/runs/$run/jobs" | jq -e '
    any(.jobs[]; .name == "ubuntu-noble-installer" and .conclusion == "success"
        and any(.steps[]; .name == "Verify 512 MiB live and full installs, both TLS modes and WebRTC"
                and .conclusion == "success")
        and any(.steps[]; .name == "Verify packaged compact recording and safe add-on"
                and .conclusion == "success")
        and any(.steps[]; .name == "Verify the archive keeps the older Noble Server base intact"
                and .conclusion == "success")
        and any(.steps[]; .name == "Verify actual offline compact install and recording on Noble GA Server"
                and .conclusion == "success")
        and any(.steps[]; .name == "Require webcam layout and both simulated group receipts from the packaged installs"
                and .conclusion == "success"))' >/dev/null
stage=verify-distribution-helpers
# These are separate assets, not a modification or rebuild of the tested archive.
setup_commit=$(gh api "repos/$repo/actions/runs/$setup_run" | jq -er --arg branch "$branch" '
    select(.status == "completed" and .conclusion == "success" and .head_branch == $branch
           and (.path | split("@")[0]) == ".github/workflows/vps-resilience.yml") | .head_sha')
[[ "$setup_commit" =~ ^[a-f0-9]{40}$ ]]
# A green transport test for a previous archive is not evidence for this one.
gh api "repos/$repo/contents/docs/vps-offline-release.json?ref=$setup_commit" --jq .content | base64 -d \
    | jq -e --arg ver "$version" --arg run "$run" --arg installer "$installer" --arg artifact "$artifact_sha" '
        .version == $ver and (.run_id | tostring) == $run
        and .installer_sha256 == $installer and .artifact_sha256 == $artifact' >/dev/null
gh api "repos/$repo/actions/runs/$setup_run/jobs" | jq -e '
    any(.jobs[]; .name == "offline-and-tcp" and .conclusion == "success"
        and any(.steps[]; .name == "Check offline profile selection, managed swap and real TCP ICE on 512 MiB"
                and .conclusion == "success"))' >/dev/null
for helper in install-vps.sh scripts/prepare-vps-swap.sh; do
    gh api "repos/$repo/contents/$helper?ref=$setup_commit" --jq .content | base64 -d | cmp - "$helper"
done
grep -Fxq "readonly VERSION=$version" install-vps.sh
grep -Fxq "readonly SHA256=$installer" install-vps.sh
stage=verify-ci-artifact
gh api "repos/$repo/actions/runs/$run/artifacts" | jq -e --arg digest "sha256:$artifact_sha" '
    [.artifacts[] | select(.name == "classroom-vps-offline-verified")]
    | length == 1 and .[0].expired == false and .[0].digest == $digest' >/dev/null
stage=download-verified-build
gh run download "$run" --repo "$repo" --name classroom-vps-offline-verified --dir "$work"
sha_file="$name-SHA256SUMS.txt"
printf '%s  %s\n' "$checksums" "$work/$sha_file" | sha256sum --check --strict
stage=check-published-inventory
(
    cd "$work"
    cmp <(printf '%s\n' "$name.tar.gz" "$name-ubuntu-source.tar" "$site" | sort) \
        <(cut -c67- "$sha_file" | sort)
    sha256sum --check --strict "$sha_file"
    grep -Fxq "$installer  $name.tar.gz" "$sha_file"
    tar -xOf "$name.tar.gz" "baci-classroom-vps-offline/$site" | cmp - "$site"
    BUILD=$(tar -xOf "$name.tar.gz" baci-classroom-vps-offline/BUILD.json)
    jq -e --arg sha "$commit" --arg ver "$version" --arg recorder "$recorder" '.git == $sha and .version == $ver and .arch == "amd64" and .ubuntu == "24.04"
        and .tls_modes == ["auto", "files"] and .minimum_visible_ram_kib == 393216
        and .install_profiles == ["live", "compact", "full"] and .full_visible_ram_kib == 1835008
        and .recording_by_default == true and .school_recorder_user_agent == "Baci-Classroom-Recorder/0.4.23"
        and (.pinned_payload_sha256[$recorder] | test("^[a-f0-9]{64}$"))
        and .compact_visible_ram_kib == 458752 and .compact_swap_kib == 512000
        and .compact_no_swap_visible_ram_kib == 917504' <<< "$BUILD" >/dev/null
)
stage=prepare-corresponding-source-assets
# GitHub Releases cap a single asset at 2 GiB. The corresponding source for
# the full Ubuntu closure can exceed that, although the installer is much smaller.
source_tar="$name-ubuntu-source.tar"
source_assets=("$source_tar")
if (( $(stat -c %s "$work/$source_tar") >= 2000000000 )); then
    (
        cd "$work"
        split -b 950M -d -a 2 --numeric-suffixes=1 "$source_tar" "$source_tar.part-"
        whole=$(sha256sum "$source_tar" | cut -d ' ' -f1)
        cat "$source_tar".part-* | sha256sum | grep -Fq "$whole  -"
        printf '%s  %s\n' "$whole" "$source_tar" > "$source_tar.reassembled-sha256.txt"
    )
    source_assets=()
    for part in "$work/$source_tar".part-* "$work/$source_tar.reassembled-sha256.txt"; do source_assets+=("$(basename "$part")"); done
    rm -- "$work/$source_tar"
fi
stage=prepare-distribution-assets
install -m 0644 install-vps.sh "$work/install-vps.sh"
install -m 0644 scripts/prepare-vps-swap.sh "$work/baci-prepare-swap.sh"
install -m 0644 docs/install-vps-from-github.fa.md "$work/$guide"
assets=("$name.tar.gz" install-vps.sh baci-prepare-swap.sh "$guide" "$site" "${source_assets[@]}")
(
    cd "$work"
    sha256sum "${assets[@]}" > "$sha_file"
)
assets+=("$sha_file")
stage=prepare-release-notes
cat > "$work/notes.md" <<EOF_NOTES
## کلاس زنده، ضبط و تحویل فیلم روی یک VPS — Ubuntu 24.04 LTS amd64

**[راهنمای نصب فارسی](https://github.com/$repo/releases/download/$tag/$guide)** · **[آرشیو نصب](https://github.com/$repo/releases/download/$tag/$name.tar.gz)** · **[اسکریپت نصب](https://github.com/$repo/releases/download/$tag/install-vps.sh)** · **[SHA-256](https://github.com/$repo/releases/download/$tag/$sha_file)**

روی VPS تازهٔ سازگار، پس از بررسی هش، اسکریپت و آرشیو را کنار هم با SFTP بفرست و \`bash ./install-vps.sh\` بزن. \`--check\` اختیاری و بدون نصب است. می‌شود همین آرشیو را مستقیم از انتشار یا آینهٔ HTTPS گرفت؛ برای نصب وابستگی‌ها به مخزن آنلاین Ubuntu یا CDN نیازی نیست. این آرشیو همان فایل آزموده‌شده در ماشین‌های ایزوله است. روی VPS ضبطِ فعلی، این مسیر را برای ارتقای درجا اجرا نکن.

- **صدای فیلم:** اصلاح تیک‌تیک وسط صحبت در بستهٔ نصب تازه است. وصلهٔ r2 جداگانه لازم نیست.
- **نمای فیلم:** ارائه‌دهنده روی «نمایش در ضبط» بالای یک وب‌کم روشن می‌زند تا آن وب‌کم در قاب بزرگ بر تخته اولویت بگیرد. نمای زنده و اولویت صفحهٔ مشترک تغییر نمی‌کند. سربرگ فیلم‌های تازه: **طراحی و توسعه : سید مهدی بیات**.
- **رفع ۴۰۳ Recorder داخل نصب:** درخواست‌های ضبطِ احرازشده با شناسهٔ واقعی Recorder به سایت می‌رسند؛ هیچ فایل اصلاحی جدا، غیرفعال‌کردن TLS یا تغییر مجوزهای سایت لازم نیست. بعد از جفت‌سازی و فعال‌کردن ضبط، \`sudo baci-vps doctor --online --wait\` heartbeat همان Recorder را بررسی می‌کند.
- **پیش‌فرض ضبط را حفظ می‌کند:** \`compact\` از ۴۴۸ MiB RAM قابل‌مشاهده؛ زیر ۸۹۶ MiB، swap فعال در حد ۵۱۲ MiB لازم است. \`full\` از ۱۷۹۲ MiB قابل‌مشاهده. \`live\` فقط با انتخاب صریح، بدون ضبط. فایل \`baci-prepare-swap.sh\` اختیاری است و فقط با تأیید \`--apply\` روی VPS سازگار swap اختصاصی می‌سازد.
- **HTTPS خودکار:** Caddy برای نام رسانه با رکورد A مستقیم و TCP 443 باز از Let’s Encrypt گواهی می‌گیرد. اگر VPS به مرجع صدور نمی‌رسد، نصب و HTTPS با گواهی عمومی آماده و \`--tls files\` بدون دسترسی به آن مرجع ممکن است. پورت ۸۰ لازم نیست. برای رسانه UDP 7882 مسیر ترجیحی است و TCP 7881 جایگزین در شبکهٔ سازگار؛ HTTPS به‌تنهایی رسانه را عبور نمی‌دهد.
- **سایت مدرسه:** ZIP کوچک \`$site\` شامل مدیر $manager_version و کلاس $site_version است. VPS تازه فایل جفت‌سازی خصوصی تازه می‌سازد که تنها در پنل HTTPS مدیر کل وارد می‌شود. روی نصب مدیریت‌شدهٔ سازگار در حالت \`live\` می‌توان با \`--add-recorder\` در زمان بیکاری و بدون تغییر اتصال قبلی ضبط را اضافه کرد؛ [شرایط آن در راهنما](https://github.com/$repo/releases/download/$tag/$guide) آمده است.
- فایل‌های \`*-ubuntu-source.tar\` و \`*.tar.part-*\` صرفاً کد منبع متناظر Ubuntu هستند؛ برای نصب دانلودشان نکن. برای استفاده از منبع، پس از تأیید هش همهٔ بخش‌ها، روی رایانه با \`cat $source_tar.part-* > $source_tar\` یکپارچه و هش بازسازی‌شده را مقایسه کن.

**رفع ناسازگاری ۱٫۳٫۰ با Ubuntu اولیه:** نسخه‌های هم‌خوان D-Bus، GLib و Python از مخزن امضاشده همراه بسته‌اند. پیش‌بررسی طرح واقعی APT را بدون تغییر بسته‌ها اجرا می‌کند. حذف یا پایین‌آوردن نسخه‌ها مجاز نیست و به بازنشانی VPS یا به‌روزرسانی کلی Ubuntu نیازی نیست.

[آزمون همین آرشیو]($base): نصب و ساخت MP4 روی تصویر رسمی Ubuntu Server 24.04 اولیه با ۱ GiB بدون swap و روی تصویر به‌روز با ۵۱۲ MiB و swap؛ یک دبیر و ۱۰ شاگرد مصنوعی روی UDP، ضبط با heartbeat تازه پس از ۴۰۳ کنترل‌شده، دریافت و رمزگشایی MP4 و آزمون نمای وب‌کم/بازگشت تخته در مقصدهای شبیه‌سازی‌شدهٔ بله و روبیکا، و پاک‌سازی بدون ساخت و ارسال بعد از «نفرست» یا لغو مقصد. [آزمون جداگانهٔ نصب‌یار، swap و دو مرورگر روی TCP]($setup_evidence). آزمون ACME با CA آزمایشی بوده؛ پیش از کلاس اصلی، مسیر رسانه از دو شبکه و دریافت و پخش فیلم در گروه واقعی مدرسه را جدا بررسی کن.
EOF_NOTES
stage=find-or-create-draft
find_release() {
    gh api "repos/$repo/releases" --paginate --jq ".[] | select(.tag_name == \"$tag\")"
}
release=$(find_release)
if [[ -z "$release" ]]; then
    gh release create "$tag" \
        --repo "$repo" --target "$commit" --title "Classroom VPS v$version · Live, recording and HTTPS · Ubuntu 24.04 amd64" \
        --draft --latest=false --notes-file "$work/notes.md" > "$work/release.log" 2>&1 || release_error
    release=$(find_release)
fi
# The Actions integration may be prohibited from making a release targeting an
# earlier commit. A maintainer-created draft is safe only with the exact target
# and a digest-matching subset of the expected assets. Never alter public assets.
id=$(jq -ser --arg commit "$commit" '
    select(length == 1) | .[0] | select(.draft == true and .prerelease == false and .target_commitish == $commit) | .id' <<<"$release")
[[ "$id" =~ ^[0-9]+$ ]]
stage=inventory-draft-assets
asset_inventory() {
    gh api "repos/$repo/releases/$id/assets" --paginate --jq '.[] | [.name,.size,.digest] | @tsv'
}
asset_inventory > "$work/github-assets.tsv"
: > "$work/expected-assets.tsv"
missing=()
for asset in "${assets[@]}"; do
    size=$(stat -c %s "$work/$asset")
    digest=$(sha256sum "$work/$asset" | cut -d ' ' -f1)
    line="$asset"$'\t'"$size"$'\t'"sha256:$digest"
    printf '%s\n' "$line" >> "$work/expected-assets.tsv"
    if ! grep -Fxq "$line" "$work/github-assets.tsv"; then missing+=("$work/$asset"); fi
done
while IFS= read -r line; do
    grep -Fxq "$line" "$work/expected-assets.tsv"
done < "$work/github-assets.tsv"
stage=upload-draft-assets
if (( ${#missing[@]} )); then
    gh release upload "$tag" "${missing[@]}" --repo "$repo" > "$work/release.log" 2>&1 || release_error
fi
stage=verify-github-asset-sha256
asset_inventory > "$work/github-assets.tsv"
cmp <(sort "$work/expected-assets.tsv") <(sort "$work/github-assets.tsv")
while IFS=$'\t' read -r asset size digest; do
    echo "::notice title=GitHub asset verified::$asset ($size bytes) $digest"
done < "$work/expected-assets.tsv"
stage=set-validated-release-notes
gh release edit "$tag" --repo "$repo" --notes-file "$work/notes.md" > "$work/release.log" 2>&1 || release_error
stage=publish-draft
gh release edit "$tag" --repo "$repo" --draft=false --latest=true > "$work/release.log" 2>&1 || release_error
stage=verify-public-release
gh api "repos/$repo/releases/tags/$tag" | jq -e --arg archive "$name.tar.gz" --arg digest "sha256:$installer" '
    .draft == false and any(.assets[]; .name == $archive and .digest == $digest)' >/dev/null
echo "::notice title=Public VPS release::https://github.com/$repo/releases/tag/$tag"
