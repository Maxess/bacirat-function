#!/usr/bin/python3
"""Install a fresh classroom VPS from local packages, with optional automatic public HTTPS."""
import argparse
import contextlib
import grp
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import platform
import pwd
import re
import secrets
import shutil
import socket
import ssl
import stat
import subprocess as sp
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
from zipfile import BadZipFile, ZipFile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # python -I: only the verified staging or root-owned install directory.
import vps_media as media

VERSION = '1.3.4'
APP, CFG, SYSTEMD = media.APP, media.CFG, media.SYSTEMD
REC = Path('/opt/school-classroom-recorder')
RECORDER_RELEASE = '0.4.23'
REC_CFG = Path('/etc/school-classroom-recorder.json')
REC_CREDENTIAL = Path('/run/school-classroom-recorder-credential/config.json')
SPOOL = Path('/var/lib/school-classroom-recorder')
HTTPS_STATE = Path('/var/lib/school-classroom-https')
CLI = Path('/usr/local/sbin/baci-vps')
PROFILE = Path('/etc/apparmor.d/school-classroom-chromium')
STATE = CFG / 'offline-vps.json'
PAIR = CFG / 'site-pairing.json'
PAIR_NAME = 'Baci-VPS-pairing.json'
TLS = CFG / 'tls'
TRUST_ROOTS = Path('/etc/ssl/certs/ca-certificates.crt')
ACME_DIRECTORY = 'https://acme-v02.api.letsencrypt.org/directory'
ACME_WAIT = 150
SFU = media.SFU_SERVICE
GATE = 'school-classroom-admission.service'
HTTPS = 'school-classroom-https.service'
RECORDER = 'school-classroom-recorder.service'
RENDERER = 'school-classroom-postprocess.service'
LIVE_UNITS = (SFU, GATE, HTTPS)
UNITS = (*LIVE_UNITS, RECORDER, RENDERER)
ENABLED = UNITS[:-1]  # Renderer only starts as a dependency of Recorder.
PROGRAM_UNITS = (SFU, GATE, RECORDER, RENDERER)
REC_PROGRAM = REC / 'releases/0.4.23'
RENDER_SOCKET = Path('/run/school-classroom-postprocess/render.sock')
ACCOUNTS = (media.NAME, 'school-classroom-tls', 'school-recorder')
BROWSER_VERSION = '148.0.7778.96'
BROWSER_TOP = 'chrome-headless-shell-linux64'
BROWSER = REC / 'browsers/chromium_headless_shell-1223'
CHROME = BROWSER / BROWSER_TOP / 'chrome-headless-shell'
CADDY_VERSION = '2.11.9'
CADDY_ASSET = f'caddy_{CADDY_VERSION}_linux_amd64.tar.gz'
CHROME_ASSET = BROWSER_TOP + '.zip'
REC_ASSET = 'baci-classroom-recorder-v0.4.23-r4.zip'
R3_RECORDING_SHA = 'fe11f7dad4574aeac594cf8d023844d3841de4491b2d933dd865722b01f1f9fa'
SCHOOL_HTTP_ASSET = 'baci-classroom-recorder-school-http-v0.4.23.py'
PAYLOAD = {
    media.ASSET: media.SHA256,
    CADDY_ASSET: '5b8cb7faf8726a2028ad96a66e54e6e81e8f361bc6175e5d22bbd997cc5177fb',
    CHROME_ASSET: '88817c574c1838a39f88fda0bbd043b4481fd385fb10e92c323e230844d636ce',
    REC_ASSET: 'fd1889a15638ff8092b75b183eeba8211b5fa80b0632a1bed3d4833e0bb5a8bd',
    SCHOOL_HTTP_ASSET: '72550ec6cb374a0e86e618e65b84a455fbeb506a7b8cafdf4c5c8e6736c953c4',
}
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'HOME': '/root'}
GIB = 1024**3
# A VM provisioned with 512 MiB exposes less to /proc/meminfo after kernel reservations.
MIN_RAM_KIB = 384 * 1024
FULL_RAM_KIB = 1792 * 1024
COMPACT_RAM_KIB = 448 * 1024
COMPACT_NOSWAP_RAM_KIB = 896 * 1024
COMPACT_SWAP_KIB = 500 * 1024  # A nominal 512 MiB swapfile loses a small header.
PROFILES = ('live', 'compact', 'full')
LIVE_DEBS = ('ca-certificates', 'openssl', 'python3')
COMPACT_CAPTURE = '[Service]\nMemoryHigh=112M\nMemoryMax=160M\nMemorySwapMax=0\n'


class Refused(Exception):
    pass


class RecorderProbeFailed(Refused):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def need(ok, message):
    if not ok:
        raise Refused(message)


def run(*argv, output=False, show=False, timeout=60, label=None, **kwargs):
    # Commands with sensitive inputs must never write subprocess diagnostics to the terminal.
    args = list(map(str, argv))
    try:
        return sp.run(args, check=True, timeout=timeout, env=ENV, umask=0o022,
                      stdout=sp.PIPE if output else (None if show else sp.DEVNULL),
                      stderr=None if show else sp.DEVNULL, **kwargs).stdout
    except (OSError, sp.SubprocessError):
        raise Refused((label or Path(args[0]).name) + ' failed. See START-HERE-fa.md.') from None


def info(*args):
    return run('systemctl', 'show', '--value', *args, output=True, timeout=10).decode().strip()


def read_limited(path, *, private=False, limit=128 * 1024):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise Refused(f'Cannot read a regular input file: {path.name}') from None
    with os.fdopen(fd, 'rb') as file:
        st = os.fstat(file.fileno())
        need(stat.S_ISREG(st.st_mode) and 0 < st.st_size <= limit, f'Invalid input file: {path.name}')
        need(not private or not st.st_mode & 0o077, f'{path.name}: private input must have mode 0600 (chmod 600).')
        data = file.read(limit + 1)
    need(0 < len(data) <= limit, f'Invalid input size: {path.name}')
    return data


def json_file(path, *, private=True):
    return json.loads(read_limited(path, private=private, limit=4096))


def write(path, data, mode=0o644, *, uid=0, gid=0):
    if isinstance(data, str):
        data = data.encode()
    file = path.open('xb')  # Do not unlink a pre-existing file if the exclusive open failed.
    try:
        with file:
            os.fchown(file.fileno(), uid, gid)
            os.fchmod(file.fileno(), mode)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def save_json(path, obj, mode=0o600, **kwargs):
    write(path, json.dumps(obj, sort_keys=True) + '\n', mode, **kwargs)


def mkdir(path, mode=0o755, *, uid=0, gid=0):
    path.mkdir()
    os.chown(path, uid, gid)
    path.chmod(mode)


def names(site, host, ipv4):
    try:
        url = urlsplit(site.strip().rstrip('/'))
        origin = media.school_origin('https://' + url.netloc)
        path = media.site_path(url.path)
        need(url.scheme == 'https' and not url.query and not url.fragment and '?' not in site and '#' not in site,
             'Use the school base URL only (HTTPS, optional /subfolder, no page or query).')
        host = media.hostname(host.strip())
        ip = ipaddress.IPv4Address(ipv4.strip())
        need(ip.is_global and not ip.is_multicast, 'Use the public IPv4 assigned by your VPS provider.')
        need(host != origin[8:] and not _is_ip(host), 'The media subdomain must be different from the school web host.')
        return {'version': VERSION, 'school': origin + path, 'origin': origin,
                'site_path': path, 'media': host, 'ipv4': str(ip)}
    except (ValueError, argparse.ArgumentTypeError):
        raise Refused('Enter the school HTTPS address, a separate media DNS name and the VPS public IPv4.') from None


