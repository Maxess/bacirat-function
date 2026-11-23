#!/usr/bin/env python3
"""Guarded side-by-side update of an idle stock Baci Recorder 0.4.21 or 0.4.22."""
import argparse
import base64
from contextlib import contextmanager, suppress
import fcntl
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile

VERSION = '0.4.23'
PAYLOAD_SHA256 = '663da68a85bdfac47ae2a0fbb48628d856f567e116bf367bdbd28ff935b490d1'
BASE_UNIT_SHA256 = 'cd3543f13478fccddf934d9d15fc1fe79f6f863753c3d7ae27e555c540ec93e0'
STOCK = {'0.4.21': 'd48e2e7e983bbaf934d5c6ccb95ca1a1e088c9458b0cb5e9cd58edd775e0cf19', '0.4.22': '7b69cd132eb37abe7a7856458fc50658a8e7bc69d2ba5e79c238782ff549b046'}
ROOT = Path('/opt/school-classroom-recorder')
SPOOL = Path('/var/lib/school-classroom-recorder')
SYSTEMD = Path('/etc/systemd/system')
CONFIG = Path('/etc/school-classroom-recorder.json')
ADMISSION = Path('/etc/school-classroom/admission.json')
CGROUP = Path('/sys/fs/cgroup/cgroup.controllers')
RUNTIME = Path('/run/school-classroom-postprocess')
RECORDER = 'school-classroom-recorder.service'
POSTPROCESS = 'school-classroom-postprocess.service'
CREDENTIAL = '10-recorder-credential.conf'
NATIVE = '60-native-recorder.conf'
DROPIN = RECORDER + '.d/'
BINARY = 'baci-native-receiver'
COMMON = frozenset('board.js capture.py frame.js livekit-client.LICENSE.txt livekit-client.umd.js pdf-resources.zip '
                   'pdf.LICENSE.txt recording.py requirements.txt transport.py view.css view.html view.js '
                   'fonts/OFL.txt fonts/Vazirmatn-Bold.woff2 fonts/Vazirmatn-Regular.woff2'.split())
DEFERRED = frozenset('archive.py encoded.js ingest.html ingest.js movie.py replay.js'.split())
NEW = frozenset(f'clock.py disk_writer.py native_capture.py postprocess.py {BINARY} baci-native-NOTICES.txt'.split())
PROGRAM = COMMON | DEFERRED | NEW | {'README-fa.md', 'fonts/README.md'}
UNITS = {RECORDER, POSTPROCESS, DROPIN + CREDENTIAL, DROPIN + NATIVE}
HEX = re.compile(r'[a-f0-9]{32}')
FONT_CACHE_FILE = re.compile(r'[a-f0-9]{32}-le64\.cache-[0-9]{1,3}')
HOST = r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
MAX_ZIP = 64 * 1024**2


class Refused(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def exists(path):
    return path.exists() or path.is_symlink()


def directory(path, *, uid=0, private=False):
    st = path.lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == uid and path.resolve() == path
            and not st.st_mode & (0o077 if private else 0o022), f'Unsafe directory: {path}')


@contextmanager
def regular(path, *, uid=0, forbidden=0o022):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid == uid and st.st_nlink == 1 and not st.st_mode & forbidden,
                f'Unexpected ownership, mode or file type: {path.name}')
        yield stream


def private_json(path, *, uid=0, forbidden=0o077):
    with regular(path, uid=uid, forbidden=forbidden) as stream:
        data = stream.read(8193)
    try:
        value = json.loads(data) if len(data) <= 8192 else None
    except (ValueError, RecursionError):
        value = None
    require(isinstance(value, dict), f'Invalid private configuration: {path.name}')
    return value


def sha(path):
    with regular(path) as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(names):
    return hashlib.sha256(''.join(f'{name}\0{sha(ROOT / name)}\n' for name in sorted(names)).encode()).hexdigest()


def stock_version():
    extra = set(name for name in DEFERRED if exists(ROOT / name))
    version = '0.4.22' if extra == DEFERRED else '0.4.21'
    require(extra in (set(), DEFERRED) and not any(exists(ROOT / name) for name in NEW),
            'Existing files are not those of a stock 0.4.21/0.4.22 Recorder.')
    require(inventory(COMMON | extra) == STOCK[version], 'Installed Recorder files do not match stock 0.4.21/0.4.22.')
    return version


