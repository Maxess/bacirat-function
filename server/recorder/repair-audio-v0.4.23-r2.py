#!/usr/bin/python3
"""Apply or revert the audio replay repair on a managed, idle Baci VPS 1.3.2."""
import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import socket
import stat
import struct
import sys
import tempfile
from types import ModuleType

APP = Path('/opt/school-classroom')
HELPERS = {
    'vps_media.py': '6dd018f843995131fc69d6c434d900b4f10be1a84ac547c97d9f1833bd1c6993',
    'baci-vps.py': '6455e2f0187f014d9dd5456a3165e8f05f632ef3dceae23283a7a776b7824f1a',
}
AFTER_SHA = '54cb9ab61118e60412357b0555d72325fb0cd1b7c8a2136c70f17ba16de11a1c'
STOCK_SHA = '6fb4a82aefdee96b1c2f52240baa580625caf116bfab79f5a2330a687101aa7f'
RENDERER_ASSETS = {
    'archive.py': 'af6165d251a3df09e377f767ff9df205d4b51992635ef8f19280c864d02b71e2',
    'movie.py': 'a91aded20c3b7eaee6096a0514b2f6efa69e293f0427ae03395311ae2ce29802',
    'postprocess.py': '6bedc7bd6636d360c47529dc02212ce85128c6eb124854f8d8c27e61ef0a4180',
}
COMPACT_SHA = 'e224bd2088dab6a22b0db71095272a8e563b9d368c221772db8d389af64061dd'
UNIT_SHA = 'c5f0c7ca7dafb92837f6cb1e2721cc177377ee34ce3d192d84a13029185ff2ea'
# Replaced only by the deterministic build. Never read a patch from the cwd.
STOCK = b'@@STOCK_REPLAY@@'
FIXED = b'@@FIXED_REPLAY@@'
HEX = re.compile(r'[a-f0-9]{32}')


class Refused(Exception):
    pass