def _is_ip(text):
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def prompt(value, label):
    if value is None:
        need(sys.stdin.isatty(), 'For non-interactive use, pass --site, --media and --ipv4.')
        value = input(label + ': ').strip()
    return value


def system_preflight():
    release = Path('/etc/os-release').read_text()
    need(re.search(r'^ID=ubuntu$', release, re.M) and re.search(r'^VERSION_ID="?24\.04"?$', release, re.M)
         and os.geteuid() == 0 and platform.machine() == 'x86_64' and sys.version_info[:2] == (3, 12),
         'Run as root on a dedicated Ubuntu 24.04 amd64 VPS.')
    need(Path('/sys/fs/cgroup/cgroup.controllers').is_file() and bool(info('-p', 'Version')),
         'A running systemd with cgroup v2 is required.')


def check_memory(meminfo):
    found = re.search(r'^MemTotal:[ \t]+([0-9]+)[ \t]+kB$', meminfo, re.M)
    need(found is not None, 'Cannot determine the RAM visible to Linux (/proc/meminfo).')
    total_kib = int(found[1])
    need(total_kib >= MIN_RAM_KIB, f'Linux sees {total_kib} KiB of RAM. '
         f'Use a VPS provisioned with 512 MiB or more (at least {MIN_RAM_KIB // 1024} MiB visible to Linux; swap does not count).')
    return total_kib


def swap_size(meminfo):
    match = re.search(r'^SwapTotal:[ \t]+([0-9]+)[ \t]+kB$', meminfo, re.M)
    return int(match[1]) if match else 0


def install_profile(requested, total_kib, swap_kib=0):
    need(requested == 'auto' or requested in PROFILES, 'Unknown VPS installation profile.')
    # The archive and the download launcher both default to recording; never
    # hide a failed recording requirement by silently installing live-only.
    profile = ('full' if total_kib >= FULL_RAM_KIB else 'compact') if requested == 'auto' else requested
    if profile == 'full':
        need(total_kib >= FULL_RAM_KIB, f'Linux sees {total_kib} KiB of RAM. '
             'Full recording and film rendering need a larger VPS; use --profile live for live classes without local recording.')
    if profile == 'compact':
        need(total_kib >= COMPACT_RAM_KIB, f'Linux sees {total_kib} KiB of RAM; the compact recording profile needs at least {COMPACT_RAM_KIB} KiB visible, not swap.')
        # x86-64 V8 needs an accounted virtual CodeRange larger than the
        # usable RAM of a nominal 512 MiB guest. Stock Linux can admit the
        # mapping with enabled swap, without changing global overcommit.
        need(total_kib >= COMPACT_NOSWAP_RAM_KIB or swap_kib >= COMPACT_SWAP_KIB,
             'Compact recording below 896 MiB visible RAM requires 512 MiB of active swap. '
             'The installer does not create swap or change the kernel overcommit policy.')
    return profile


def fresh_preflight(requested='auto'):
    for path in (APP, CFG, REC, REC_CFG, SPOOL, HTTPS_STATE, CLI, PROFILE):
        need(not os.path.lexists(path), f'Existing classroom path {path}; nothing was overwritten.')
    for name in (*UNITS, 'school-classroom-watch.service'):
        need(info('-p', 'LoadState', name) == 'not-found' and not info('-p', 'DropInPaths', name)
             and not os.path.lexists(SYSTEMD / name) and not os.path.lexists(SYSTEMD / (name + '.d')),
             f'Existing unit or override {name}; use a fresh dedicated VPS.')
    for account in ACCOUNTS:
        for lookup in (pwd.getpwnam, grp.getgrnam):
            try:
                lookup(account)
            except KeyError:
                continue
            raise Refused(f'Existing account or group {account}; nothing was overwritten.')
    for path in (APP.parent, CFG.parent, SYSTEMD, SPOOL.parent, CLI.parent, PROFILE.parent):
        if path == PROFILE.parent and not path.exists():  # apparmor package creates the directory.
            continue
        st = path.lstat()
        need(path.resolve() == path and stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022,
             f'Unexpected system directory {path}; only a stock VPS is supported.')
    for port, kind in [(443, socket.SOCK_STREAM), (7880, socket.SOCK_STREAM), (7881, socket.SOCK_STREAM),
                       (7883, socket.SOCK_STREAM), (7882, socket.SOCK_DGRAM)]:
        try:
            with socket.socket(socket.AF_INET, kind) as sock:
                if kind == socket.SOCK_STREAM:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(('0.0.0.0', port))
        except OSError:
            raise Refused(f'Port {port}/{"UDP" if kind == socket.SOCK_DGRAM else "TCP"} is already in use. No service was stopped.') from None
    meminfo = Path('/proc/meminfo').read_text()
    total_kib = check_memory(meminfo)
    profile = install_profile(requested, total_kib, swap_size(meminfo))
    need(all(shutil.disk_usage(path).free >= size * GIB for path, size in
             [(APP.parent, 2), (SPOOL.parent, 5), (HERE, 1)]), 'Not enough free space; allow at least 8 GiB free on the system disk.')
    return total_kib, profile


def tls_selection(mode, cert, key):
    """Keep existing two-file installs offline, and never fall back from broken inputs to ACME."""
    if mode == 'auto':
        need(cert is None and key is None, '--tls auto cannot be combined with --cert or --key.')
        return 'auto', None, None
    use_files = mode == 'files' or cert is not None or key is not None
    cert = cert if cert is not None else Path('fullchain.pem')
    key = key if key is not None else Path('privkey.pem')
    present = tuple(os.path.lexists(path) for path in (cert, key))
    if use_files or any(present):
        need(all(present), 'TLS files are incomplete. Supply both PEM files, or explicitly choose --tls auto for a new certificate.')
        return 'files', cert, key
    return 'auto', None, None


def check_media_dns(config):
    try:
        found = {entry[4][0] for entry in socket.getaddrinfo(config['media'], 443, type=socket.SOCK_STREAM)}
    except OSError:
        found = set()
    need(found == {config['ipv4']},
         f"Media DNS is not ready: {config['media']} must point only to {config['ipv4']} "
         '(one A record, no AAAA or CDN proxy). Wait for DNS, then retry.')


def tls_context():
    context = ssl.create_default_context(cafile=str(TRUST_ROOTS))
    context.hostname_checks_common_name = False
    return context


def check_acme_access():
    # A bounded, certificate-verified GET. No account, challenge or certificate order is created here.
    try:
        opener = build_opener(ProxyHandler({}), media.NoRedirect(), HTTPSHandler(context=tls_context()))
        req = Request(ACME_DIRECTORY, headers={'Accept': 'application/json', 'User-Agent': 'Baci-VPS-Setup/' + VERSION})
        with opener.open(req, timeout=7) as response:
            raw = response.read(16385)
            valid = response.status == 200 and len(raw) <= 16384 and response.headers.get_content_type() == 'application/json'
        directory = json.loads(raw) if valid else None
        valid = isinstance(directory, dict) and all(
            isinstance(directory.get(key), str) and directory[key].startswith('https://')
            for key in ('newNonce', 'newAccount', 'newOrder'))
    except (OSError, ValueError, http.client.HTTPException):
        valid = False
    need(valid, 'Cannot reach the trusted Let’s Encrypt ACME directory. Check VPS outbound HTTPS, DNS and system time, '
         'or run --tls files with a valid fullchain.pem and privkey.pem for fully offline installation.')