def sys_command(program, *args, read=False):
    try:
        # Let systemd itself finish its start/stop jobs (which have unit timeouts).
        output = subprocess.run([f'/usr/bin/{program}', *args], check=True, text=True,
                                timeout=7 if read else None,
                                stdout=subprocess.PIPE if read else subprocess.DEVNULL, stderr=subprocess.DEVNULL).stdout
        return output if read else None
    except (OSError, subprocess.SubprocessError):
        raise Refused(f'{program} could not confirm {args[0]}. Review the unit status locally.') from None


def properties(unit, *keys):
    return dict(line.split('=', 1) for line in sys_command('systemctl', 'show', '--no-pager', '--all',
                '--property=' + ','.join(keys), unit, read=True).splitlines() if '=' in line)


def check_units(payload):
    unit_path = SYSTEMD / RECORDER
    require(sha(unit_path) == BASE_UNIT_SHA256, 'Expected the stock Recorder unit.')
    drop = SYSTEMD / (RECORDER + '.d')
    previous_drop = exists(drop)
    installed = []
    if previous_drop:
        directory(drop)
        installed = sorted(p.name for p in drop.iterdir())
    require(installed in ([], [CREDENTIAL]), 'Custom Recorder drop-in; no automatic update.')
    credential = installed == [CREDENTIAL]
    if credential:
        require(sha(drop / CREDENTIAL) == sha(payload / DROPIN / CREDENTIAL), 'Nonstandard legacy credential drop-in.')
    props = properties(RECORDER, 'FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'SubState')
    require(props == {'FragmentPath': str(unit_path), 'DropInPaths': str(drop / CREDENTIAL) if credential else '',
                      'NeedDaemonReload': 'no', 'SubState': 'running'}, 'Expected the running, loaded stock Recorder.')
    props = properties(POSTPROCESS, 'LoadState', 'DropInPaths')
    require(props.get('LoadState') == 'not-found' and props.get('DropInPaths') == ''
            and not exists(SYSTEMD / POSTPROCESS) and not exists(SYSTEMD / (POSTPROCESS + '.d')),
            'A renderer service already exists; no automatic update.')
    require(properties('school-classroom-sfu.service', 'ActiveState').get('ActiveState') == 'active',
            'Expected the local school-classroom-sfu service to be active.')
    return previous_drop, credential


def read_config():
    config = private_json(CONFIG)
    require(set(config) == {'site', 'secret'} and isinstance(config['site'], str) and isinstance(config['secret'], str)
            and re.fullmatch(r'https://' + HOST + r'(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63})*', config['site'], re.ASCII)
            and re.fullmatch(r'[A-Za-z0-9_-]{32,128}', config['secret']), 'Invalid Recorder configuration.')
    admission = private_json(ADMISSION, forbidden=0o027)
    site = urlsplit(config['site'])
    require(admission.get('school_origin') == f'https://{site.netloc}' and all(
            isinstance(admission.get(k), str) and re.fullmatch(r'[A-Za-z0-9_-]{%s,128}' % minimum, admission[k])
            for k, minimum in (('api_key', 12), ('api_secret', 32))), 'Recorder site is not the configured local LiveKit school.')
    return site, config['secret'], admission


def post(conn, route, auth, body):
    conn.request('POST', route, json.dumps(body).encode(), {'Authorization': 'Bearer ' + auth, 'Content-Type': 'application/json'})
    response = conn.getresponse()  # Never follows redirects or consults an HTTP proxy.
    if response.status != 200 or response.headers.get_content_type() != 'application/json':
        raise ValueError('unexpected response')
    data = response.read(262145)
    if len(data) > 262144 or response.length not in (None, 0):
        raise ValueError('invalid response size')
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError('invalid JSON object')
    return result


def livekit_rooms(admission):
    def b64(data):
        return base64.urlsafe_b64encode(data).rstrip(b'=')
    now = int(time.time())
    claims = {'iss': admission['api_key'], 'nbf': now - 5, 'exp': now + 30, 'video': {'roomList': True}}
    unsigned = b64(b'{"alg":"HS256","typ":"JWT"}') + b'.' + b64(json.dumps(claims).encode())
    token = unsigned + b'.' + b64(hmac.digest(admission['api_secret'].encode(), unsigned, 'sha256'))
    try:
        with http.client.HTTPConnection('127.0.0.1', 7880, timeout=3) as conn:
            result = post(conn, '/twirp/livekit.RoomService/ListRooms', token.decode(), {})
        rooms = result.get('rooms', [])
        if set(result) - {'rooms'} or not isinstance(rooms, list):
            raise ValueError('unexpected LiveKit response')
    except (OSError, ValueError, http.client.HTTPException, RecursionError):
        raise Refused('Local LiveKit room list could not be verified; no room was closed.') from None
    return rooms


def school_status(site, secret, worker):
    try:
        route = site.path + '/plugins/school-plugins.php?plugin=school-classroom&route=recording'
        with http.client.HTTPSConnection(site.hostname, timeout=6) as conn:
            result = post(conn, route, secret, {'action':'maintenance','id':None,'protocol':2,'worker':worker})
        if result.get('ok') is not True or result.get('release') != VERSION or not all(
                type(result.get(field)) is bool for field in ('idle','worker_ok','native_ready')):
            raise ValueError('unexpected school response')
        return result
    except (OSError, ValueError, http.client.HTTPException, RecursionError):
        raise Refused(f'Cannot verify the school queue. Activate classroom {VERSION} with unchanged recording settings first.') from None


def unexpected_spool_entry(path):
    # Only a relative path, escaped even for control characters; never read the file.
    return f'Unexpected Recorder spool entry {ascii(str(path.relative_to(SPOOL)))}; nothing was deleted.'


def check_font_cache(path, uid):
    # The stock service user's HOME is the 0700 spool. Chromium/fontconfig can
    # create 0755 cache directories below it even with the service's 0077 umask.
    # Recognize only that layout, with no symlinks or writable-by-others entries.
    directory(path, uid=uid)
    for cache in path.iterdir():
        require(cache.name == 'fontconfig', unexpected_spool_entry(cache))
        directory(cache, uid=uid)
        for item in cache.iterdir():
            require(item.name == 'CACHEDIR.TAG' or FONT_CACHE_FILE.fullmatch(item.name), unexpected_spool_entry(item))
            info = item.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == uid and info.st_nlink == 1
                    and not info.st_mode & 0o022, unexpected_spool_entry(item))


