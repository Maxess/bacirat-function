#!/usr/bin/env python3
"""Assemble the managed classroom ZIP and the one-time pilot manager bundle."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts'
HOST = ROOT / 'plugin-manager'
SRC = ROOT / 'school-addon/upload/online-classroom'
LEGACY = ARTIFACTS / 'school-classroom-pilot-v0.1.0.zip'
LEGACY_SHA = 'bebc35a02f57346880373a034ee493b757a8017a12812a133ab957358d9de161'
VERSION = '0.2.3'
MANAGER_VERSION = '0.1.4'


def write_zip(path, entries):
    with ZipFile(path, 'w') as archive:
        for name, data in sorted(entries.items()):
            item = ZipInfo(name)
            item.external_attr = 0o100644 << 16
            archive.writestr(item, data, compress_type=ZIP_DEFLATED, compresslevel=9)


def plugin_entries():
    files = {name: (SRC / name).read_bytes() for name in (
        'index.php', 'core.php', 'api.php', 'deactivate.php', 'media.php', 'assets/classroom.js', 'assets/classroom.css',
    )}
    if hashlib.sha256(LEGACY.read_bytes()).hexdigest() != LEGACY_SHA:
        raise ValueError('Original reviewed SDK archive is not the published v0.1.0 ZIP')
    with ZipFile(LEGACY) as archive:
        for name in ('assets/livekit-client.umd.js', 'assets/livekit-client.LICENSE.txt'):
            files[name] = archive.read(f'upload/online-classroom/{name}')
    manifest = {
        'format': 1, 'id': 'school-classroom', 'version': VERSION, 'title': 'کلاس آنلاین',
        'menus': {role: 'کلاس آنلاین' for role in ('student', 'teacher', 'admin')},
        'routes': {'page': 'index.php', 'api': 'api.php'},
        'hooks': {'deactivate': 'deactivate.php'},
        'settings': [
            {'key': 'class_id', 'type': 'class', 'label': 'کلاس آزمایشی'},
            {'key': 'signal_url', 'type': 'wss', 'label': 'آدرس رسانه (wss://)'},
            {'key': 'api_key', 'type': 'token', 'label': 'شناسهٔ رسانه (API Key)', 'min': 12, 'max': 64},
            {'key': 'api_secret', 'type': 'secret', 'label': 'کلید خصوصی رسانه (API Secret)', 'min': 32, 'max': 128},
        ],
        'files': {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    files['plugin.json'] = (json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ARTIFACTS)
    parser.add_argument('--approve-reviewed-manifest', action='store_true', help='replace the trusted manifest pin after reviewing the payload')
    opts = parser.parse_args()
    entries = plugin_entries()
    expected = hashlib.sha256(entries['plugin.json']).hexdigest()
    manager = HOST / 'plugins/school-plugins.php'
    code = manager.read_text(encoding='utf-8')
    if f"const MPM_VERSION = '{MANAGER_VERSION}';" not in code:
        raise ValueError('Manager source and archive versions differ')
    pattern = rf"('{re.escape(VERSION)}' => ')([0-9a-f]{{64}}|CATALOG_SHA256_PLACEHOLDER)(')"
    matches = list(re.finditer(pattern, code))
    if len(matches) != 1:
        raise ValueError('Add one catalog pin for the new version before building')
    match = matches[0]
    if match[2] != expected:
        if not opts.approve_reviewed_manifest:
            raise ValueError('Managed package differs from approved catalog; review before publishing')
        code = code[:match.start(2)] + expected + code[match.end(2):]
        manager.write_text(code, encoding='utf-8')
    opts.output_dir.mkdir(parents=True, exist_ok=True)
    write_zip(opts.output_dir / f'school-classroom-plugin-v{VERSION}.zip', entries)
    write_zip(opts.output_dir / f'mtagbot-plugin-manager-v{MANAGER_VERSION}.zip', {
        'plugins/school-plugins.php': manager.read_bytes(), 'README-fa.md': (HOST / 'README-fa.md').read_bytes(),
    })
    print(f'Built both archives; classroom manifest SHA-256: {expected}')


if __name__ == '__main__':
    main()
