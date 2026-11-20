#!/usr/bin/env python3
"""Prepare an isolated LiveKit school-media pilot on Ubuntu 24.04/systemd."""
import argparse
import base64
import contextlib
import fcntl
import hashlib
import hmac
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
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from http.client import HTTPException
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

VERSION = '1.13.11'
ASSET = f'livekit_{VERSION}_linux_amd64.tar.gz'
SHA256 = '1e9ee90ca301e1842b2887fc77cb122759756a9c8aeb4334ee91de3325ec43fb'
URL = f'https://github.com/livekit/livekit/releases/download/v{VERSION}/{ASSET}'
NAME = 'school-classroom'
APP = Path('/opt') / NAME
CFG = Path('/etc') / NAME
SYSTEMD = Path('/etc/systemd/system')
SIGNAL_PATHS = '/rtc /rtc/validate /rtc/v1 /rtc/v1/validate'
ROOM_METHODS = ['CreateRoom', 'DeleteRoom', 'ListRooms', 'ListParticipants', 'UpdateParticipant']
SFU_SERVICE = NAME + '-sfu.service'
EVENT_ROUTE = '/plugins/school-plugins.php?plugin=school-classroom&route=events'
WEBHOOK_MARKER = '# BEGIN school-classroom native webhook (managed)'
CONFIG_LIMIT = 64 * 1024


def hostname(value):
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if len(value) > 253 or not re.fullmatch(rf'(?:{label}\.)+{label}', value.lower()):
        raise argparse.ArgumentTypeError('use a fully qualified DNS hostname without a scheme or port')
    return value.lower()


def school_origin(value):
    if not value.startswith('https://'):
        raise argparse.ArgumentTypeError('use the HTTPS origin of the test school site')
    return 'https://' + hostname(value[8:])


def site_path(value):
    if value == '/':
        return ''
    if len(value) > 220 or not re.fullmatch(r'(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63})*', value):
        raise argparse.ArgumentTypeError('use only the site subpath, e.g. /school-test (no trailing slash)')
    return value


def public_ip(value):
    ip = ipaddress.IPv4Address(value)
    if not ip.is_global:
        raise argparse.ArgumentTypeError('use the assigned public IPv4 from your provider')
    return str(ip)