def check_spool(uid):
    directory(SPOOL, uid=uid, private=True)
    worker = private_json(SPOOL / 'worker.json', uid=uid).get('id')
    require(isinstance(worker, str) and HEX.fullmatch(worker), 'Invalid existing Recorder identity.')
    for path in SPOOL.iterdir():
        if path.name in ('.lock', 'worker.json'):
            continue
        if path.name == '.cache':
            check_font_cache(path, uid)
            continue
        require(HEX.fullmatch(path.name), unexpected_spool_entry(path))
        directory(path, uid=uid, private=True)
        require(not any(exists(path / f) for f in ('receipt.json', '.capturing')), 'A recording or receipt is still pending.')
        terminal = False
        for name in ('.retained', '.cleaned'):
            if exists(path / name):
                with regular(path / name, uid=uid, forbidden=0o077):
                    terminal = True
        require(terminal, 'A recording, render or delivery is pending in the local spool.')
    return worker


def guard(config, uid, worker):
    site, secret, admission = config
    require(check_spool(uid) == worker, 'Recorder worker identity changed.')
    status = school_status(site, secret, worker)
    require(status['idle'] and status['worker_ok'], 'The school has an open bell, a pending job or a different active recorder.')
    require(not livekit_rooms(admission), 'LiveKit has open rooms. Finish all classes, including test rooms.')
    return status


