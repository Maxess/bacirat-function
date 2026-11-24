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
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && "$run" =~ ^[0-9]+$ && "$commit" =~ ^[a-f0-9]{40}$ && "$checksums" =~ ^[a-f0-9]{64}$ ]]
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
    BUILD=$(tar -xOf "$name.tar.gz" baci-classroom-vps-offline/BUILD.json)
    jq -e --arg sha "$commit" --arg ver "$version" '.git == $sha and .version == $ver and .arch == "amd64" and .ubuntu == "24.04"' <<< "$BUILD" >/dev/null
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
## نصب آفلاین VPS تازه — Ubuntu 24.04 LTS amd64

- **برای نصب روی VPS** فقط \`$name.tar.gz\` را بگیر. \`START-HERE-fa.md\` داخل آرشیو و [راهنمای فارسی](https://github.com/$repo/blob/$commit/server/offline/README-fa.md) مراحل را توضیح می‌دهند.
- **برای پنل مدرسه** ZIP کوچک \`baci-classroom-site-pairing-v0.1.31.zip\` را روی رایانه باز کن. اگر کلاس ۰٫۴٫۲۴ از قبل نصب است فقط مدیر ۰٫۱٫۳۱ لازم است.
- \`*-ubuntu-source.tar\` یا بخش‌های \`*.tar.part-*\` کد منبع متناظر بسته‌های Ubuntu هستند؛ **روی VPS لازم نیستند**. اگر کد منبع را می‌خواهی، بعد از دریافت بخش‌ها و بررسی SHA-256، آن‌ها را با \`cat *-ubuntu-source.tar.part-* > $source_tar\` به هم بچسبان و SHA-256 فایل بازسازی‌شده را هم بررسی کن.

این بسته دانلود در زمان نصب ندارد. DNS، فایروال ارائه‌دهنده و گواهی HTTPS عمومی از پیش آماده لازم‌اند. برای اتصال واقعی به سایت و دستگاه‌ها شبکه لازم است. برای ارتقای VPS قبلی استفاده نشود.

[آزمون همین بایت‌ها روی Ubuntu تازه با خروجی اینترنت بسته]($base) — نصب و اجرای دوباره، TLS و API امضاشده، وب‌هوک، ضربان ضبط، MP4 sandbox‌شده و راه‌اندازی پس از reboot. روی مدرسه، گوشی یا VPS تولید نصب نشده است.
EOF_NOTES
gh release create "$tag" "${files[@]}" \
    --repo "$repo" --target "$commit" --title "Offline classroom VPS v$version · Ubuntu 24.04 amd64" \
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
