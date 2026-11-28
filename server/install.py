#!/usr/bin/env python3
"""Prepare an isolated LiveKit school-media pilot on Ubuntu 24.04/systemd."""
import argparse
import base64
import contextlib
import fcntl
from fractions import Fraction
import hashlib
import hmac
import ipaddress
import itertools
import json
from math import ceil
import mmap
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


UDP_BATCH = '  batch_io:\n    batch_size: 64\n    max_flush_interval: 1ms\n'


def engine_config(ip, api_key, secret, *, camera=True, udp_batch=True, bound_udp=False):
    video = '    - mime: video/VP8\n' if camera else ''
    ips = f'  ips:\n    includes: [{ip}/32]\n' if bound_udp else ''
    batch = UDP_BATCH if udp_batch else ''
    return f'''port: 7880
bind_addresses: [127.0.0.1]
rtc:
{ips}  node_ip: {ip}
  use_external_ip: false
  udp_port: 7882
{batch}  tcp_port: 7881
room:
  auto_create: false
  max_participants: 4
  empty_timeout: 300
  departure_timeout: 20
  enabled_codecs:
    - mime: audio/opus
{video}turn:
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


def unit(description, exec_start, *, environment=''):
    return f'''[Unit]
Description={description}
After=network.target

[Service]
Type=simple
User={NAME}
Group={NAME}
ExecStart={exec_start}
{environment}UMask=0077
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


def sfu_unit_config(*, legacy=False, one_core=False, network_online=False):
    description = 'School classroom LiveKit audio SFU' if legacy else 'School classroom LiveKit SFU'
    # The pinned release's Go runtime sizes its scheduler. Remove even a pin
    # inherited from the systemd manager, without touching the CPU allocation.
    environment = 'Environment=GOMAXPROCS=1\n' if one_core else 'UnsetEnvironment=GOMAXPROCS\n'
    text = unit(description, f'{APP}/livekit-server --config {CFG}/livekit.yaml', environment=environment)
    return text.replace('After=network.target\n', 'Wants=network-online.target\nAfter=network-online.target\n', 1) if network_online else text


def stock_sfu_unit(text):
    if text == sfu_unit_config(network_online=True):
        return False, False  # The offline installer waits for its assigned UDP address.
    for legacy in (False, True):
        for one_core in (False, True):
            if text == sfu_unit_config(legacy=legacy, one_core=one_core):
                return legacy, one_core
    raise ValueError('This is not the pilot systemd unit; automatic restart refused')


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


def installed_layout(current, deployment):
    """Recognize only exact stock layouts before and after UDP batching, including offline installs."""
    ip = re.search(r'^  node_ip: ([0-9.]+)$', current, re.MULTILINE)
    if not ip:
        raise ValueError('LiveKit configuration is not from this pilot installer; use the manual guide')
    ip = str(ipaddress.IPv4Address(ip[1]))
    for camera, batch, bound in itertools.product((False, True), repeat=3):
        layout = dict(camera=camera, udp_batch=batch, bound_udp=bound)
        base = engine_config(ip, deployment['api_key'], deployment['api_secret'], **layout)
        if current == base:
            return ip, layout, None
        if not current.startswith(base + WEBHOOK_MARKER + '\n'):
            continue
        # A block edited by hand or pointing to another site must never be overwritten.
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
        return ip, layout, previous_path
    raise ValueError('Existing/custom LiveKit configuration was left unchanged; use the manual guide')


def registered_config(current, deployment, path):
    ip, layout, previous_path = installed_layout(current, deployment)
    base = engine_config(ip, deployment['api_key'], deployment['api_secret'], **layout)
    url = webhook_url(deployment['school_origin'], (previous_path or '') if path is None else path)
    return base + native_webhook_block(deployment['api_key'], url), url


def update_engine_config(current, deployment, **change):
    ip, layout, previous_path = installed_layout(current, deployment)
    wanted = engine_config(ip, deployment['api_key'], deployment['api_secret'], **(layout | change))
    if previous_path is not None:
        wanted += native_webhook_block(deployment['api_key'], webhook_url(deployment['school_origin'], previous_path))
    return wanted


def camera_config(current, deployment):
    return update_engine_config(current, deployment, camera=True)


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
            raise ValueError('Another media setup is in progress; try again later') from error
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


