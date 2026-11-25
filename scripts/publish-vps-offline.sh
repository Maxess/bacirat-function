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
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && "$run" =~ ^[0-9]+$ && "$commit" =~ ^[a-f0-9]{40}$ \
    && "$checksums" =~ ^[a-f0-9]{64}$ && "$installer" =~ ^[a-f0-9]{64}$ && "$artifact_sha" =~ ^[a-f0-9]{64}$ ]]
name="baci-classroom-vps-offline-v$version-ubuntu24.04-amd64"
tag="vps-offline-v$version"
base="https://github.com/$repo/actions/runs/$run"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
stage=validate-ci-run
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
                and .conclusion == "success"))' >/dev/null
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
    cmp <(printf '%s\n' "$name.tar.gz" "$name-ubuntu-source.tar" 'baci-classroom-site-pairing-v0.1.31.zip' | sort) \
        <(cut -c67- "$sha_file" | sort)
    sha256sum --check --strict "$sha_file"
    grep -Fxq "$installer  $name.tar.gz" "$sha_file"
    BUILD=$(tar -xOf "$name.tar.gz" baci-classroom-vps-offline/BUILD.json)
    jq -e --arg sha "$commit" --arg ver "$version" '.git == $sha and .version == $ver and .arch == "amd64" and .ubuntu == "24.04"
        and .tls_modes == ["auto", "files"] and .minimum_visible_ram_kib == 393216
        and .install_profiles == ["live", "compact", "full"] and .full_visible_ram_kib == 1835008
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
assets=("$name.tar.gz" 'baci-classroom-site-pairing-v0.1.31.zip' "${source_assets[@]}")
(
    cd "$work"
    sha256sum "${assets[@]}" > "$sha_file"
)
assets+=("$sha_file")
files=()
for asset in "${assets[@]}"; do files+=("$work/$asset"); done
stage=create-draft
if gh release view "$tag" --repo "$repo" >/dev/null 2>&1; then
    echo "::error title=VPS release::The tag already has a release. No asset was replaced."
    exit 1
fi
cat > "$work/notes.md" <<EOF_NOTES
## کلاس زنده و ضبط روی یک VPS — Ubuntu 24.04 LTS amd64

- **زنده بدون ضبط**: \`--profile live\` روی ۵۱۲ MiB RAM و ۱ vCPU بدون swap بررسی شده است. \`auto\` روی RAM کم همچنان این حالت را انتخاب می‌کند.
- **ضبط کم‌حافظه روی همان VPS**: \`--profile compact\` را صریح انتخاب کن. برای پلن ۵۱۲ MiB، دست‌کم ۴۴۸ MiB باید به لینوکس برسد و یک swap فعال در حد ۵۱۲ MiB لازم است. ضبط فشرده هنگام کلاس انجام می‌شود؛ ساخت و ارسال MP4 پس از بسته‌شدن، تأیید و تأخیر مدرسه، خارج از زنگ فعال است. آزمون ظرفیت کوتاه و مصنوعی است، نه تضمین چند کلاس یا همهٔ دوربین‌ها.
- **افزودن ضبط به نصب ۱٫۲٫۰ live**: مسیر جداگانهٔ \`--add-recorder\` فقط برای همان نصب مدیریت‌شده، جفت‌سازی موجود و بدون زنگ/فیلم معوق است. اول از راهنمای بسته و \`--check\` استفاده کن؛ نصب تازه یا وارد کردن مجدد جفت‌سازی لازم نیست.
- **برای نصب روی VPS** فقط \`$name.tar.gz\` را بگیر. \`START-HERE-fa.md\` داخل آرشیو و [راهنمای فارسی](https://github.com/$repo/blob/$commit/server/offline/README-fa.md) مراحل را توضیح می‌دهند.
- **برای سایت تازهٔ مدرسه** در همان راهنما از نصب Baci روی هاست PHP/MySQL شروع کن. ZIP کوچک \`baci-classroom-site-pairing-v0.1.31.zip\` را روی رایانه باز کن؛ مدیر ۰٫۱٫۳۱ و کلاس ۰٫۴٫۲۴ هستند. بستهٔ کامل سایت پایه را از منبع اصلی بگیر.
- \`*-ubuntu-source.tar\` یا بخش‌های \`*.tar.part-*\` کد منبع متناظر بسته‌های Ubuntu هستند؛ **روی VPS لازم نیستند**. اگر کد منبع را می‌خواهی، بعد از دریافت بخش‌ها و بررسی SHA-256، آن‌ها را با \`cat *-ubuntu-source.tar.part-* > $source_tar\` به هم بچسبان و SHA-256 فایل بازسازی‌شده را هم بررسی کن.

پیش‌نیازهای نرم‌افزاری از خود بسته نصب می‌شوند. با \`--tls auto\` و رکورد A مستقیم و TCP 443 باز، Caddy گواهی عمومی Let’s Encrypt را می‌گیرد، نصب و تمدید می‌کند؛ به گواهی قبلی یا پورت 80 نیازی نیست. اگر صدور آنلاین ممکن نیست، \`--tls files\` از گواهی آماده استفاده می‌کند و کاملاً آفلاین نصب می‌شود. مسیر عادی نصب، تنظیمات VPS موجود را بازنویسی نمی‌کند.

[آزمون همین بایت‌ها روی Ubuntu]($base) — یک دبیر و ۱۰ شاگرد مصنوعی روی یک VM ۵۱۲ MiB با swap، دریافت رسانه با UDP و تحویل MP4 قابل‌پخش به مقصد آزمایشی HTTPS، با تصویر متحرک، PDF و صدای دو میکروفون. رد ارسال و لغو مقصد نیز باید به حذف رسانه و نبود ارسال منجر شوند. نصب compact با صدور TLS-ALPN-01 در CA آزمایشی جداگانه بررسی شده است. آزمون افزودن ضبط و سلامت نصب قبلی روی VM یک GiB بدون swap انجام شده؛ آن نتیجه جایگزین آزمون ۵۱۲ MiB نیست. صدور عمومی، دستگاه‌ها و گروه واقعی مدرسه به بررسی جداگانه در محیط مقصد نیاز دارند.
EOF_NOTES
gh release create "$tag" "${files[@]}" \
    --repo "$repo" --target "$commit" --title "Classroom VPS v$version · Automatic TLS · Ubuntu 24.04 amd64" \
    --draft --latest=false --notes-file "$work/notes.md"
stage=verify-github-asset-sha256
# The REST release id works for both old and new gh CLI versions. The tag
# itself may not exist as a Git ref while this release is still a draft.
id=$(gh api "repos/$repo/releases" --paginate --jq ".[] | select(.tag_name == \"$tag\" and .draft == true) | .id")
[[ "$id" =~ ^[0-9]+$ ]]
gh api "repos/$repo/releases/$id/assets" --jq '.[] | [.name,.size,.digest] | @tsv' > "$work/github-assets.tsv"
for asset in "${assets[@]}"; do
    size=$(stat -c %s "$work/$asset")
    digest=$(sha256sum "$work/$asset" | cut -d ' ' -f1)
    grep -Fxq "$asset"$'\t'"$size"$'\t'"sha256:$digest" "$work/github-assets.tsv"
    echo "::notice title=GitHub asset verified::$asset ($size bytes) SHA-256 $digest"
done
stage=publish-draft
gh release edit "$tag" --repo "$repo" --draft=false --latest=false
echo "::notice title=Public VPS release::https://github.com/$repo/releases/tag/$tag"