def unpack(source, payload):
    zip_path = payload.parent / 'checked.zip'
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as origin, zip_path.open('xb') as out:
        info = os.fstat(origin.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_ZIP, 'Expected the local Recorder ZIP file.')
        left = MAX_ZIP
        while chunk := origin.read(1024**2):
            left -= len(chunk)
            require(left >= 0, 'Recorder ZIP exceeds the release size limit.')
            out.write(chunk)
    require(sha(zip_path) == PAYLOAD_SHA256, 'Recorder ZIP SHA-256 does not match the reviewed payload.')
    with ZipFile(zip_path) as archive:
        entries = archive.infolist()
        require({n.filename for n in entries} == PROGRAM | UNITS and len(entries) == len(PROGRAM | UNITS)
                and sum(n.file_size for n in entries) <= MAX_ZIP, 'Unexpected Recorder ZIP layout.')
        for name in sorted(PROGRAM | UNITS):
            path = payload / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as item, path.open('xb') as out:
                shutil.copyfileobj(item, out)
            path.chmod(0o755 if name == BINARY else 0o644)


def install_unit(src, dest):
    require(not exists(dest), f'Unexpected pre-existing unit: {dest.name}')
    with tempfile.NamedTemporaryFile(dir=dest.parent, prefix='.recorder-', delete=False) as tmp:
        pending = Path(tmp.name)
        try:
            with src.open('rb') as stream:
                shutil.copyfileobj(stream, tmp)
            os.fchmod(tmp.fileno(), 0o644)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.link(pending, dest)
        finally:
            pending.unlink(missing_ok=True)


def test_renderer(uid):
    directory(RUNTIME, uid=uid, private=True)
    endpoint = RUNTIME / 'render.sock'
    info = endpoint.lstat()
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o077, 'Renderer socket not private.')
    require(properties(POSTPROCESS, 'SubState').get('SubState') == 'running', 'Renderer unit did not start.')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as con:
            con.settimeout(21)
            con.connect(str(endpoint))
            con.sendall(b'{"probe":"self-test"}\n')
            with con.makefile('rb') as stream:
                result = json.loads(stream.readline(512))
        if result != {'result':'baci-render/1 tested'}:
            raise ValueError('unexpected renderer handshake')
    except (OSError, ValueError, RecursionError):
        raise Refused('Offline renderer did not pass the sandboxed Chromium/FFmpeg self-test.') from None


def running_invocation(unit):
    props = properties(unit, 'SubState', 'InvocationID')
    invocation = props.get('InvocationID', '')
    return invocation if props.get('SubState') == 'running' and HEX.fullmatch(invocation) and int(invocation, 16) else None


def wait_ready(marker, check=None):
    # Outlive the site's 18-second heartbeat window before calling a new worker ready.
    stable_for = 20 if check is not None else 6
    deadline, stable, seen = time.monotonic() + 45, 0, None
    while time.monotonic() < deadline:
        current = running_invocation(RECORDER)
        if current != seen:
            seen, stable = current, 0
        if current and not stable:
            logs = sys_command('journalctl', '--no-pager', '-n', '36', '-o', 'cat', '_SYSTEMD_INVOCATION_ID=' + current, read=True)
            if marker in logs.splitlines():
                stable = time.monotonic()
        if stable and time.monotonic() - stable >= stable_for and (check is None or check()):
            return
        time.sleep(1)
    raise Refused('Recorder did not remain ready with the expected version and school heartbeat.')