def replace_pilot_config(path, expected, new, info, *, private=True):
    # Write and sync next to the original, then replace only after checking it was not edited in the meantime.
    fd, scratch = tempfile.mkstemp(prefix='.livekit-webhook-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            stream.write(new)
            stream.flush()
            os.fsync(stream.fileno())
        current, actual = read_pilot_file(path, private=private)
        if current != expected or (actual.st_uid, actual.st_gid, actual.st_mode) != (info.st_uid, info.st_gid, info.st_mode):
            raise ValueError(f'Managed file changed concurrently ({path.name}); it was not overwritten')
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


def sfu_properties(*keys):
    values = subprocess.run(['systemctl', 'show', SFU_SERVICE, '--all', '--property=' + ','.join(keys)],
                            capture_output=True, text=True, check=True, timeout=8).stdout.splitlines()
    return dict(line.split('=', 1) for line in values if '=' in line)


def check_restart_target(deployment, *, pid=None):
    service_file = SYSTEMD / SFU_SERVICE
    stock_sfu_unit(read_pilot_file(service_file, private=False)[0])
    expected = {'FragmentPath': str(service_file), 'DropInPaths': '', 'ActiveState': 'active', 'NeedDaemonReload': 'no'}
    if pid is not None:
        expected['MainPID'] = str(pid)
    props = sfu_properties(*expected)
    if any(props.get(name) != value for name, value in expected.items()):
        hint = '; apply without --restart instead' if pid is None else ''
        raise ValueError('Automatic restart needs the running, up-to-date pilot unit without overrides' + hint)
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


def require_media_sudo():
    if os.geteuid() != 0 or platform.system() != 'Linux':
        raise ValueError('Run media setup locally on the test media VPS with sudo')


def go_build_info(path):
    """Read the inline Go 1.18+ build record from a Linux ELF without executing it."""
    magic = b'\xff Go buildinf:'
    frame = (bytes.fromhex('3077af0c9274080241e1c107e6d618e6'), bytes.fromhex('f932433186182072008242104116d8f2'))

    def field(data, first, maximum):
        size = 0
        for shift in (0, 7, 14):
            b = data[first]
            first += 1
            size |= (b & 127) << shift
            if b < 128 and size <= maximum and first + size <= len(data):
                return data[first:first + size], first + size
            if b < 128:
                break
        raise ValueError('Invalid Go build record')

    with path.open('rb') as source:
        if source.read(4) != b'\x7fELF':
            return None, []
        with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as data:
            first = data.find(magic)
            while first != -1:
                if first % 16 == 0 and first + 32 < len(data) and data[first + 15] & 2:
                    try:
                        version, cursor = field(data, first + 32, 64)
                        metadata, _ = field(data, cursor, 1024 * 1024)
                    except (ValueError, IndexError):
                        pass  # The magic may also occur as an ordinary string constant.
                    else:
                        if metadata.startswith(frame[0]) and metadata.endswith(frame[1]):
                            return version.decode('ascii', 'replace'), metadata[16:-16].decode('utf-8', 'replace').splitlines()
                first = data.find(magic, first + len(magic))
    return None, []


def go_cpu_policy(version, metadata, debug):
    if not any(re.fullmatch(r'path\tgithub\.com/livekit/livekit(?:-server)?/cmd/server', line) for line in metadata):
        version = None
    match = re.fullmatch(r'go1\.(\d{1,3})(?:\.\d{1,4})?', version or '', re.ASCII)
    minor = int(match[1]) if match else None
    if minor is None:
        return None, None, None
    # A Go >=1.25 compiler can still build a module with older language defaults.
    # The running environment takes precedence over DefaultGODEBUG in the binary.
    built = next((row.partition('=')[2].strip('"') for row in metadata if row.startswith('build\tDefaultGODEBUG=')), '')
    settings = dict(item.split('=', 1) for item in (built + ',' + debug).split(',') if '=' in item)
    auto = minor >= 25 and all(settings.get(key, '1') == '1' for key in ('containermaxprocs', 'updatemaxprocs'))
    return version, minor, auto


def cgroup_cpu_limits(membership, mountinfo):
    """Return CPU cgroup version and unit/hierarchy hard quotas (None = unlimited)."""
    groups = [row.split(':', 2) for row in membership.splitlines()]
    v1 = next((row[2] for row in groups if len(row) == 3 and 'cpu' in row[1].split(',')), None)
    v2 = next((row[2] for row in groups if len(row) == 3 and row[0] == '0' and not row[1]), None)
    version, group = (1, v1) if v1 is not None else (2, v2)
    if not group or not group.startswith('/') or '..' in PurePosixPath(group).parts:
        raise ValueError('Unknown CPU cgroup')
    for row in mountinfo.splitlines():
        left, sep, right = row.partition(' - ')
        fields, fs = left.split(), right.split()
        # A subtree-only mount cannot tell us the limits of hidden ancestors.
        if not sep or len(fields) < 5 or fields[3] != '/' or len(fs) < 3:
            continue
        if ((version == 2 and fs[0] == 'cgroup2') or
                (version == 1 and fs[0] == 'cgroup' and 'cpu' in fs[2].split(','))):
            base = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4]))
            break
    else:
        raise ValueError('CPU cgroup hierarchy is not visible')

    node = base / group.lstrip('/')
    quotas = []
    while True:
        if not node.is_dir():
            raise ValueError('CPU cgroup disappeared')
        if version == 1:
            quota = (node / 'cpu.cfs_quota_us').read_text().strip()
            period = (node / 'cpu.cfs_period_us').read_text().strip()
            unlimited = '-1'
        else:
            try:
                quota, period = (node / 'cpu.max').read_text().split()
            except FileNotFoundError:
                quota, period = 'max', '1'  # CPU controller not enabled here; still inspect parents.
            unlimited = 'max'
        if int(period) <= 0 or (quota != unlimited and int(quota) <= 0):
            raise ValueError('Invalid CPU cgroup quota')
        quotas.append(None if quota == unlimited else Fraction(int(quota), int(period)))
        if node == base:
            return version, quotas[0], min((q for q in quotas if q is not None), default=None)
        node = node.parent