def cert_inputs(cert, key):
    chain = read_limited(cert)
    key_data = read_limited(key, private=True)
    pattern = rb'-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----'
    pems = re.findall(pattern, chain)
    need(1 <= len(pems) <= 8 and not re.sub(pattern, b'', chain).strip(),
         'Use a PEM fullchain: first the media certificate, then its intermediates (no private key).')
    need(re.fullmatch(rb'\s*-----BEGIN ((?:RSA |EC )?PRIVATE KEY)-----\s+[A-Za-z0-9+/=\s]+?-----END \1-----\s*', key_data),
         'Use the matching unencrypted PEM private key.')
    return chain, key_data


def validate_cert(host, chain, key):
    with tempfile.TemporaryDirectory(prefix='tls-', dir=HERE) as path:
        tls = Path(path)
        fullchain, privkey = tls / 'chain.pem', tls / 'key.pem'
        write(fullchain, chain, 0o600)
        write(privkey, key, 0o600)
        label = 'Media TLS certificate (hostname, trust chain, system time or expiration)'
        run('openssl', 'verify', '-no-CApath', '-no-CAstore', '-CAfile', TRUST_ROOTS,
            '-untrusted', fullchain, '-verify_hostname', host, '-purpose', 'sslserver', fullchain, label=label)
        run('openssl', 'x509', '-in', fullchain, '-noout', '-checkend', '86400', label=label)
        san = run('openssl', 'x509', '-in', fullchain, '-noout', '-ext', 'subjectAltName', output=True, label=label)
        need(b'DNS:' in san, 'The media hostname must be in the certificate DNS SAN list.')
        features = run('openssl', 'x509', '-in', fullchain, '-noout', '-ext', 'tlsfeature', output=True, label=label)
        need(b'status_request' not in features, 'Use a certificate that does not require online OCSP stapling.')
        pub = run('openssl', 'x509', '-in', fullchain, '-noout', '-pubkey', output=True, label=label)
        pub_key = run('openssl', 'pkey', '-in', privkey, '-passin', 'pass:', '-pubout', output=True, label='TLS private key')
        need(pub and pub == pub_key, 'The TLS private key does not match the media certificate.')


def pairing(config):
    return {'format': 'baci-vps-pairing/1', 'site': config['school'], 'signal_url': 'wss://' + config['media'],
            'api_key': secrets.token_hex(12), 'api_secret': secrets.token_hex(32), 'recording_secret': secrets.token_hex(32)}


def local_ipv4(address):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.bind((address, 0))
        except OSError:
            return False  # Provider may forward from a public IPv4 to a private interface.
    return True


def sfu_config(config, keys):
    address = config['ipv4']
    # With multiple interfaces, constrain the UDP mux to the managed local IPv4
    # when assigned; otherwise retain the original NAT-compatible layout.
    text = media.engine_config(address, keys['api_key'], keys['api_secret'], bound_udp=local_ipv4(address))
    return text + media.native_webhook_block(keys['api_key'], media.webhook_url(config['origin'], config['site_path']))


def edge_config(config):
    site = media.caddy_config(config['media'], config['origin'])
    domain, rules = site.split(' {\n', 1)
    if config.get('tls_mode', 'files') == 'auto':
        # Direct IPv4 TLS-ALPN-01 on the same 443 port as the application. No port 80,
        # DNS plugin, provider credential, or ACME client download is needed.
        automatic = 'disable_redirects'
        tls = ('    tls {\n        issuer acme {\n'
               f'            dir {ACME_DIRECTORY}\n'
               '            disable_http_challenge\n        }\n    }\n')
    else:
        automatic = 'off'
        tls = f'    tls {TLS}/fullchain.pem {TLS}/privkey.pem\n'
    return (f'{{\n    admin off\n    auto_https {automatic}\n    ocsp_stapling off\n'
            '    servers {\n        protocols h1 h2\n    }\n}\n\n'
            f'{domain} {{\n    bind 0.0.0.0\n' + tls + rules)


def edge_unit():
    return media.unit('Baci classroom HTTPS edge',
                      f'{APP}/caddy run --config {CFG}/Caddyfile --adapter caddyfile',
                      environment=f'Environment=XDG_DATA_HOME={HTTPS_STATE}\n'
                                  'UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy SSL_CERT_FILE SSL_CERT_DIR\n'
                                  'StateDirectory=school-classroom-https\nStateDirectoryMode=0700\n'
                                  'AmbientCapabilities=CAP_NET_BIND_SERVICE\n'
                                  'LimitCORE=0\n').replace(
        f'User={media.NAME}\nGroup={media.NAME}\n', f'User={ACCOUNTS[1]}\nGroup={ACCOUNTS[1]}\n').replace(
        'CapabilityBoundingSet=\n', 'CapabilityBoundingSet=CAP_NET_BIND_SERVICE\n')


def deb_apt(profile, *, addition=False, check=False):
    goals = ('--no-upgrades',) if addition else LIVE_DEBS if profile == 'live' else ()
    options = ('--check',) if check else ()
    action = 'Offline APT plan' if check else 'Offline APT (no package removal or downgrade)'
    run('/bin/bash', HERE / 'apt-local.sh', HERE, *options, *goals, show=True, timeout=900, label=action)


def deb_preflight(profile, *, addition=False):
    deb_apt(profile, addition=addition, check=True)


def deb_install(profile, *, addition=False):
    print('نصب پیش‌نیازها از خود بسته، بدون دانلود…', flush=True)
    deb_apt(profile, addition=addition)


def verify_payloads():
    for name, digest in PAYLOAD.items():
        with (HERE / 'payload' / name).open('rb') as file:
            need(hashlib.file_digest(file, 'sha256').hexdigest() == digest, f'Unexpected release payload: {name}')


def browser_files():
    mkdir(BROWSER.parent)
    mkdir(BROWSER)
    with ZipFile(HERE / 'payload' / CHROME_ASSET) as archive:
        seen, expanded = set(), 0
        for item in archive.infolist():
            p = PurePosixPath(item.filename)
            need(p.parts and p.parts[0] == BROWSER_TOP and '..' not in p.parts and str(p) == item.filename.rstrip('/')
                 and p not in seen and 'widevine' not in str(p).lower(), 'Unexpected headless Chromium archive entry.')
            seen.add(p)
            expanded += item.file_size
            need(expanded <= 600 * 1024**2, 'Headless Chromium archive is too large.')
            dest = BROWSER / p
            if not item.is_dir():
                mode = item.external_attr >> 16
                need(stat.S_IFMT(mode) in (0, stat.S_IFREG), 'Unexpected link or special file in Chromium archive.')
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Parent directories created here are root-owned and can be traversed by the service.
                for parent in (dest.parent, *dest.parent.parents):
                    if parent == BROWSER:
                        break
                    parent.chmod(0o755)
                with archive.open(item) as src, dest.open('xb') as out:
                    shutil.copyfileobj(src, out)
                dest.chmod(0o755 if dest == CHROME else 0o644)
    need(CHROME.is_file() and CHROME.with_name('LICENSE.headless_shell').is_file(), 'Expected the Chromium executable and its notices.')
    write(BROWSER / 'INSTALLATION_COMPLETE', b'')


def renderer_program():
    mkdir(REC / 'releases')
    mkdir(REC_PROGRAM)
    mkdir(REC_PROGRAM / 'fonts')
    units = {}
    with ZipFile(HERE / 'payload' / REC_ASSET) as archive:
        for name in archive.namelist():  # whole upstream ZIP already pinned; keep original service files.
            if name in (RECORDER, RENDERER) or name.startswith(RECORDER + '.d/'):
                units[name] = archive.read(name)
            elif name != 'transport.py':
                write(REC_PROGRAM / name, archive.read(name), 0o755 if name == 'baci-native-receiver' else 0o644)
    # The renderer and replay are the unchanged, reviewed r3 artifacts.
    # Keep the same pinned HTTP transport here and in read-only add-on preflight.
    write(REC_PROGRAM / 'transport.py', school_http_bytes(HERE / 'payload' / SCHOOL_HTTP_ASSET))
    return units