def apply(payload, release, version, drops, config, uid, worker):
    new_paths, created_dirs = [], []
    worker_started = release_installed = False
    try:
        sys_command('systemctl', 'stop', RECORDER)
        with regular(SPOOL / '.lock', uid=uid, forbidden=0o077) as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused('Another recorder still holds the spool lock.') from None
            guard(config, uid, worker)  # Check again after the old service has stopped.
            for folder, already in ((release.parent, exists(release.parent)), (SYSTEMD / (RECORDER + '.d'), drops[0])):
                if not already:
                    folder.mkdir(mode=0o755)
                    created_dirs.append(folder)
                    folder.chmod(0o755)
            with tempfile.TemporaryDirectory(prefix='.staged-', dir=release.parent) as temp:
                stage = Path(temp) / '0.4.23'
                stage.mkdir(mode=0o755)
                stage.chmod(0o755)
                for name in sorted(PROGRAM):
                    dest = stage / name
                    if dest.parent != stage and not dest.parent.exists():
                        dest.parent.mkdir(mode=0o755)
                        dest.parent.chmod(0o755)
                    shutil.copy2(payload / name, dest)
                require(not exists(release), 'The release path appeared during staging.')
                stage.rename(release)
                release_installed = True
            for name in (DROPIN + CREDENTIAL, DROPIN + NATIVE, POSTPROCESS):
                if name.endswith(CREDENTIAL) and drops[1]:
                    continue
                path = SYSTEMD / name
                install_unit(payload / name, path)
                new_paths.append(path)
            sys_command('systemctl', 'daemon-reload')
            guard(config, uid, worker)
            sys_command('systemctl', 'start', POSTPROCESS)
            test_renderer(uid)
            guard(config, uid, worker)
        worker_started = True
        sys_command('systemctl', 'start', RECORDER)
        wait_ready(f'recorder: native live capture available ({VERSION}); film renderer starts only off peak',
                   lambda: bool(running_invocation(POSTPROCESS)) and school_status(*config[:2], worker)['native_ready'])
    except (OSError, Refused, KeyboardInterrupt) as error:
        try:
            if worker_started:
                # Do not switch the worker's source format if someone opened a new bell mid-update.
                guard(config, uid, worker)
            if new_paths:
                sys_command('systemctl', 'stop', RECORDER)
                if SYSTEMD / POSTPROCESS in new_paths:
                    sys_command('systemctl', 'stop', POSTPROCESS)
                for path in reversed(new_paths):
                    path.unlink()
                sys_command('systemctl', 'daemon-reload')
            if release_installed:
                shutil.rmtree(release)
            for folder in reversed(created_dirs):
                folder.rmdir()
            sys_command('systemctl', 'start', RECORDER)
            label = 'compositor' if version == '0.4.21' else 'deferred capture'
            wait_ready(f'recorder: {label} and encoder available ({version})')
        except (OSError, Refused, KeyboardInterrupt):
            raise Refused('Update failed; automatic rollback could not be confirmed. Old code remains in place. '
                          'Do not delete recordings. Review both Recorder units and the school queue.') from None
        reason = str(error) if isinstance(error, Refused) else type(error).__name__
        raise Refused(f'{reason} Previous Recorder {version} restarted. Check matching site/Recorder versions before recording.') from None


def upgrade(args):
    require(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'The reviewed package requires Linux x86-64.')
    require(CGROUP.is_file() and {'cpu','memory'} <= set(CGROUP.read_text().split()), 'Expected systemd with cgroup v2 CPU/memory controllers.')
    uid = pwd.getpwnam('school-recorder').pw_uid
    require(uid > 0, 'Expected the unprivileged school-recorder user.')
    directory(ROOT)
    directory(ROOT / 'fonts')
    directory(SYSTEMD)
    release = ROOT / 'releases' / VERSION
    if exists(release.parent):
        directory(release.parent)
    require(not exists(release), f'Release path {release} already exists; no files were replaced.')
    fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with tempfile.TemporaryDirectory(prefix='baci-recorder-check-') as temp:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused('Another Recorder upgrade is running.') from None
            payload = Path(temp) / 'new'
            unpack(args.archive, payload)
            version = stock_version()
            drops = check_units(payload)
            config, worker = read_config(), check_spool(uid)
            guard(config, uid, worker)
            print(f'Verified stock Recorder {version}, current school, empty LiveKit, no pending recordings and reviewed local ZIP.', flush=True)
            if args.apply:
                apply(payload, release, version, drops, config, uid, worker)
                print(f'Recorder {VERSION} is ready (native receiver, offline renderer and school heartbeat).')
            else:
                print('Check only; no service or installed file was changed.')
    finally:
        os.close(fd)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check-only', action='store_true')
    mode.add_argument('--apply', action='store_true')
    cli.add_argument('archive', type=Path, help='local, SHA-256-pinned Recorder ZIP')
    args = cli.parse_args()
    if os.geteuid() != 0:
        cli.error('Run this verified script as root on the Recorder VPS.')
    os.umask(0o077)

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        upgrade(args)
    except Refused as error:
        print(f'Recorder update refused: {error}', file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, BadZipFile, KeyboardInterrupt) as error:
        print(f'Recorder update could not run ({type(error).__name__}); verify paths and systemd locally.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