def sfu_cpu_snapshot():
    props = sfu_properties('ActiveState', 'MainPID', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload')
    pid = props.get('MainPID', '')
    if props.get('ActiveState') != 'active' or not re.fullmatch(r'[1-9][0-9]*', pid):
        raise ValueError('The dedicated LiveKit unit is not running; no live CPU settings to inspect')
    service = SYSTEMD / SFU_SERVICE
    layout = 'custom-or-stale'
    if props.get('FragmentPath') == str(service) and props.get('DropInPaths') == '' and props.get('NeedDaemonReload') == 'no':
        try:
            _, one_core = stock_sfu_unit(read_pilot_file(service, private=False)[0])
            layout = 'stock-one-core' if one_core else 'stock-auto'
        except (OSError, ValueError):
            pass  # We can still inspect the live process without trusting the unit on disk.
    pid = int(pid)
    proc = Path('/proc') / str(pid)
    exe = proc / 'exe'
    installed = APP / 'livekit-server'
    running = exe.stat()
    try:
        disk = installed.lstat()
        stock_path = (os.path.samestat(running, disk) and stat.S_ISREG(disk.st_mode)
                      and disk.st_uid == os.geteuid() and not disk.st_mode & 0o022)
    except FileNotFoundError:
        stock_path = False
    version, metadata = go_build_info(exe)
    env = dict(item.split(b'=', 1) for item in (proc / 'environ').read_bytes().split(b'\0') if b'=' in item)
    version, minor, auto = go_cpu_policy(version, metadata, env.get(b'GODEBUG', b'').decode('ascii', 'replace'))
    affinity = len(os.sched_getaffinity(pid))
    try:
        cgroup, leaf, hard = cgroup_cpu_limits((proc / 'cgroup').read_text(), Path('/proc/self/mountinfo').read_text())
    except (OSError, ValueError):
        cgroup, leaf, hard = None, None, None
    if not os.path.samestat(running, exe.stat()):
        raise ValueError('LiveKit executable changed during inspection; recheck')
    return dict(pid=pid, stock_path=stock_path, layout=layout, version=version, minor=minor, auto=auto,
                pin=env.get(b'GOMAXPROCS'), affinity=affinity, cgroup=cgroup, leaf=leaf, hard=hard)


def automatic_procs(affinity, quota):
    # Go 1.25–1.27 rounds the unit quota up and uses at least two Ps if affinity allows.
    return affinity if quota is None else min(affinity, max(2, ceil(quota)))


def check_cpu_allocation(report):
    if not report['stock_path'] or not report['auto'] or not report['cgroup'] or not report['affinity']:
        raise ValueError('Verified automatic CPU sizing needs the installed LiveKit image, its Go defaults, and visible CPU limits; no change made')
    if automatic_procs(report['affinity'], report['leaf']) > automatic_procs(report['affinity'], report['hard']):
        raise ValueError('The Go runtime would not see the tighter parent CPU quota; no change made')


def print_sfu_cpu(report):
    def limit(value):
        return 'unknown' if not report['cgroup'] else ('unlimited' if value is None else f'{float(value):.6g}')

    pin = report['pin']
    safe_pin = 'unset' if pin is None else (pin.decode() if re.fullmatch(rb'[0-9]{1,9}', pin) else 'set (noncanonical)')
    version = report['version'] or 'unknown'
    mode = {True: 'yes', False: 'no', None: 'unknown'}[report['auto']]
    print(f"pid={report['pid']} livekit_go={version} GOMAXPROCS={safe_pin} go_cpu_auto_supported={mode}")
    affinity, leaf, hard = (report[key] for key in ('affinity', 'leaf', 'hard'))
    hierarchy = f"v{report['cgroup']}" if report['cgroup'] else 'unknown'
    print(f'affinity_cpus={affinity} cgroup={hierarchy} unit_quota_cpus={limit(leaf)} hierarchy_quota_cpus={limit(hard)}')
    print(f"unit_layout={report['layout']} running_installed_binary={'yes' if report['stock_path'] else 'no'}")
    if report['auto'] and report['cgroup']:
        guess = automatic_procs(affinity, leaf)
        print(f'go_auto_expected_after_unset={guess} (estimate, not a runtime metric)')
        if guess > automatic_procs(affinity, hard):
            print('The parent CPU quota is tighter than the quota Go reads in the unit; review the effective allocation first.')
    if report['minor'] is not None and report['minor'] < 25:
        legacy = max(1, min(affinity, int(hard))) if hard is not None else affinity
        legacy = legacy if report['cgroup'] else 'unknown'
        print(f'legacy_gomaxprocs_start={legacy} (conservative starting point, not a capacity rating)')


def configure_cpu(args):
    require_media_sudo()
    if not args.apply:
        print_sfu_cpu(sfu_cpu_snapshot())
        return
    with locked_config(True):
        report = sfu_cpu_snapshot()
        print_sfu_cpu(report)
        check_cpu_allocation(report)
        service = SYSTEMD / SFU_SERVICE
        before, info = read_pilot_file(service, private=False)
        legacy, one_core = stock_sfu_unit(before)
        if not one_core and report['pin'] is None:
            print('cpu_auto=yes changed=no restarted=no')
            return
        if not one_core or report['pin'] != b'1':
            raise ValueError('The running CPU setting does not match the stock one-core unit; no change made')
        deployment = pilot_deployment()
        check_restart_target(deployment, pid=report['pid'])
        wanted = sfu_unit_config(legacy=legacy)
        backup = CFG / (SFU_SERVICE + '.before-cpu-' + secrets.token_hex(6) + '.bak')
        write_private(backup, before, mode=0o600)
        print(f'backup={backup}', flush=True)
        replace_pilot_config(service, before, wanted, info, private=False)
        restarted = False
        try:
            subprocess.run(['systemctl', 'daemon-reload'], check=True)
            check_restart_target(deployment, pid=report['pid'])
            restarted = True
            restart_pilot(deployment)
            after = sfu_cpu_snapshot()
            check_cpu_allocation(after)
            if after['pin'] is not None or after['layout'] != 'stock-auto':
                raise ValueError('Could not verify the automatic CPU setting in the restarted SFU')
        except (ValueError, OSError, subprocess.SubprocessError):
            try:
                replace_pilot_config(service, wanted, before, info, private=False)
                subprocess.run(['systemctl', 'daemon-reload'], check=True)
                if restarted:
                    restart_pilot(deployment)
            except (ValueError, OSError, subprocess.SubprocessError):
                raise ValueError(f'Automatic CPU change failed; recovery could not be verified. Review the unit and backup {backup}') from None
            raise ValueError('Automatic CPU change not completed; previous unit restored') from None
        print_sfu_cpu(after)
        print('cpu_auto=yes changed=yes restarted=yes')


def apply_pilot_change(path, before, wanted, info, deployment, restart, change):
    changed = before != wanted
    if restart:
        check_restart_target(deployment)
    if changed:
        backup = CFG / (f'livekit.yaml.before-{change}-' + secrets.token_hex(6) + '.bak')
        write_private(backup, before, mode=0o600)
        print(f'backup={backup}', flush=True)
        replace_pilot_config(path, before, wanted, info)
    if restart:
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
            raise ValueError(f'Restart with the {change} change failed; previous configuration and service restored') from None
    return f'changed={"yes" if changed else "no"} restarted={"yes" if restart else "no"}'


def configure_webhook(args):
    require_media_sudo()
    with locked_config(args.apply):
        deployment = pilot_deployment()
        path = CFG / 'livekit.yaml'
        before, info = read_pilot_file(path)
        wanted, url = registered_config(before, deployment, args.site_path)
        print(f'webhook_url={url}', flush=True)
        if args.verify:
            verify_receiver(deployment, url)
            print('webhook_endpoint_verified=yes (signed probe, not LiveKit event delivery)')
        if not args.apply:
            print(f'webhook_configured={"yes" if before == wanted else "no"} applied=no restarted=no')
            return
        result = apply_pilot_change(path, before, wanted, info, deployment, args.restart, 'webhook')
        print(f'webhook_configured=yes {result}')


def configure_camera(args):
    require_media_sudo()
    with locked_config(args.apply):
        deployment = pilot_deployment()
        path = CFG / 'livekit.yaml'
        before, info = read_pilot_file(path)
        wanted = camera_config(before, deployment)
        if not args.apply:
            print(f'camera_configured={"yes" if before == wanted else "no"} applied=no restarted=no')
            return
        result = apply_pilot_change(path, before, wanted, info, deployment, args.restart, 'camera')
        print(f'camera_configured=yes {result}')


def configure_udp(args):
    # Reuse the exact-config, idle-service check and restart rollback. No address,
    # codec, credential, TCP fallback or webhook is changed by this command.
    require_media_sudo()
    with locked_config(args.apply):
        deployment = pilot_deployment()
        path = CFG / 'livekit.yaml'
        before, info = read_pilot_file(path)
        wanted = update_engine_config(before, deployment, udp_batch=not args.disable)
        if not args.apply:
            print(f'udp_batch={"off" if UDP_BATCH not in before else "on"} change_needed={"yes" if before != wanted else "no"}')
        elif before == wanted:
            print(f'udp_batch={"off" if args.disable else "on"} changed=no restarted=no')
        else:
            result = apply_pilot_change(path, before, wanted, info, deployment, True, 'udp')
            print(f'udp_batch={"off" if args.disable else "on"} {result}')


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
    changes = {'webhook': configure_webhook, 'camera': configure_camera, 'cpu': configure_cpu, 'udp': configure_udp}
    if argv and argv[0] in changes:
        change = argv[0]
        description = ('Inspect the running LiveKit Go build and CPU limits; optionally enable automatic sizing on the stock SFU.'
                       if change == 'cpu' else f'Configure {change} on the existing dedicated media pilot.')
        parser = argparse.ArgumentParser(prog='install.py ' + change, description=description)
        if change == 'webhook':
            parser.add_argument('--site-path', type=site_path, help='test school subpath; default keeps a managed URL, or uses / for new setup')
            parser.add_argument('--verify', action='store_true', help='verify the receiver with a signed read-only probe')
        if change == 'udp':
            parser.add_argument('--disable', action='store_true', help='restore stock UDP without batching')
        parser.add_argument('--apply', action='store_true', help='save the configuration after making a private backup; default is read-only')
        parser.add_argument('--restart', action='store_true', help='restart only the running, idle, unmodified pilot SFU unit; requires --apply')
        args, work = parser.parse_args(argv[1:]), changes[change]
        if args.restart and not args.apply:
            parser.error('--restart requires --apply')
        if change in ('cpu', 'udp') and args.apply and not args.restart:
            parser.error('This change requires both --apply and --restart in a maintenance window')
    else:
        parser = argparse.ArgumentParser(description=__doc__, epilog='Existing pilot? See install.py {webhook,camera,cpu,udp} --help.')
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
