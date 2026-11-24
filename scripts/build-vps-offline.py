#!/usr/bin/env python3
"""Online publisher-side builder. Run on official Ubuntu 24.04 amd64 (not on the VPS)."""
import argparse
from email.parser import Parser
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess as sp
import sys
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
OFFLINE = ROOT / 'server/offline'
sys.path.insert(0, str(ROOT / 'server'))
sys.modules['vps_media'] = __import__('install')
spec = importlib.util.spec_from_file_location('offline_setup', OFFLINE / 'setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

BUNDLE = 'baci-classroom-vps-offline'
NAME = f'{BUNDLE}-v{setup.VERSION}-ubuntu24.04-amd64'
SITE = 'baci-classroom-site-pairing-v0.1.31.zip'
ARCHIVE = 'https://archive.ubuntu.com/ubuntu/'


def run(*argv, **kw):
    return sp.run([str(a) for a in argv], check=True, **kw).stdout


def sha(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def checksums(paths, base):
    return ''.join(f'{sha(path)}  {path.relative_to(base)}\n' for path in sorted(paths))


def fetch(url, destination, digest):
    run('curl', '--proto', '=https', '--proto-redir', '=https', '--tlsv1.2', '--fail', '--silent', '--show-error',
        '--retry', '3', '--location', '--connect-timeout', '15', '--max-time', '240', '--output', destination, url)
    if sha(destination) != digest:
        raise ValueError(f'Wrong SHA-256: {destination.name}')


def non_deb(bundle):
    payload = bundle / 'payload'
    payload.mkdir()
    origins = {
        setup.media.ASSET: setup.media.URL,
        setup.CADDY_ASSET: f'https://github.com/caddyserver/caddy/releases/download/v{setup.CADDY_VERSION}/{setup.CADDY_ASSET}',
        setup.CHROME_ASSET: f'https://storage.googleapis.com/chrome-for-testing-public/{setup.BROWSER_VERSION}/linux64/{setup.CHROME_ASSET}',
    }
    for filename, digest in setup.PAYLOAD.items():
        path = payload / filename
        if filename in origins:
            fetch(origins[filename], path, digest)
        else:
            shutil.copyfile(ROOT / 'artifacts' / filename, path)
            if sha(path) != digest:
                raise ValueError(f'Frozen Recorder payload changed: {filename}')
    with ZipFile(payload / setup.CHROME_ASSET) as chrome:
        notice = chrome.read(setup.BROWSER_TOP + '/LICENSE.headless_shell')
        if b'Redistribution and use in source and binary forms' not in notice or any('widevine' in n.lower() for n in chrome.namelist()):
            raise ValueError('Expected only the redistributable Chromium headless shell and its third-party notices')
    wheels = bundle / 'wheels'
    wheels.mkdir()
    run(sys.executable, '-Im', 'pip', '--isolated', 'download', '--disable-pip-version-check', '--no-cache-dir',
        '--only-binary=:all:', '--require-hashes', '--index-url', 'https://pypi.org/simple', '--dest', wheels,
        '-r', OFFLINE / 'requirements.lock')
    with ZipFile(next(wheels.glob('playwright-*.whl'))) as wheel:
        data = json.loads(wheel.read('playwright/driver/package/browsers.json'))
        shell = next(x for x in data['browsers'] if x['name'] == 'chromium-headless-shell')
        if (shell['browserVersion'], shell['revision']) != (setup.BROWSER_VERSION, '1223'):
            raise ValueError('Playwright and the Chromium payload do not match')
    return origins


def apt_bundle(work, bundle, sources):
    repo = bundle / 'apt'
    repo.mkdir()
    pool = repo / 'pool'
    pool.mkdir()
    apt = work / 'apt-build'
    for folder in (apt / 'lists/partial', apt / 'archives/partial'):
        folder.mkdir(parents=True)
    own_sources = apt / 'ubuntu.sources'
    own_sources.write_text(f'Types: deb deb-src\nURIs: {ARCHIVE}\n'
                           'Suites: noble noble-updates noble-security\nComponents: main universe restricted multiverse\n'
                           'Architectures: amd64\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n')
    status = apt / 'empty-status'
    status.touch()
    config = apt / 'apt.conf'
    config.write_text('Dir::Etc::main "/dev/null";\nDir::Etc::parts "-";\n'
                      f'Dir::Etc::sourcelist "{own_sources}";\nDir::Etc::sourceparts "-";\n'
                      'Dir::Etc::preferences "/dev/null";\nDir::Etc::preferencesparts "-";\n'
                      f'Dir::State::lists "{apt}/lists";\nDir::State::status "{status}";\n'
                      f'Dir::Cache::archives "{apt}/archives";\nDir::Cache::pkgcache "";\nDir::Cache::srcpkgcache "";\n'
                      'APT::Architecture "amd64";\nAPT::Architectures {"amd64";};\n'
                      'APT::Get::Always-Include-Phased-Updates "true";\nAPT::Install-Recommends "false";\n'
                      f'APT::Sandbox::User "{os.environ["USER"]}";\nAcquire::Languages "none";\nAcquire::Retries "3";\n')
    env = os.environ | {'APT_CONFIG': str(config), 'LC_ALL': 'C', 'DEBIAN_FRONTEND': 'noninteractive'}
    run('apt-get', '-q', '-o', 'APT::Update::Error-Mode=any', 'update', env=env)
    goals = [line for line in (OFFLINE / 'ubuntu-packages.txt').read_text().splitlines() if line and not line.startswith('#')]
    # An empty dpkg status avoids accidentally relying on anything installed on CI (including dev libraries).
    run('apt-get', '-qy', '--download-only', '--no-remove', 'install', *goals, env=env)
    index, rows, source_set = [], [], set()
    for archive in sorted((apt / 'archives').glob('*.deb')):
        fields = run('dpkg-deb', '-f', archive, text=True, capture_output=True).rstrip()
        control = Parser().parsestr(fields)
        name, arch, version = (control[x] for x in ('Package', 'Architecture', 'Version'))
        if arch not in ('all', 'amd64') or not name or not version:
            raise ValueError('Unexpected Ubuntu package architecture or name')
        digest = sha(archive)
        # Avoid dpkg's URL-escaped epoch in local names; Package + Arch is unique in this repo.
        filename = f'{name}_{arch}.deb'
        archive.rename(pool / filename)
        index.append(fields + f'\nFilename: ./pool/{filename}\nSize: {(pool / filename).stat().st_size}\nSHA256: {digest}\n')
        source = control.get('Source', name)
        match = re.fullmatch(r'([a-z0-9.+-]+)(?: \(([^ )]+)\))?', source)
        if not match:
            raise ValueError(f'Unexpected source control field: {name}')
        source_pkg, source_ver = match[1], match[2] or version
        source_set.add((source_pkg, source_ver))
        for field in ('Built-Using', 'Static-Built-Using'):
            source_set.update(re.findall(r'([a-z0-9.+-]+) \(= ([^ )]+)\)', control.get(field, '')))
        rows.append((name, arch, version, digest, source_pkg, source_ver))
    if not index or not set(goals) <= {row[0] for row in rows}:
        raise ValueError('APT did not download all runtime goals from the empty baseline')
    (repo / 'Packages').write_text('\n'.join(index))
    (bundle / 'ubuntu-package-lock.tsv').write_text('package\tarch\tversion\tsha256\tsource\tsource-version\n'
                                                    + ''.join('\t'.join(row) + '\n' for row in sorted(rows)))
    sources.mkdir()
    (sources / 'ubuntu-package-lock.tsv').write_bytes((bundle / 'ubuntu-package-lock.tsv').read_bytes())
    (sources / 'README.txt').write_text('Corresponding Ubuntu archive source packages (.dsc and referenced tar files).\n'
                                       'Not needed for VPS installation. See ubuntu-package-lock.tsv and the signed Ubuntu archive.\n')
    run('apt-get', '-q', '--download-only', '--only-source', 'source',
        *(f'{p}={v}' for p, v in sorted(source_set)), env=env, cwd=sources)
    if len(list(sources.glob('*.dsc'))) != len(source_set):
        raise ValueError('Corresponding Ubuntu source download is incomplete')
    (sources / 'SHA256SUMS').write_text(checksums(sources.iterdir(), sources))
    print(f'Ubuntu dependency inventory: {len(rows)} binaries, {len(source_set)} source packages.', flush=True)
    return len(rows)


def tar(root, folder, destination, gzip):
    command = ['tar', '--sort=name', '--mtime=@0', '--owner=0', '--group=0', '--numeric-owner',
               '-C', str(root), '-cf', str(destination) if not gzip else '-', folder]
    if not gzip:
        run(*command)
    else:
        with destination.open('xb') as out, sp.Popen(command, stdout=sp.PIPE) as archive:
            try:
                run('gzip', '-n', '-3', stdin=archive.stdout, stdout=out)
            finally:
                archive.stdout.close()
            if archive.wait():
                raise ValueError('tar failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output_dir', type=Path)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12) or run('dpkg', '--print-architecture', text=True, capture_output=True).strip() != 'amd64' \
            or not re.search(r'^ID=ubuntu\n', Path('/etc/os-release').read_text(), re.M):
        parser.error('The builder must run on Ubuntu 24.04 amd64 with Python 3.12 + pip.')
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if list(out.iterdir()):
        parser.error('Use a new output directory.')
    with tempfile.TemporaryDirectory(prefix='baci-offline-build-') as temp:
        root = Path(temp)
        bundle = root / BUNDLE
        bundle.mkdir()
        for name in ('install.sh', 'apt-local.sh', 'setup.py', 'requirements.lock', 'ubuntu-packages.txt', 'THIRD-PARTY.md'):
            shutil.copyfile(OFFLINE / name, bundle / name)
        shutil.copyfile(OFFLINE / 'README-fa.md', bundle / 'START-HERE-fa.md')
        shutil.copyfile(ROOT / 'server/install.py', bundle / 'vps_media.py')
        shutil.copyfile(ROOT / 'server/admission.py', bundle / 'admission.py')
        print('Fetching pinned application binaries, headless browser and hashed wheels…', flush=True)
        origins = non_deb(bundle)
        print('Resolving the signed Ubuntu binary and corresponding source closure…', flush=True)
        source_folder = root / f'{NAME}-ubuntu-source'
        count = apt_bundle(root, bundle, source_folder)
        site = ROOT / 'artifacts' / SITE
        shutil.copyfile(site, bundle / SITE)
        shutil.copyfile(site, out / SITE)
        (bundle / 'BUILD.json').write_text(json.dumps({'version': setup.VERSION, 'ubuntu': '24.04', 'arch': 'amd64',
            'git': run('git', 'rev-parse', 'HEAD', cwd=ROOT, text=True, capture_output=True).strip(),
            'deb_count': count, 'apt_origin': ARCHIVE, 'pinned_payload_sha256': setup.PAYLOAD, 'payload_origins': origins},
            indent=2, sort_keys=True) + '\n')
        (bundle / 'SHA256SUMS').write_text(checksums((p for p in bundle.rglob('*') if p.is_file()), bundle))
        tar(root, BUNDLE, out / f'{NAME}.tar.gz', gzip=True)
        tar(root, source_folder.name, out / f'{NAME}-ubuntu-source.tar', gzip=False)
        (out / f'{NAME}-SHA256SUMS.txt').write_text(checksums(out.iterdir(), out))
        print((out / f'{NAME}-SHA256SUMS.txt').read_text(), end='')
        print('Build complete; do not publish before the isolated systemd VPS rehearsal.')


if __name__ == '__main__':
    main()