def need(condition, message):
    if not condition:
        raise Refused(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def directory(path, uid=0, *, private=False):
    st = path.lstat()
    need(stat.S_ISDIR(st.st_mode) and st.st_uid == uid and path.resolve() == path
         and not st.st_mode & (0o077 if private else 0o022), f'Unexpected directory: {path.name}.')


def file(path, *, uid=0, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as fp:
        st = os.fstat(fp.fileno())
        need(stat.S_ISREG(st.st_mode) and st.st_uid == uid and st.st_nlink == 1
             and not st.st_mode & (0o077 if private else 0o022) and 0 < st.st_size <= 128 * 1024,
             f'Unexpected installed file: {path.name}.')
        data = fp.read(128 * 1024 + 1)
        need(len(data) == st.st_size, f'File changed while reading: {path.name}.')
    return data, st


def verified_module(name, filename):
    path = APP / filename
    code, _ = file(path)
    need(digest(code) == HELPERS[filename], f'Expected the published VPS 1.3.2 bytes: {filename}.')
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(code, str(path), 'exec'), module.__dict__)
    return module


def helpers():
    directory(APP.parent)
    directory(APP)
    previous, saved_path = sys.modules.get('vps_media'), sys.path[:]
    sys.modules['vps_media'] = verified_module('vps_media', 'vps_media.py')
    try:
        return verified_module('_baci_vps_audio_repair', 'baci-vps.py')
    finally:
        sys.path[:] = saved_path
        if previous is None:
            sys.modules.pop('vps_media', None)
        else:
            sys.modules['vps_media'] = previous


def reviewed_payloads():
    stock, fixed = (base64.b64decode(payload, validate=True) for payload in (STOCK, FIXED))
    need(digest(stock) == STOCK_SHA and digest(fixed) == AFTER_SHA, 'Repair payload checksum mismatch.')
    return stock, fixed


@contextmanager
def installation_lock(path):
    directory(path)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('Another Recorder maintenance process is running.') from None
        yield
    finally:
        os.close(fd)


def replace(path, original, wanted):
    # The program directory is root-owned; each reader sees one complete version.
    with tempfile.TemporaryDirectory(prefix='.audio-repair-', dir=path.parent) as stage:
        pending = Path(stage) / path.name
        with pending.open('xb') as fp:
            os.fchmod(fp.fileno(), 0o644)
            fp.write(wanted)
            fp.flush()
            os.fsync(fp.fileno())
        need(file(path)[0] == original, 'Installed replay changed concurrently. Nothing was overwritten.')
        os.replace(pending, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Repair:
    def __init__(self, vps, stock, fixed):
        self.vps, self.stock, self.fixed = vps, stock, fixed
        self.replay = vps.REC_PROGRAM / 'replay.js'
        for path in (vps.CFG, vps.REC_PROGRAM.parent, vps.REC_PROGRAM):
            directory(path)
        self.config = json.loads(file(vps.STATE, private=True)[0])
        need(self.config.get('version') == '1.3.2' and self.config.get('profile') in ('compact', 'full'),
             'This repair is only for managed VPS 1.3.2 with recording (compact or full).')
        self.compact = self.config['profile'] == 'compact'
        self.keys = json.loads(file(vps.PAIR, private=True)[0])
        need(self.keys.get('site') == self.config['school'], 'Installed school pairing does not match the current site.')
        self.uid = pwd.getpwnam('school-recorder').pw_uid
        need(self.uid > 0, 'Expected unprivileged Recorder account.')
        self.target = self.process()
        self.keepers = self.invocations()
        self.local_worker = self.worker()

    def worker(self):
        spool = self.vps.SPOOL
        directory(spool, self.uid, private=True)
        need(all(entry.name in ('worker.json', '.lock', '.cache') for entry in spool.iterdir()),
             'Local Recorder spool still has jobs. Wait for it to settle; do not delete any recordings.')
        identity = json.loads(file(spool / 'worker.json', uid=self.uid, private=True)[0]).get('id')
        need(isinstance(identity, str) and HEX.fullmatch(identity), 'Expected the current Recorder worker identity.')
        return identity

    def idle(self):
        vps = self.vps
        need(self.worker() == self.local_worker, 'Local Recorder identity changed.')
        try:
            rooms = vps.media.local_rooms(vps.media.pilot_deployment())
            need(rooms == [], 'LiveKit still has open rooms. Close all classes and practice rooms first.')
            status = vps.recorder_status(self.config, self.keys, self.local_worker)
        except (ValueError, OSError):
            raise Refused('Could not confirm the idle school and local LiveKit. No job will be interrupted.') from None
        need(all(status.get(name) is True for name in ('idle', 'worker_ok', 'native_ready')),
             'School still has a room or a pending job, or the Recorder is not ready.')

    def process(self):
        vps = self.vps
        props = vps.run('systemctl', 'show', '--no-pager',
                        '--property=FragmentPath,DropInPaths,NeedDaemonReload,SubState,User,MainPID,InvocationID',
                        vps.RENDERER, output=True, timeout=8).decode()
        state = dict(row.split('=', 1) for row in props.splitlines() if '=' in row)
        unit = vps.SYSTEMD / vps.RENDERER
        paths = [unit.with_name(unit.name + '.d') / '70-renderer-r1.conf']
        if self.compact:
            paths.append(paths[0].with_name('80-compact.conf'))
        pid, inv = state.get('MainPID', ''), state.get('InvocationID', '')
        need(state.get('FragmentPath') == str(unit) and set(state.get('DropInPaths', '').split()) == set(map(str, paths))
             and state.get('NeedDaemonReload') == 'no' and state.get('SubState') == 'running'
             and state.get('User') == 'school-recorder' and re.fullmatch(r'[1-9][0-9]{0,9}', pid)
             and int(pid) > 1 and HEX.fullmatch(inv) and int(inv, 16),
             'Expected a running managed r1 renderer without custom systemd overrides.')
        return int(pid), inv

    def invocations(self):
        vps = self.vps
        result = tuple(vps.info('-p', 'InvocationID', unit) for unit in (*vps.LIVE_UNITS, vps.RECORDER))
        need(all(HEX.fullmatch(value) and int(value, 16) for value in result), 'Expected running LiveKit and Recorder services.')
        return result

    def proc_file(self, name):
        # Intentional use of /proc/PID/root from the validated live unit. Refuse
        # an overlaid replay file instead of changing a path the renderer cannot see.
        return file(Path('/proc') / str(self.target[0]) / 'root' / self.replay.with_name(name).relative_to('/'))

    def visible(self, expected):
        need(self.process() == self.target and self.invocations() == self.keepers,
             'A service changed independently during the repair.')
        raw, host = file(self.replay)
        guest, view = self.proc_file('replay.js')
        need(raw == guest == expected and (host.st_dev, host.st_ino) == (view.st_dev, view.st_ino),
             'The running renderer does not see these replay bytes. Nothing will be mounted or restarted.')
        vps = self.vps
        unit = vps.SYSTEMD / vps.RENDERER
        need(digest(file(unit)[0]) == UNIT_SHA, 'Expected the published renderer unit.')
        r1 = self.replay.parent.with_name('0.4.23-renderer-r1')
        drop = unit.with_name(unit.name + '.d')
        binds = '[Service]\n' + ''.join(f'BindReadOnlyPaths={r1 / name}:{self.replay.with_name(name)}\n'
                                       for name in ('movie.py', 'postprocess.py'))
        need(file(drop / '70-renderer-r1.conf')[0] == binds.encode(), 'Expected the published r1 bind mounts.')
        assets = RENDERER_ASSETS | ({'compact-renderer.py': COMPACT_SHA} if self.compact else {})
        need(all(digest(self.proc_file(name)[0]) == pin for name, pin in assets.items()),
             'Expected the published r1 renderer (reloads replay.js for each new film).')

    def probe(self, test=False):
        path = self.vps.RENDER_SOCKET
        directory(path.parent, self.uid, private=True)
        st = path.lstat()
        need(stat.S_ISSOCK(st.st_mode) and st.st_uid == self.uid and not st.st_mode & 0o077,
             'Expected the renderer private socket.')
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(22 if test else 3)
                conn.connect(str(path))
                pid, uid, _ = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
                need(pid == self.target[0] and uid == self.uid, 'Renderer socket does not belong to the running unit.')
                action = 'self-test-mp4' if test else 'ready'
                conn.sendall((json.dumps({'probe': action}) + '\n').encode('ascii'))
                with conn.makefile('rb') as fp:
                    result = json.loads(fp.readline(512))
        except (ValueError, OSError):
            raise Refused('Could not complete the private renderer check.') from None
        answer = 'baci-render/1 tested mp4' if test else 'baci-render/1'
        need(result == {'result': answer}, 'Renderer failed the sandboxed MP4 check or is busy.')

    def change(self, before, after):
        self.idle()
        self.visible(before)
        try:
            replace(self.replay, before, after)
            self.visible(after)
            self.probe(test=True)
            self.idle()
            self.visible(after)
        except (Exception, KeyboardInterrupt) as error:
            try:
                actual, _ = file(self.replay)
                if actual == after:
                    replace(self.replay, after, before)
                else:
                    need(actual == before, 'Installed file changed independently.')
                restored, _ = file(self.replay)
                need(restored == before, 'Prior replay bytes could not be verified.')
            except (Exception, KeyboardInterrupt):
                raise Refused('Repair unconfirmed; automatic rollback could not be verified. No recording files were changed.') from None
            problem = str(error) if isinstance(error, Refused) else type(error).__name__
            raise Refused(f'{problem} Previous replay file restored or unchanged. No recording files were changed.') from None


def run(vps, mode, payloads):
    try:
        vps.system_preflight()
        with installation_lock(vps.REC):
            repair = Repair(vps, *payloads)
            before, _ = file(repair.replay)
            need(before in payloads, 'Installed replay.js is neither the reviewed original nor r2. Nothing was replaced.')
            repair.idle()
            repair.visible(before)
            repair.probe()
            current = 'r2' if before == repair.fixed else 'stock'
            print(f'audio_preflight=ok installed={current} rooms_and_queue=idle recorder=ready', flush=True)
            if mode == 'check-only':
                print('check_only=yes no_changes=yes')
                return
            after = repair.fixed if mode == 'apply' else repair.stock
            if before == after:
                print(f'audio_repair=ok installed={current} already_selected=yes no_changes=yes')
                return
            repair.change(before, after)
            print(f"audio_repair=ok installed={'r2' if mode == 'apply' else 'stock'} mp4_check=ok services=unchanged")
    except vps.Refused as error:
        raise Refused(str(error)) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    for mode in ('check-only', 'apply', 'rollback'):
        group.add_argument('--' + mode, dest='mode', action='store_const', const=mode)
    args = parser.parse_args()
    if os.geteuid() != 0 or not sys.flags.isolated:
        parser.error('Run the SHA-256 verified script with sudo /usr/bin/python3 -IB.')
    os.umask(0o077)
    sys.dont_write_bytecode = True

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        payloads = reviewed_payloads()
        run(helpers(), args.mode, payloads)
    except Refused as error:
        print(f'Audio repair refused: {error}', file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt) as error:
        print(f'Audio repair unconfirmed ({type(error).__name__}); no private data is shown.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