def venv_install():
    venv = REC / 'venv'
    run('/usr/bin/python3', '-Im', 'venv', venv, timeout=90, label='Local Python venv')
    sp_env = ENV | {'PIP_CONFIG_FILE': '/dev/null', 'PIP_DISABLE_PIP_VERSION_CHECK': '1'}
    try:
        sp.run(['/usr/bin/unshare', '--net', '--', str(venv / 'bin/python'), '-Im', 'pip', 'install', '--no-index',
                '--no-cache-dir', '--only-binary=:all:', '--require-hashes', '--find-links', str(HERE / 'wheels'),
                '-r', str(HERE / 'requirements.lock')], env=sp_env, umask=0o022, check=True, timeout=120)
    except (OSError, sp.SubprocessError):
        raise Refused('Offline Python wheel installation failed.') from None
    expected = run(venv / 'bin/python', '-Ic',
                   'from playwright.sync_api import sync_playwright; '
                   'p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()', output=True)
    # Playwright's API property describes full Chromium, but a headless launch
    # below uses the headless shell. Prove that the wheel is the matching revision.
    manifest = venv / 'lib/python3.12/site-packages/playwright/driver/package/browsers.json'
    registry = json.loads(manifest.read_bytes())['browsers']
    need(bool(expected) and any(x['name'] == 'chromium-headless-shell' and x['revision'] == '1223'
                                and x['browserVersion'] == BROWSER_VERSION for x in registry),
         'Playwright and the bundled headless Chromium are not the same release.')


def profile_needed():
    flag = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
    return flag.exists() and flag.read_text().strip() == '1'


def profile_text():
    return f'abi <abi/4.0>,\ninclude <tunables/global>\n\nprofile school-classroom-chromium {CHROME} flags=(unconfined) {{\n    userns,\n}}\n'


@contextlib.contextmanager
def edge_tls(config):
    # Standard hostname and trust-chain verification, with no DNS in local checks.
    with socket.create_connection(('127.0.0.1', 443), timeout=4) as conn:
        with tls_context().wrap_socket(conn, server_hostname=config['media']) as tls:
            yield tls


def probe_edge(config, path, *, method='GET', headers=None, body=b''):
    with edge_tls(config) as tls:
        fields = {'Host': config['media'], 'Connection': 'close', 'Content-Length': str(len(body)), **(headers or {})}
        tls.sendall((f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in fields.items()) + '\r\n').encode() + body)
        with http.client.HTTPResponse(tls) as response:
            response.begin()
            return response.status, response.headers, response.read(16385)


def edge_certificate(config):
    with edge_tls(config) as tls:
        return tls.getpeercert()['notAfter']


def probe_renderer(test=False):
    st = RENDER_SOCKET.lstat()
    need(stat.S_ISSOCK(st.st_mode) and not st.st_mode & 0o077 and st.st_uid == pwd.getpwnam(ACCOUNTS[2]).pw_uid,
         'The renderer socket is not private.')
    action = 'self-test-mp4' if test else 'ready'
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(22 if test else 3)
        conn.connect(str(RENDER_SOCKET))
        conn.sendall((json.dumps({'probe': action}) + '\n').encode())
        with conn.makefile('rb') as file:
            result = json.loads(file.readline(512))
    need(result == {'result': 'baci-render/1' + (' tested mp4' if test else '')},
         'Sandboxed renderer check failed (Chromium or FFmpeg).')


def with_recording(config):
    return config.get('profile', 'full') in ('compact', 'full')  # v1.1.0 state did not name the profile.


def program_checks(config, *, fresh=False):
    full = with_recording(config)
    for name in (PROGRAM_UNITS if full else (SFU, GATE)):
        need(info('-p', 'SubState', name) == 'running', f'{name} is not running.')
    if fresh:
        # Verify the private media API early, before submitting a public certificate order.
        need(not media.local_rooms(media.pilot_deployment()), 'A fresh LiveKit must not already have rooms.')
        if full:
            need(run(REC_PROGRAM / 'baci-native-receiver', '--check', output=True) == b'baci-rtp/2\n',
                 'Native Recorder failed its compatibility check.')
    if full:
        worker = json_file(SPOOL / 'worker.json')['id']
        need(isinstance(worker, str) and re.fullmatch(r'[a-f0-9]{32}', worker), 'Native Recorder has no valid local identity.')
        probe_renderer()


def local_checks(config):
    program_checks(config)
    need(info('-p', 'SubState', HTTPS) == 'running', f'{HTTPS} is not running.')
    code, _, _ = probe_edge(config, '/healthz')
    need(code == 200, 'HTTPS /healthz did not reach LiveKit.')
    code, _, _ = probe_edge(config, '/rtc')
    need(code == 403, 'HTTPS signaling admission did not refuse a request without a ticket.')
    code, headers, _ = probe_edge(config, '/rtc', method='OPTIONS', headers={'Origin': config['origin']})
    need(code == 204 and headers.get('Access-Control-Allow-Origin') == config['origin'], 'School-origin signaling preflight failed.')
    deployment = media.pilot_deployment()
    code, _, raw = probe_edge(config, '/twirp/livekit.RoomService/ListRooms', method='POST', body=b'{}', headers={
        'Authorization': 'Bearer ' + media.jwt(deployment, video={'roomList': True}), 'Content-Type': 'application/json'})
    need(code == 200 and isinstance(json.loads(raw).get('rooms', []), list), 'Signed local media API check failed.')


def wait_ready(check, seconds, message):
    deadline, last = time.monotonic() + seconds, message
    while time.monotonic() < deadline:
        try:
            return check()
        except Refused as error:
            last = str(error)
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            last = message
        time.sleep(1)
    raise Refused(last)


def wait_edge(config):
    automatic = config['tls_mode'] == 'auto'
    return wait_ready(lambda: edge_certificate(config), ACME_WAIT if automatic else 28,
                      ('Automatic TLS did not obtain a browser-trusted certificate. '
                       'Check the public A record, TCP 443 to this VPS, outbound access to Let’s Encrypt and any CAA restrictions. '
                       'Do not retry repeatedly; see START-HERE-fa.md.')
                      if automatic else 'Caddy did not serve the supplied trusted certificate on TCP 443.')


def safe_remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


