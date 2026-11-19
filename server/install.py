#!/usr/bin/env python3
"""Prepare an isolated LiveKit school-media pilot on Ubuntu 24.04/systemd."""
import argparse
import hashlib
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
import subprocess
import sys
import tarfile
import tempfile

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


def hostname(value):
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if len(value) > 253 or not re.fullmatch(rf'(?:{label}\.)+{label}', value.lower()):
        raise argparse.ArgumentTypeError('use a fully qualified DNS hostname without a scheme or port')
    return value.lower()


def school_origin(value):
    if not value.startswith('https://'):
        raise argparse.ArgumentTypeError('use the HTTPS origin of the test school site')
    return 'https://' + hostname(value[8:])


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
    sfu_unit = SYSTEMD / f'{NAME}-sfu.service'
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
        write_private(CFG / 'livekit.yaml', engine_config(args.ipv4, api_key, secret), gid=account.pw_gid)
        write_private(CFG / 'admission.json', json.dumps({
            'api_key': api_key, 'api_secret': secret, 'school_origin': origin,
        }) + '\n', gid=account.pw_gid)
        write_private(CFG / 'school-values.txt', f'site_origin={origin}\nsignal_url=wss://{args.media_host}\napi_key={api_key}\napi_secret={secret}\n', mode=0o600)
        write_private(CFG / 'Caddyfile', caddy_config(args.media_host, origin), mode=0o644)
        write_private(sfu_unit, unit('School classroom LiveKit audio SFU', f'{APP}/livekit-server --config {CFG}/livekit.yaml'), mode=0o644)
        write_private(gate_unit, unit('School classroom signaling admission', f'/usr/bin/python3 -I {APP}/admission.py {CFG}/admission.json'), mode=0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    print(f'media_prepared=yes version={VERSION} started=no')
    print(f'Caddy site: {CFG}/Caddyfile; private site values: {CFG}/school-values.txt')
    print('Next: configure firewall and import the Caddy site, then start both services per the guide.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('media_host', type=hostname)
    parser.add_argument('ipv4', type=public_ip)
    parser.add_argument('school_origin', type=school_origin)
    parser.add_argument('--archive', type=Path, help='verify and use the pinned local LiveKit archive instead of downloading')
    try:
        install(parser.parse_args())
    except (ValueError, OSError, tarfile.TarError) as error:
        sys.exit(f'School classroom setup: {error}')
    except subprocess.CalledProcessError as error:
        sys.exit(f'School classroom setup: {error.cmd[0]} failed (exit {error.returncode})')


if __name__ == '__main__':
    main()