def write_private(file, content, mode=0o640, gid=0):
    fd = os.open(file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        os.fchown(out.fileno(), 0, gid)
        os.fchmod(out.fileno(), mode)
        out.write(content)


def engine_config(ip, api_key, secret):
    return f'''port: 7880
bind_addresses: [127.0.0.1]
rtc:
  node_ip: {ip}
  use_external_ip: false
  udp_port: 7882
  tcp_port: 7881
room:
  auto_create: false
  max_participants: 4
  empty_timeout: 300
  departure_timeout: 20
  enabled_codecs:
    - mime: audio/opus
turn:
  enabled: false
keys:
  "{api_key}": "{secret}"
logging:
  level: warn
'''


def caddy_config(host, origin):
    room_paths = ' '.join('/twirp/livekit.RoomService/' + rpc for rpc in ROOM_METHODS)
    return f'''{host} {{
    @classroomPreflight {{
        method OPTIONS
        path {SIGNAL_PATHS}
        header Origin {origin}
    }}
    handle @classroomPreflight {{
        header Access-Control-Allow-Origin {origin}
        header Access-Control-Allow-Methods "GET, POST, OPTIONS"
        header Access-Control-Allow-Headers "Authorization, Content-Type"
        respond 204
    }}
    @classroomSignal path {SIGNAL_PATHS}
    handle @classroomSignal {{
        route {{
            forward_auth 127.0.0.1:7883 {{
                uri /admit
            }}
            reverse_proxy 127.0.0.1:7880
        }}
    }}
    @classroomRoomService {{
        method POST
        path {room_paths}
    }}
    handle @classroomRoomService {{
        reverse_proxy 127.0.0.1:7880
    }}
    handle /healthz {{
        reverse_proxy 127.0.0.1:7880 {{
            rewrite /
        }}
    }}
    handle {{
        respond 404
    }}
}}
'''


def unit(description, exec_start):
    return f'''[Unit]
Description={description}
After=network.target

[Service]
Type=simple
User={NAME}
Group={NAME}
ExecStart={exec_start}
Environment=GOMAXPROCS=1
UMask=0077
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
ProtectHome=yes
ProtectSystem=strict
PrivateTmp=yes
CapabilityBoundingSet=
LimitNOFILE=8192

[Install]
WantedBy=multi-user.target
'''


def sfu_unit_config():
    return unit('School classroom LiveKit audio SFU', f'{APP}/livekit-server --config {CFG}/livekit.yaml')


def webhook_url(origin, path):
    return school_origin(origin) + site_path(path) + EVENT_ROUTE


def native_webhook_block(api_key, url):
    return f'''{WEBHOOK_MARKER}
webhook:
  api_key: "{api_key}"
  urls:
    - '{url}'
api:
  enable_psrpc_for_get_list_participants: true
# END school-classroom native webhook
'''


def registered_config(current, deployment, path):
    """Only update the exact shipped configuration, not an arbitrary or shared YAML file."""
    ip = re.search(r'^  node_ip: ([0-9.]+)$', current, re.MULTILINE)
    if not ip:
        raise ValueError('LiveKit configuration is not from this pilot installer; use the manual guide')
    base = engine_config(str(ipaddress.IPv4Address(ip[1])), deployment['api_key'], deployment['api_secret'])
    previous_path = ''
    if current != base:
        if not current.startswith(base + WEBHOOK_MARKER + '\n'):
            raise ValueError('Existing/custom LiveKit configuration was left unchanged; use the manual guide')
        # Recognize exactly an earlier block of ours on the same configured test origin.
        previous = re.search(r"^    - '(https://[^'\n]+)'$", current[len(base):], re.MULTILINE)
        prefix = deployment['school_origin']
        if not previous or not previous[1].startswith(prefix + '/') or not previous[1].endswith(EVENT_ROUTE):
            raise ValueError('The managed webhook points outside this test installation; no change made')
        previous_path = previous[1][len(prefix):-len(EVENT_ROUTE)]
        try:
            old_url = webhook_url(prefix, previous_path)
        except argparse.ArgumentTypeError as error:
            raise ValueError('Unrecognized managed webhook block; no change made') from error
        if current != base + native_webhook_block(deployment['api_key'], old_url):
            raise ValueError('The managed webhook block was edited; no change made')
    url = webhook_url(deployment['school_origin'], previous_path if path is None else path)
    return base + native_webhook_block(deployment['api_key'], url), url


@contextlib.contextmanager
def locked_config(exclusive):
    if CFG.resolve(strict=True) != CFG:
        raise ValueError('The pilot configuration directory must not be a symlink')
    fd = os.open(CFG, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('The pilot configuration directory must be root-owned and not writable by other users')
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another webhook setup is in progress; try again later') from error
        yield
    finally:
        os.close(fd)


def read_pilot_file(path, private=True):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        unsafe = 0o027 if private else 0o022
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & unsafe:
            raise ValueError(f'{path.name} has unsafe ownership, permissions or file type')
        raw = stream.read(CONFIG_LIMIT + 1)
    if len(raw) > CONFIG_LIMIT:
        raise ValueError(f'{path.name} is not a supported pilot file')
    return raw.decode('utf-8'), info


def pilot_deployment():
    raw, _ = read_pilot_file(CFG / 'admission.json')
    try:
        obj = json.loads(raw)
        deployment = {name: obj[name] for name in ('school_origin', 'api_key', 'api_secret')}
        if school_origin(deployment['school_origin']) != deployment['school_origin']:
            raise ValueError('Noncanonical origin')
        if not re.fullmatch(r'[A-Za-z0-9_-]{12,64}', deployment['api_key']) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', deployment['api_secret']):
            raise ValueError('Invalid signing configuration')
        return deployment
    except (ValueError, TypeError, AttributeError, KeyError, argparse.ArgumentTypeError):
        # Never include JSON contents in a diagnostic.
        raise ValueError('admission.json does not describe a supported test installation') from None


def replace_pilot_config(path, expected, new, info):
    # Write and sync next to the original, then replace only after checking it was not edited in the meantime.
    fd, scratch = tempfile.mkstemp(prefix='.livekit-webhook-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            stream.write(new)
            stream.flush()
            os.fsync(stream.fileno())
        current, actual = read_pilot_file(path)
        if current != expected or (actual.st_uid, actual.st_gid, actual.st_mode) != (info.st_uid, info.st_gid, info.st_mode):
            raise ValueError('LiveKit configuration changed concurrently; it was not overwritten')
        os.replace(scratch, path)
    finally:
        Path(scratch).unlink(missing_ok=True)


def jwt(deployment, **claims):
    now = int(time.time())
    def enc(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b'=')
    payload = json.dumps({'iss': deployment['api_key'], 'nbf': now - 5, 'exp': now + 30, **claims}, separators=(',', ':')).encode()
    unsigned = enc(b'{"alg":"HS256","typ":"JWT"}') + b'.' + enc(payload)
    return (unsigned + b'.' + enc(hmac.digest(deployment['api_secret'].encode(), unsigned, 'sha256'))).decode('ascii')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an Authorization header to another URL.


def post_json(url, body, mime, authorization, timeout):
    request = Request(url, data=body, headers={'Content-Type': mime, 'Authorization': authorization})
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
            if response.status != 200 or response.headers.get_content_type() != 'application/json':
                raise ValueError('Unexpected server response')
            raw = response.read(1024 * 1024 + 1)
            declared_size = response.headers.get('Content-Length')
            if len(raw) > 1024 * 1024 or (declared_size is not None and declared_size != str(len(raw))):
                raise ValueError('Unexpected server response')
    except HTTPException:
        raise ValueError('Incomplete server response') from None
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError('Unexpected server response')
    return result


def verify_receiver(deployment, url):
    # The plugin verifies an ordinary signed callback then ignores this non-room event.
    body = b'{"event":"school_classroom_webhook_check"}'
    checksum = base64.b64encode(hashlib.sha256(body).digest()).decode('ascii')
    try:
        result = post_json(url, body, 'application/webhook+json', jwt(deployment, sha256=checksum), 6)
    except (ValueError, OSError):
        raise ValueError('The school endpoint did not accept the signed probe; check HTTPS, subpath, activation and the media key') from None
    if set(result) != {'ok'} or result['ok'] is not True:
        raise ValueError('The school endpoint did not confirm the signed probe')


def local_rooms(deployment):
    result = post_json('http://127.0.0.1:7880/twirp/livekit.RoomService/ListRooms', b'{}',
                       'application/json', 'Bearer ' + jwt(deployment, video={'roomList': True}), 2)
    # Twirp may serialize an empty repeated field as either {"rooms": []} or {}.
    rooms = result.get('rooms', [])
    if set(result) - {'rooms'} or not isinstance(rooms, list):
        raise ValueError('Invalid LiveKit room list')
    return rooms


def check_restart_target(deployment):
    service_file = SYSTEMD / SFU_SERVICE
    if read_pilot_file(service_file, private=False)[0] != sfu_unit_config():
        raise ValueError('This is not the pilot systemd unit; automatic restart refused')
    values = subprocess.run(['systemctl', 'show', SFU_SERVICE, '--all',
                             '--property=FragmentPath,DropInPaths,ActiveState,NeedDaemonReload'],
                            capture_output=True, text=True, check=True, timeout=8).stdout.splitlines()
    props = dict(line.split('=', 1) for line in values if '=' in line)
    expected = {'FragmentPath': str(service_file), 'DropInPaths': '', 'ActiveState': 'active', 'NeedDaemonReload': 'no'}
    if any(props.get(name) != value for name, value in expected.items()):
        raise ValueError('Automatic restart needs the running, up-to-date pilot unit without overrides; apply without --restart instead')
    try:
        rooms = local_rooms(deployment)
    except (ValueError, OSError):
        raise ValueError('Could not confirm an idle LiveKit over its local API; automatic restart refused') from None
    if rooms:
        raise ValueError('LiveKit has open rooms; wait for classes to finish before restarting')


def restart_pilot(deployment):
    # Wait for systemd's job to finish. Killing systemctl alone does not cancel an
    # in-progress restart and could race against restoring the old configuration.
    subprocess.run(['systemctl', 'restart', SFU_SERVICE], check=True)
    until = time.monotonic() + 8
    while True:
        try:
            local_rooms(deployment)
            return
        except (ValueError, OSError):
            if time.monotonic() >= until:
                raise ValueError('LiveKit did not become healthy after restarting') from None
            time.sleep(0.4)


def require_webhook_sudo():
    if os.geteuid() != 0 or platform.system() != 'Linux':
        raise ValueError('Run webhook setup locally on the test media VPS with sudo')


def configure_webhook(args):
    require_webhook_sudo()
    with locked_config(args.apply):
        deployment = pilot_deployment()
        path = CFG / 'livekit.yaml'
        before, info = read_pilot_file(path)
        wanted, url = registered_config(before, deployment, args.site_path)
        changed = before != wanted
        print(f'webhook_url={url}', flush=True)
        if args.verify:
            verify_receiver(deployment, url)
            print('webhook_endpoint_verified=yes (signed probe, not LiveKit event delivery)')
        if not args.apply:
            print(f'webhook_configured={"no" if changed else "yes"} applied=no restarted=no')
            return
        if args.restart:
            check_restart_target(deployment)
        if changed:
            backup = CFG / ('livekit.yaml.before-webhook-' + secrets.token_hex(6) + '.bak')
            write_private(backup, before, mode=0o600)
            print(f'backup={backup}', flush=True)
            replace_pilot_config(path, before, wanted, info)
        if args.restart:
            try:
                restart_pilot(deployment)
            except (ValueError, OSError, subprocess.SubprocessError):
                if not changed:
                    raise ValueError('Restart failed; configuration was not changed') from None
                replace_pilot_config(path, wanted, before, info)
                try:
                    restart_pilot(deployment)
                except (ValueError, OSError, subprocess.SubprocessError):
                    raise ValueError('Previous configuration restored, but LiveKit did not recover; check the test unit') from None
                raise ValueError('Restart with the webhook failed; previous configuration and service restored') from None
        print(f'webhook_configured=yes changed={"yes" if changed else "no"} restarted={"yes" if args.restart else "no"}')


def checked_archive(destination, offline_archive):
    if offline_archive:
        shutil.copyfile(offline_archive, destination)
    else:
        subprocess.run(['curl', '--proto', '=https', '--tlsv1.2', '--fail', '--location',
                        '--silent', '--show-error', '--retry', '2', '--connect-timeout', '10',
                        '--max-time', '240', '--output', str(destination), URL], check=True)
    with destination.open('rb') as file:
        if hashlib.file_digest(file, 'sha256').hexdigest() != SHA256:
            raise ValueError('LiveKit archive does not match the pinned SHA-256')


def extract_binary(package, destination):
    with tarfile.open(package, 'r:gz') as tar:
        entries = [m for m in tar if m.isfile() and PurePosixPath(m.name).name == 'livekit-server']
        if len(entries) != 1:
            raise ValueError('Expected exactly one livekit-server binary in the release')
        with tar.extractfile(entries[0]) as src, destination.open('xb') as dst:
            shutil.copyfileobj(src, dst)
    destination.chmod(0o755)


def install(args):
    if os.geteuid() != 0 or platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise ValueError('Run with sudo on Linux x86_64')
    if not Path('/run/systemd/system').is_dir():
        raise ValueError('A running systemd is required')
    origin = args.school_origin
    if args.media_host == origin[8:]:
        raise ValueError('Use a separate media hostname; the PHP site is not this VPS')
    sfu_unit = SYSTEMD / SFU_SERVICE
    gate_unit = SYSTEMD / f'{NAME}-admission.service'
    for path in (APP, CFG, sfu_unit, gate_unit):
        if os.path.lexists(path):
            raise ValueError(f'Pilot already exists: {path}; no files were overwritten')
    addrs = {entry[4][0] for entry in socket.getaddrinfo(args.media_host, None, socket.AF_INET)}
    if addrs != {args.ipv4}:
        raise ValueError('The media hostname A record must point directly at the one supplied IPv4')
    os.umask(0o022)
    with tempfile.TemporaryDirectory(prefix='school-classroom-') as directory:
        tmp = Path(directory)
        archive = tmp / ASSET
        checked_archive(archive, args.archive)
        binary = tmp / 'livekit-server'
        extract_binary(archive, binary)
        try:
            account = pwd.getpwnam(NAME)
        except KeyError:
            subprocess.run(['useradd', '--system', '--user-group', '--no-create-home', '--home-dir',
                            '/nonexistent', '--shell', '/usr/sbin/nologin', NAME], check=True)
            account = pwd.getpwnam(NAME)
        if account.pw_uid == 0 or account.pw_gid == 0 or account.pw_shell != '/usr/sbin/nologin' or account.pw_dir != '/nonexistent':
            raise ValueError('An incompatible account with the service name already exists')
        api_key, secret = secrets.token_hex(12), secrets.token_hex(32)
        APP.mkdir(mode=0o755)
        CFG.mkdir(mode=0o755)
        shutil.copy2(binary, APP / 'livekit-server')
        shutil.copy2(Path(__file__).with_name('admission.py'), APP / 'admission.py')
        config = engine_config(args.ipv4, api_key, secret)
        if args.webhook:
            callback = webhook_url(origin, args.site_path or '')
            config += native_webhook_block(api_key, callback)
        write_private(CFG / 'livekit.yaml', config, gid=account.pw_gid)
        write_private(CFG / 'admission.json', json.dumps({
            'api_key': api_key, 'api_secret': secret, 'school_origin': origin,
        }) + '\n', gid=account.pw_gid)
        write_private(CFG / 'school-values.txt', f'site_origin={origin}\nsignal_url=wss://{args.media_host}\napi_key={api_key}\napi_secret={secret}\n', mode=0o600)
        write_private(CFG / 'Caddyfile', caddy_config(args.media_host, origin), mode=0o644)
        write_private(sfu_unit, sfu_unit_config(), mode=0o644)
        write_private(gate_unit, unit('School classroom signaling admission', f'/usr/bin/python3 -I {APP}/admission.py {CFG}/admission.json'), mode=0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    print(f'media_prepared=yes version={VERSION} started=no')
    print(f'Caddy site: {CFG}/Caddyfile; private site values: {CFG}/school-values.txt')
    if args.webhook:
        print(f'webhook_configured=yes webhook_url={callback}')
    print('Next: configure firewall and import the Caddy site, then start both services per the guide.')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'webhook':
        parser = argparse.ArgumentParser(prog='install.py webhook', description='Configure the native webhook of the existing test media pilot.')
        parser.add_argument('--site-path', type=site_path, help='site subpath; defaults to the existing setting or domain root on first setup')
        parser.add_argument('--apply', action='store_true', help='save the webhook after making a private config backup; default is read-only')
        parser.add_argument('--restart', action='store_true', help='restart only the idle pilot SFU, after applying; requires --apply')
        parser.add_argument('--verify', action='store_true', help='probe the enabled school endpoint with a signed, non-room event')
        args, work = parser.parse_args(argv[1:]), configure_webhook
        if args.restart and not args.apply:
            parser.error('--restart requires --apply')
    else:
        parser = argparse.ArgumentParser(description=__doc__, epilog='Existing pilot? See install.py webhook --help.')
        parser.add_argument('media_host', type=hostname)
        parser.add_argument('ipv4', type=public_ip)
        parser.add_argument('school_origin', type=school_origin)
        parser.add_argument('--archive', type=Path, help='verify and use the pinned local LiveKit archive instead of downloading')
        parser.add_argument('--webhook', action='store_true', help='include the school native webhook in a fresh installation')
        parser.add_argument('--site-path', type=site_path, help='optional test school subpath for --webhook')
        args, work = parser.parse_args(argv), install
        if args.site_path is not None and not args.webhook:
            parser.error('--site-path requires --webhook')
    try:
        work(args)
    except (ValueError, OSError, tarfile.TarError) as error:
        sys.exit(f'School classroom setup: {error}')
    except subprocess.CalledProcessError as error:
        sys.exit(f'School classroom setup: {error.cmd[0]} failed (exit {error.returncode})')
    except subprocess.TimeoutExpired as error:
        sys.exit(f'School classroom setup: {error.cmd[0]} timed out')


if __name__ == '__main__':
    main()