class Fresh:
    """Only roll back paths/accounts this process created before sharing credentials."""
    def __init__(self, profile):
        self.profile = profile
        self.full = with_recording({'profile': profile})
        self.units = UNITS if self.full else LIVE_UNITS
        self.enabled = ENABLED if self.full else LIVE_UNITS
        self.paths = []
        self.users = []
        self.starting = self.enabling = self.apparmor = False

    def folder(self, path, **kwargs):
        path.mkdir()
        self.paths.append(path)
        os.chown(path, kwargs.get('uid', 0), kwargs.get('gid', 0))
        path.chmod(kwargs.get('mode', 0o755))

    def file(self, path, *args, **kwargs):
        write(path, *args, **kwargs)
        self.paths.append(path)

    def cleanup(self):
        # If systemd cannot confirm stop/disable, do not remove files under live services.
        if self.enabling:
            run('systemctl', 'disable', *self.enabled)
        if self.starting:
            run('systemctl', 'stop', *self.units)
        if self.apparmor:
            run('apparmor_parser', '-R', PROFILE)
        for path in reversed(self.paths):
            safe_remove(path)
        if self.starting:
            run('systemctl', 'daemon-reload')
        for name in reversed(self.users):
            run('userdel', name)
            with contextlib.suppress(KeyError):
                grp.getgrnam(name)
                run('groupdel', name)

    def create_account(self, name):
        run('useradd', '--system', '--user-group', '--no-create-home', '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', name)
        self.users.append(name)

    def recorder_files(self, config, keys):
        recorder = pwd.getpwnam(ACCOUNTS[2])
        self.folder(REC)
        self.folder(SPOOL, mode=0o700, uid=recorder.pw_uid, gid=recorder.pw_gid)
        self.file(REC_CFG, json.dumps({'site': config['school'], 'secret': keys['recording_secret']}) + '\n', 0o600)
        units = renderer_program()
        if self.profile == 'compact':
            program = REC_PROGRAM / 'compact-renderer.py'
            write(program, (HERE / program.name).read_bytes())
            encoder = REC_PROGRAM / 'compact-ffmpeg.py'
            write(encoder, (HERE / encoder.name).read_bytes(), 0o755)
            small = check_memory(Path('/proc/meminfo').read_text()) < COMPACT_NOSWAP_RAM_KIB
            # Keep resident memory bounded; the small renderer may page out a
            # limited amount ONLY off peak. Do not set a tight MemoryHigh that
            # stalls Chromium/FFmpeg on every page fault during PDF startup.
            resident, swap = ('336M', '192M') if small else ('640M', '0')
            units[RENDERER + '.d/80-compact.conf'] = ('[Service]\nExecStart=\n'
                f'ExecStart={REC}/venv/bin/python -Esu {program}\nEnvironment=XDG_CACHE_HOME=/tmp/baci-font-cache\n'
                'CPUQuota=100%\nMemoryHigh=infinity\n'
                f'MemoryMax={resident}\nMemorySwapMax={swap}\n')
            units[RECORDER + '.d/80-compact.conf'] = COMPACT_CAPTURE
        browser_files()
        venv_install()
        if profile_needed():
            self.file(PROFILE, profile_text())
            run('apparmor_parser', '-r', PROFILE, label='Scoped AppArmor Chromium user-namespace profile')
            self.apparmor = True
        return units

    def install_units(self, units):
        for name, data in sorted(units.items()):
            path = SYSTEMD / name
            if path.parent != SYSTEMD and path.parent not in self.paths:
                self.folder(path.parent)
            self.file(path, data)

    def apply(self, config, cert_files):
        keys = pairing(config)
        for name in (ACCOUNTS if self.full else ACCOUNTS[:2]):
            self.create_account(name)
        sfu, tls = map(pwd.getpwnam, ACCOUNTS[:2])
        self.folder(APP)
        self.folder(CFG)
        self.folder(HTTPS_STATE, mode=0o700, uid=tls.pw_uid, gid=tls.pw_gid)
        if cert_files is not None:
            chain, key = cert_files
            mkdir(TLS, 0o750, gid=tls.pw_gid)
            write(TLS / 'fullchain.pem', chain, 0o640, gid=tls.pw_gid)
            write(TLS / 'privkey.pem', key, 0o640, gid=tls.pw_gid)
        media.extract_binary(HERE / 'payload' / media.ASSET, APP / 'livekit-server')
        with tarfile.open(HERE / 'payload' / CADDY_ASSET, 'r:gz') as archive:
            with archive.extractfile('caddy') as source, (APP / 'caddy').open('xb') as out:
                shutil.copyfileobj(source, out)
        (APP / 'caddy').chmod(0o755)
        for source, dest in [('vps_media.py', 'vps_media.py'), ('admission.py', 'admission.py'), ('setup.py', 'baci-vps.py')]:
            write(APP / dest, (HERE / source).read_bytes())
        deployment = {k: keys[k] for k in ('api_key', 'api_secret')}
        write(CFG / 'livekit.yaml', sfu_config(config, deployment), 0o640, gid=sfu.pw_gid)
        save_json(CFG / 'admission.json', deployment | {'school_origin': config['origin']}, 0o640, gid=sfu.pw_gid)
        save_json(PAIR, keys)
        write(CFG / 'Caddyfile', edge_config(config))
        units = {
            SFU: media.sfu_unit_config(network_online=True),
            GATE: media.unit('School classroom signaling admission', f'/usr/bin/python3 -IB {APP}/admission.py {CFG}/admission.json'),
            HTTPS: edge_unit(),
        }
        if self.full:
            units.update(self.recorder_files(config, keys))
        self.install_units(units)
        self.file(CLI, f'#!/bin/sh\nexec /usr/bin/python3 -IB {APP}/baci-vps.py "$@"\n', 0o755)
        run('runuser', '-u', ACCOUNTS[1], '--', APP / 'caddy', 'validate', '--config', CFG / 'Caddyfile', '--adapter', 'caddyfile',
            label='Caddy local TLS configuration')
        self.starting = True
        run('systemctl', 'daemon-reload')
        print('۳/۴  ' + ('آزمون رسانه، مرورگر sandbox و ساخت MP4…' if self.full else 'آزمون رسانه و درگاه کلاس زنده…'), flush=True)
        services = (SFU, GATE, RECORDER) if self.full else (SFU, GATE)
        run('systemctl', 'start', *services, timeout=125, label='systemd classroom service startup')
        wait_ready(lambda: program_checks(config, fresh=True), 28, 'Local classroom services did not become ready.')
        if self.full:
            probe_renderer(test=True)
        print('۴/۴  ' + ('دریافت و نصب خودکار گواهی عمومی از Let’s Encrypt؛ کمی صبر کن…'
                        if config['tls_mode'] == 'auto' else 'آزمون HTTPS با گواهی آماده، بدون اینترنت…'), flush=True)
        run('systemctl', 'start', HTTPS, label='systemd HTTPS service startup')
        expires = wait_edge(config)
        local_checks(config)
        self.enabling = True
        run('systemctl', 'enable', *self.enabled)
        save_json(STATE, config)
        print(f"tls={config['tls_mode']} expires={expires}", flush=True)



def school_http_bytes(path):
    data = read_limited(path, limit=64 * 1024)
    need(hashlib.sha256(data).hexdigest() == PAYLOAD[SCHOOL_HTTP_ASSET], 'The reviewed Recorder HTTP client did not match its SHA-256.')
    return data


def recorder_status(config, keys, worker):
    # The installed doctor and the service use the exact same transport bytes.
    # The add-on must also be able to probe before the Recorder is installed.
    path = REC_PROGRAM / 'transport.py' if HERE == APP else HERE / 'payload' / SCHOOL_HTTP_ASSET
    try:
        code = school_http_bytes(path)
    except Refused:
        raise RecorderProbeFailed('local_client_invalid', 'The installed Recorder HTTP client could not be verified.') from None
    client = {'__name__': '_baci_recorder_http'}
    exec(compile(code, '<verified Recorder HTTP client>', 'exec'), client)
    try:
        result = client['School'](config['school'], keys['recording_secret'], worker)('maintenance')
    except client['ProtocolError'] as error:
        if error.status == 403:
            reason = 'http_403'
            message = 'HTTP 403: check the recording switch, pairing and school hosting rules for the authenticated recording route.'
        elif error.status == 200:
            reason, message = 'unexpected_reply', 'The school returned HTTP 200 without a valid Recorder response.'
        elif not error.status:
            reason, message = 'https_unreachable', 'The verified HTTPS Recorder request did not complete.'
        else:
            reason = f'http_{error.status}'
            message = f'The school did not confirm the Recorder (HTTP {error.status}). Check the school HTTPS endpoint and activation.'
        raise RecorderProbeFailed(reason, message) from None
    if result.get('release') != RECORDER_RELEASE or any(type(result.get(k)) is not bool for k in ('idle', 'worker_ok', 'native_ready')):
        raise RecorderProbeFailed('unexpected_reply', 'Unexpected school Recorder response. Check the Classroom version and site URL.')
    return result


