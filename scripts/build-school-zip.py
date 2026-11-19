#!/usr/bin/env python3
"""Build the test-host add-on ZIP with the pinned, locally served SDK."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SDK_VERSION = '2.24.1'
SDK_SHA512 = '8kkpTPuszlkza99XOV/QZVQG7M730i5gH52j0AZzsUMQpQfI9tEgW2JfDge7+Z2ZUUuBEtV3r2MYbALHFJ2HIg=='
SOURCE_REV = '726ef103c6980e2d0da63c2f0bc025a5d0ea11ce'
RELEASE = 'school-classroom-pilot-v0.1.0.zip'
FILES = [
    'README-fa.md', 'setup/menu-mtagbot.patch', 'setup/school-classroom.local.example.php',
    'upload/online-classroom/index.php', 'upload/online-classroom/core.php', 'upload/online-classroom/api.php',
    'upload/online-classroom/assets/classroom.js', 'upload/online-classroom/assets/classroom.css',
]


def build(sdk):
    if base64.b64encode(hashlib.sha512(sdk).digest()).decode() != SDK_SHA512:
        raise ValueError('SDK archive does not match pinned npm integrity')
    entries = {name: (ROOT / 'school-addon' / name).read_bytes() for name in FILES}
    with tarfile.open(fileobj=io.BytesIO(sdk), mode='r:gz') as package:
        for source, target in [('dist/livekit-client.umd.js', 'livekit-client.umd.js'), ('LICENSE', 'livekit-client.LICENSE.txt')]:
            with package.extractfile('package/' + source) as file:
                entries['upload/online-classroom/assets/' + target] = file.read()
    entries['manifest.json'] = (json.dumps({
        'addon': '0.1.0', 'mtagbot_reviewed_commit': SOURCE_REV, 'livekit_client': SDK_VERSION,
        'sha256': {name: hashlib.sha256(entries[name]).hexdigest() for name in sorted(entries)},
    }, indent=2, sort_keys=True) + '\n').encode()
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk-tgz', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts' / RELEASE)
    args = parser.parse_args()
    if (ROOT / 'plugin-manager' / 'school-plugins.php').is_file():
        parser.error('Manual v0.1.0 is archived at 885bd8f4754a74edbb3e496a0da9cddb19aef468; use build-managed-school.py in this revision')
    if args.sdk_tgz:
        sdk = args.sdk_tgz.read_bytes()
    else:
        with urlopen(f'https://registry.npmjs.org/livekit-client/-/livekit-client-{SDK_VERSION}.tgz', timeout=30) as response:
            sdk = response.read(20 * 1024 * 1024 + 1)
    entries = build(sdk)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o100644 << 16
            zipped.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    print(f'{args.output} ({args.output.stat().st_size:,} bytes)')


if __name__ == '__main__':
    main()