def recorder_idle(config, keys, worker):
    need(not media.local_rooms(media.pilot_deployment()), 'LiveKit still has rooms. Close the lessons before adding recording.')
    status = recorder_status(config, keys, worker)
    need(status.get('idle') is True and status.get('worker_ok') is True,
         'The school still has an open lesson, pending recording, or another active Recorder. Nothing was replaced.')


def managed(path, kind):
    st = path.lstat()
    need(kind(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022 and path.resolve() == path,
         f'Unexpected installed path {path}; nothing was overwritten.')


def replacement(path, data, mode):
    # Write and sync in a protected sibling directory, then publish in one rename.
    with tempfile.TemporaryDirectory(prefix='.recorder-', dir=path.parent) as stage:
        scratch = Path(stage) / path.name
        write(scratch, data, mode)
        os.replace(scratch, path)


def recorder_update_unit(config, pack):
    unit = SYSTEMD / RECORDER
    dropin = SYSTEMD / (RECORDER + '.d')
    managed(dropin, stat.S_ISDIR)
    expected = {name: pack.read(name) for name in pack.namelist() if name == RECORDER or name.startswith(dropin.name + '/')}
    if config['profile'] == 'compact':
        expected[dropin.name + '/80-compact.conf'] = COMPACT_CAPTURE.encode()
    for name, data in expected.items():
        actual, _ = media.read_pilot_file(SYSTEMD / name, private=False)
        need(actual.encode() == data, 'Customized Recorder systemd unit; nothing was overwritten.')
    keys = ('FragmentPath', 'DropInPaths', 'ActiveState', 'NeedDaemonReload')
    values = run('systemctl', 'show', RECORDER, '--all', '--property=' + ','.join(keys), output=True, timeout=10).decode()
    props = dict(line.split('=', 1) for line in values.splitlines() if '=' in line)
    dropins = {str(SYSTEMD / n) for n in expected if n != RECORDER}
    need(props.get('FragmentPath') == str(unit) and set(props.get('DropInPaths', '').split()) == dropins
         and props.get('ActiveState') == 'active' and props.get('NeedDaemonReload') == 'no',
         'Expected the running stock Recorder unit with no extra overrides; nothing was overwritten.')


def update_recorder(config, *, check):
    """Replace only the reviewed r3 scheduler, keeping program path and worker identity."""
    need(config.get('recorder_bundle', config.get('version')) in ('1.3.3', VERSION)
         and config.get('profile') in ('full', 'compact'), 'This update requires a managed VPS with the r3 or r4 Recorder installed.')
    verify_payloads()
    with media.locked_config(not check):
        path = REC_PROGRAM / 'recording.py'
        for folder in (REC, REC_PROGRAM, SYSTEMD):
            managed(folder, stat.S_ISDIR)
        with ZipFile(HERE / 'payload' / REC_ASSET) as pack:
            wanted = pack.read(path.name).decode()
            recorder_update_unit(config, pack)
        before, file_info = media.read_pilot_file(path, private=False)
        need(before == wanted or hashlib.sha256(before.encode()).hexdigest() == R3_RECORDING_SHA,
             'Scheduler differs from the reviewed r3/r4 file; nothing was overwritten.')
        keys = json_file(PAIR)
        identity = json_file(SPOOL / 'worker.json')['id']
        need(isinstance(identity, str) and re.fullmatch(r'[a-f0-9]{32}', identity)
             and keys['site'] == config['school']
             and json_file(REC_CFG) == json_file(REC_CREDENTIAL) == {'site': keys['site'], 'secret': keys['recording_secret']},
             'Pairing or Recorder worker identity does not match the managed service.')
        school_http_bytes(REC_PROGRAM / 'transport.py')
        need(not any(re.fullmatch(r'[a-f0-9]{32}', child.name) for child in SPOOL.iterdir()),
             'Local Recorder spool still has jobs. Wait for it to settle; do not delete any recordings.')
        recorder_idle(config, keys, identity)
        if check or before == wanted:
            print(f'recorder_update={"preflight_ok" if check else "ok"} changed=no restarted=no')
            return
        backup = REC_PROGRAM / (path.name + '.before-r4-' + secrets.token_hex(6) + '.bak')
        write(backup, before, 0o600)
        media.replace_pilot_config(path, before, wanted, file_info, private=False)
        try:
            run('systemctl', 'restart', RECORDER, timeout=80, label='Recorder restart')
            wait_ready(lambda: program_checks(config), 15, 'Recorder readiness not confirmed after restart.')
        except (Refused, OSError, ValueError, KeyboardInterrupt):
            try:
                media.replace_pilot_config(path, wanted, before, file_info, private=False)
                run('systemctl', 'restart', RECORDER, timeout=80, label='Recorder recovery')
                wait_ready(lambda: program_checks(config), 15, 'Previous Recorder did not become ready.')
            except (Refused, OSError, ValueError):
                raise Refused(f'Recorder recovery could not be verified; keep private backup {backup}.') from None
            raise Refused('Recorder update failed; previous scheduler and service restored.') from None
        print(f'recorder_update=ok changed=yes restarted=yes backup={backup}')


def addition_preflight(config, requested, worker):
    need(config.get('version') in ('1.2.0', '1.3.0', '1.3.1', '1.3.2', '1.3.3', VERSION) and config.get('profile') == 'live' and config.get('tls_mode') in ('files','auto'),
         'This add-on is only for a completed Baci offline live install (v1.2.0, v1.3.0–v1.3.3 or this release).')
    meminfo = Path('/proc/meminfo').read_text()
    total_kib = check_memory(meminfo)
    profile = install_profile(requested, total_kib, swap_size(meminfo))
    need(profile != 'live', 'Use a recording profile with --add-recorder.')
    for path in (APP, CFG, SYSTEMD, REC.parent, SPOOL.parent, PROFILE.parent):
        if path == PROFILE.parent and not path.exists():
            continue
        managed(path, stat.S_ISDIR)
    for path in (STATE, PAIR, APP / 'baci-vps.py'):
        managed(path, stat.S_ISREG)
    site = names(config['school'], config['media'], config['ipv4'])
    need(all(config.get(k) == site[k] for k in ('school', 'origin', 'site_path', 'media', 'ipv4')), 'The installed site and media settings do not match.')
    keys = json_file(PAIR)
    deployment = media.pilot_deployment()
    need(all(isinstance(keys.get(k), str) and re.fullmatch(pattern, keys[k]) for k, pattern in {
        'api_key': r'[a-f0-9]{24}', 'api_secret': r'[a-f0-9]{64}', 'recording_secret': r'[a-f0-9]{64}',
    }.items()) and keys.get('format') == 'baci-vps-pairing/1' and keys.get('site') == config['school']
         and keys.get('signal_url') == 'wss://' + config['media'] and keys['api_secret'] != keys['recording_secret']
         and all(keys[k] == deployment[k] for k in ('api_key', 'api_secret')) and deployment['school_origin'] == config['origin'],
         'The existing private pairing does not match the running deployment. Nothing was replaced.')
    for path in (REC, REC_CFG, SPOOL, PROFILE, RENDER_SOCKET.parent):
        need(not os.path.lexists(path), f'Existing recorder path {path}; nothing was overwritten.')
    for unit in (RECORDER, RENDERER):
        need(info('-p', 'LoadState', unit) == 'not-found' and not info('-p', 'DropInPaths', unit)
             and not os.path.lexists(SYSTEMD / unit) and not os.path.lexists(SYSTEMD / (unit + '.d')),
             f'Existing recorder unit or override {unit}; nothing was overwritten.')
    for lookup in (pwd.getpwnam, grp.getgrnam):
        try:
            lookup(ACCOUNTS[2])
        except KeyError:
            continue
        raise Refused('An existing Recorder account or group was found; nothing was overwritten.')
    need(all(shutil.disk_usage(path).free >= size * GIB for path, size in ((REC.parent, 2), (SPOOL.parent, 5), (HERE, 1))),
         'Not enough free space; allow at least 8 GiB free on the system disk.')
    verify_payloads()
    local_checks(config)
    recorder_idle(config, keys, worker)
    return profile, keys, total_kib


class RecorderAddition(Fresh):
    """Start and roll back only Recorder files/accounts/units, never the live edge."""
    def __init__(self, profile):
        super().__init__(profile)
        self.units, self.enabled = (RECORDER, RENDERER), (RECORDER,)
        self.replacements = []

    def cleanup(self):
        super().cleanup()
        for path, data, mode in reversed(self.replacements):
            replacement(path, data, mode)

    def replace(self, path, data, mode):
        self.replacements.append((path, read_limited(path, private=mode == 0o600), mode))
        replacement(path, data, mode)

    def apply_addition(self, config, profile, keys, worker):
        self.create_account(ACCOUNTS[2])
        self.install_units(self.recorder_files(config, keys))
        account = pwd.getpwnam(ACCOUNTS[2])
        save_json(SPOOL / 'worker.json', {'id': worker}, uid=account.pw_uid, gid=account.pw_gid)
        self.starting = True
        run('systemctl', 'daemon-reload')
        # Do not report availability to the school while the synthetic MP4 probe runs.
        run('systemctl', 'start', RENDERER, timeout=25)
        probe_renderer(test=True)
        need(run(REC_PROGRAM / 'baci-native-receiver', '--check', output=True) == b'baci-rtp/2\n',
             'Native Recorder failed its compatibility check.')
        recorder_idle(config, keys, worker)
        self.enabling = True
        run('systemctl', 'enable', RECORDER)
        run('systemctl', 'start', RECORDER, timeout=45)
        new_config = config | {'profile': profile, 'recorder_bundle': VERSION}
        wait_ready(lambda: local_checks(new_config), 20, 'New local Recorder is not ready.')

        def heartbeat():
            need(recorder_status(config, keys, worker).get('native_ready') is True, 'The school has not confirmed the new Recorder heartbeat.')

        wait_ready(heartbeat, 28, 'The school has not confirmed the new Recorder heartbeat.')
        self.replace(APP / 'baci-vps.py', (HERE / 'setup.py').read_bytes(), 0o644)
        self.replace(STATE, (json.dumps(new_config, sort_keys=True) + '\n').encode(), 0o600)


def export_pair():
    account = pwd.getpwuid(int(os.environ.get('SUDO_UID', '0')))
    dest = Path(account.pw_dir) / PAIR_NAME
    # Use the SSH account's privileges to open its output path. Never overwrite an existing export.
    program = ('import os,sys\n'
               'try:\n f=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)\n'
               'except FileExistsError:\n sys.exit(3)\n'
               'with os.fdopen(f,"wb") as out: out.write(sys.stdin.buffer.read(4096))\n')
    result = sp.run(['/usr/bin/python3', '-IBc', program, str(dest)], input=read_limited(PAIR, private=True, limit=4096),
                    env=ENV, user=account.pw_uid, group=account.pw_gid, extra_groups=[], timeout=8,
                    stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    if result.returncode == 3:
        print(f'Existing {dest} was NOT overwritten. Move it aside privately and run sudo baci-vps export for this VPS.')
        return False
    need(result.returncode == 0, 'Private export could not be written; retry with sudo baci-vps export.')
    print(f'Private connection file: {dest}\nDownload it with SFTP and upload it only in the school administrator panel. Never share it in chat.')
    return True


def doctor_recorder(config, *, wait=False):
    try:
        keys = json_file(PAIR)
        worker = json_file(SPOOL / 'worker.json')['id']
        expected = {'site': config['school'], 'secret': keys['recording_secret']}
        need(keys['site'] == expected['site'] and re.fullmatch(r'[a-f0-9]{64}', expected['secret'])
             and re.fullmatch(r'[a-f0-9]{32}', worker) and json_file(REC_CFG) == expected
             and json_file(REC_CREDENTIAL) == expected, 'Pairing and Recorder service credential differ.')
    except (Refused, OSError, ValueError, KeyError, TypeError):
        print('school_recorder=not_verified reason=local_pairing_mismatch (no files changed; do not regenerate keys)')
        return False
    deadline = time.monotonic() + (35 if wait else 0)
    while True:
        try:
            result = recorder_status(config, keys, worker)
        except RecorderProbeFailed as error:
            print(f'school_recorder=not_verified reason={error.reason} ({error})')
            return False
        if not result['worker_ok']:
            print('school_recorder=another_worker (the school recently heard a different Recorder; do not delete its queue)')
            return False
        if result['native_ready']:
            print('school_recorder=ready')
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print('school_recorder=paired_waiting_for_heartbeat (authenticated; check Recorder journal, spool space and any previous worker)')
            return False
        time.sleep(min(2, remaining))


def doctor(config, online, *, wait=False):
    try:
        local_checks(config)
        expires = edge_certificate(config)
    except (OSError, ValueError, http.client.HTTPException):
        raise Refused('Local readiness check failed (media, HTTPS, or installed Recorder).') from None
    status = (f"profile={config.get('profile', 'full')} recorder=ok renderer=ok" if with_recording(config)
              else 'profile=live recorder=not_installed renderer=not_installed')
    print(f'local=ok https=ok admission=ok media_api=ok version={VERSION} ' + status)
    print(f"tls={config.get('tls_mode', 'files')} expires={expires}")
    if not online:
        return True
    ok = True
    try:
        check_media_dns(config)
        print('media_dns=ok')
    except (Refused, OSError):
        print('media_dns=not_verified (check A record, remove AAAA/proxy, wait for propagation)')
        ok = False
    deployment = media.pilot_deployment()
    try:
        media.verify_receiver(deployment, media.webhook_url(config['origin'], config['site_path']))
        print('school_webhook=ok (signed probe; no school data changed)')
    except (ValueError, OSError):
        print('school_webhook=not_verified (check the school URL, import, activation, HTTPS and Authorization header)')
        ok = False
    if with_recording(config):
        recorder_ok = doctor_recorder(config, wait=wait)
        ok = ok and recorder_ok
    print('Still required: open https://' + config['media'] + '/healthz from another network and try two test devices (real UDP/media).')
    if with_recording(config):
        print('Also verify a consented MP4 in the actual configured group after the school delay and with no open class.')
    return ok


def main(argv=None):
    installed = HERE == APP
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    if installed:
        sub = parser.add_subparsers(dest='action', required=True)
        check = sub.add_parser('doctor', help='local read-only checks', allow_abbrev=False)
        check.add_argument('--online', action='store_true', help='also verify DNS, signed school webhook and the actual Recorder protocol')
        check.add_argument('--wait', action='store_true', help='after pairing, wait briefly for a fresh heartbeat (requires --online)')
        sub.add_parser('export', help='copy the private connection file to your SSH home without overwriting anything')
    else:
        parser.add_argument('--site')
        parser.add_argument('--media')
        parser.add_argument('--ipv4')
        parser.add_argument('--tls', choices=('auto', 'files'), help='automatic Let’s Encrypt, or fully offline with two PEM files')
        parser.add_argument('--cert', type=Path, help='in files mode: default ./fullchain.pem')
        parser.add_argument('--key', type=Path, help='in files mode: default ./privkey.pem, mode 0600')
        parser.add_argument('--profile', choices=('auto', *PROFILES), default='auto',
                            help='auto selects compact/full with recording; choose live explicitly for live classes without recording')
        parser.add_argument('--check', action='store_true', help='read-only install or add-on preflight')
        change = parser.add_mutually_exclusive_group()
        change.add_argument('--add-recorder', action='store_true', help='add recording to an idle offline live install; reuse pairing and HTTPS')
        change.add_argument('--update-recorder', action='store_true', help='update an idle managed r3 Recorder without replacing keys, TLS or media')
        change.add_argument('--update-udp', action='store_true', help='enable UDP batching on an idle, unmodified managed SFU')
        change.add_argument('--restore-udp', action='store_true', help='restore the exact stock UDP layout without batching')
    args = parser.parse_args(argv)
    if installed and args.action == 'doctor' and args.wait and not args.online:
        parser.error('--wait requires --online')
    os.umask(0o077)
    txn = None
    try:
        system_preflight()
        if STATE.is_file():
            config = json_file(STATE)
            if installed and args.action == 'export':
                return 0 if export_pair() else 1
            if not installed and (args.update_udp or args.restore_udp):
                need(args.profile == 'auto' and all(getattr(args, k) is None for k in ('site', 'media', 'ipv4', 'tls', 'cert', 'key')),
                     'The UDP update reuses the existing addresses and configuration; do not pass new inputs.')
                try:
                    media.configure_udp(argparse.Namespace(apply=not args.check, restart=not args.check, disable=args.restore_udp))
                except ValueError as error:
                    raise Refused(str(error)) from None
                return 0
            if not installed and args.update_recorder:
                need(args.profile == 'auto' and all(getattr(args, k) is None for k in ('site', 'media', 'ipv4', 'tls', 'cert', 'key')),
                     '--update-recorder reuses the existing profile, addresses and TLS; do not pass new inputs.')
                update_recorder(config, check=args.check)
                return 0
            if not installed and args.add_recorder:
                need(all(getattr(args, k) is None for k in ('site', 'media', 'ipv4', 'tls', 'cert', 'key')),
                     '--add-recorder uses the existing addresses and TLS; do not pass new inputs.')
                worker = secrets.token_hex(16)
                profile, keys, total = addition_preflight(config, args.profile, worker)
                if args.check:
                    deb_preflight(profile, addition=True)
                    print(f'add_recorder_preflight=ok profile={profile} ram_kib={total} (read-only; no settings changed)')
                    return 0
                deb_install(profile, addition=True)
                addition_preflight(config, profile, worker)
                txn = RecorderAddition(profile)
                txn.apply_addition(config, profile, keys, worker)
                txn = None
                print(f'add_recorder=ok profile={profile} renderer_mp4=ok school_recorder=ready; existing pairing and HTTPS retained.')
                return 0
            if not installed:
                print('Already installed. No settings, keys or services were changed. This fresh installer is not an in-place upgrade.')
                # Do not run the candidate's checks against older installed files.
                if config.get('recorder_bundle', config.get('version')) != VERSION:
                    print('Use the existing sudo baci-vps doctor; do not reset the VPS to retry an installation.')
                    return 1
                return 0 if doctor(config, False) else 1
            return 0 if doctor(config, args.online, wait=args.wait) else 1
        need(not installed and not (args.add_recorder or args.update_recorder or args.update_udp or args.restore_udp), 'No completed offline installation was found.')
        total_kib, profile = fresh_preflight(args.profile)
        verify_payloads()
        if profile == 'live':
            print('حالت کلاس زنده: ضبط و ساخت فیلم روی این VPS نصب نمی‌شود.', flush=True)
        config = names(prompt(args.site, 'نشانی HTTPS خودِ سایت مدرسه، با زیرپوشه اگر دارد (نمونه: https://school.example.ir/baci)'),
                       prompt(args.media, 'نام زیردامنهٔ رسانه بدون https (نمونه: class.example.ir)'),
                       prompt(args.ipv4, 'IPv4 عمومی VPS جدید از پنل ارائه‌دهنده'))
        mode, cert, key = tls_selection(args.tls, args.cert, args.key)
        config.update(tls_mode=mode, profile=profile)
        cert_files = None
        have_trust = TRUST_ROOTS.is_file()
        if mode == 'files':
            cert_files = cert_inputs(cert, key)
            if Path('/usr/bin/openssl').is_file() and have_trust:
                validate_cert(config['media'], *cert_files)
            elif args.check:
                raise Refused('Ubuntu base has no OpenSSL/trust store; full install supplies both offline.')
        else:
            print('حالت SSL خودکار: بررسی DNS و دسترسی HTTPS به Let’s Encrypt…', flush=True)
            check_media_dns(config)
            if have_trust:
                check_acme_access()
            elif args.check:
                raise Refused('The Ubuntu base has no trust store; full install supplies it before ACME.')
        if args.check:
            deb_preflight(profile)
            suffix = ('DNS and ACME directory reachable; no certificate was ordered; check incoming TCP 443 in the provider panel'
                      if mode == 'auto' else 'local files only; DNS, firewall and pairing are separate')
            print(f'preflight=ok tls={mode} profile={profile} ram_kib={total_kib} ({suffix})')
            return 0
        deb_install(profile)
        if mode == 'files':
            validate_cert(config['media'], *cert_files)
        else:
            # Recheck after apt, before creating users, files or any certificate order.
            check_media_dns(config)
            if not have_trust:
                check_acme_access()
        fresh_preflight(profile)
        components = 'کلاس، ضبط و HTTPS' if with_recording(config) else 'کلاس زنده و HTTPS'
        print(f'۲/۴  نصب سرویس‌های {components}…', flush=True)
        txn = Fresh(profile)
        txn.apply(config, cert_files)
        txn = None  # The durable install is complete; do not undo it if exporting fails.
        recording = 'renderer_mp4=ok' if with_recording(config) else 'recorder=not_installed'
        print(f'installation=ok tls={mode} profile={profile} local_https=ok {recording} school_pairing=required')
        if not export_pair():
            return 1
        print('گام بعد: فایل خصوصی اتصال را در پنل همان مدرسه وارد و کلاس و ضبط را فعال کن. سپس روی VPS:')
        print('  sudo baci-vps doctor --online --wait')
        return 0
    except (Refused, OSError, ValueError, KeyboardInterrupt, EOFError, KeyError, http.client.HTTPException, sp.SubprocessError, tarfile.TarError, BadZipFile) as error:
        if txn:
            try:
                txn.cleanup()
                print(('Incomplete Recorder add-on rolled back; live services were not changed.' if isinstance(txn, RecorderAddition)
                       else 'Incomplete classroom services rolled back.') + ' Installed Ubuntu packages are left in place.', file=sys.stderr)
            except (Refused, OSError):
                print('Rollback could not be confirmed. Do not delete classroom paths; seek help before retrying.', file=sys.stderr)
        message = str(error) if isinstance(error, Refused) else f'Local operation failed ({type(error).__name__}); see START-HERE-fa.md.'
        print('Baci VPS: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
