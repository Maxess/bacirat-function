#!/usr/bin/env python3
"""Guarded renderer-only repair for a running stock Baci Recorder 0.4.23."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import sys
import tempfile
import time
from types import ModuleType
from zipfile import BadZipFile, ZipFile

PATCH_SHA256 = '07d51104f5fa7bbfd90d6329931fa0e9d03967b9c8fc617c8fe08f10b4d38cfd'
PATCH_DIR = '0.4.23-renderer-r1'
DROPIN = '70-renderer-r1.conf'
SFU = 'school-classroom-sfu.service'
FIXED = ('movie.py', 'postprocess.py')
MAX_PATCH = 128 * 1024


def read_patch(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as fp:
        info = os.fstat(fp.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_PATCH:
            raise ValueError('Expected a small local renderer repair ZIP.')
        raw = fp.read(MAX_PATCH + 1)
    if hashlib.sha256(raw).hexdigest() != PATCH_SHA256:
        raise ValueError('Renderer repair ZIP SHA-256 does not match the reviewed payload.')
    with ZipFile(BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) != 4 or {item.filename for item in entries} != {*FIXED, 'inventory.json', 'maintenance.py'} \
                or sum(item.file_size for item in entries) > MAX_PATCH:
            raise ValueError('Unexpected layout in renderer repair ZIP.')
        # Load the reviewed 0.4.23 maintenance helpers only after the whole ZIP
        # matches its pin. Never import from the spool, cwd, or installed code.
        up = ModuleType('_baci_pinned_maintenance')
        exec(compile(archive.read('maintenance.py'), '<pinned maintenance>', 'exec'), up.__dict__)
        inventory = json.loads(archive.read('inventory.json'))
        up.require(set(inventory) == up.PROGRAM | up.UNITS, 'Invalid stock Recorder inventory.')
        return up, inventory, {name: archive.read(name) for name in FIXED}


@contextmanager
def installation_lock(up):
    up.directory(up.ROOT)
    fd = os.open(up.ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise up.Refused('Another Recorder maintenance process is running.') from None
        yield
    finally:
        os.close(fd)


class Repair:
    def __init__(self, up, inventory, files):
        self.up, self.inventory, self.files = up, inventory, files
        self.base = up.ROOT / 'releases' / up.VERSION
        self.patch = self.base.with_name(PATCH_DIR)
        self.drop = up.SYSTEMD / (up.POSTPROCESS + '.d') / DROPIN
        self.uid = up.pwd.getpwnam('school-recorder').pw_uid
        up.require(self.uid > 0, 'Expected the unprivileged school-recorder account.')

    def loaded(self, fixed=False):
        up = self.up
        for unit, drops in ((up.RECORDER, [up.SYSTEMD / up.DROPIN / name for name in (up.CREDENTIAL, up.NATIVE)]),
                            (up.POSTPROCESS, [self.drop] if fixed else [])):
            props = up.properties(unit, 'FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'SubState')
            up.require(props == {'FragmentPath': str(up.SYSTEMD / unit), 'DropInPaths': ' '.join(map(str, drops)),
                                 'NeedDaemonReload': 'no', 'SubState': 'running'},
                       f'Expected the running and loaded Recorder unit: {unit}.')

    def stock(self):
        up = self.up
        up.require(up.platform.system() == 'Linux' and up.platform.machine() == 'x86_64', 'Expected Linux x86-64.')
        up.require(up.CGROUP.is_file() and {'cpu', 'memory'} <= set(up.CGROUP.read_text().split()),
                   'Expected systemd with cgroup v2 CPU/memory controllers.')
        for directory in (self.base.parent, self.base, self.base / 'fonts', up.SYSTEMD, up.SYSTEMD / up.DROPIN):
            up.directory(directory)
        up.require(not up.exists(self.patch) and not up.exists(self.drop.parent),
                   'Renderer repair or a custom drop-in already exists. Do not apply again.')
        up.require({p.name for p in self.base.iterdir()} == {n.split('/')[0] for n in up.PROGRAM}
                   and {p.name for p in (self.base / 'fonts').iterdir()} == {n[6:] for n in up.PROGRAM if n.startswith('fonts/')},
                   'Expected the stock 0.4.23 program layout (no extra modules or caches).')
        up.require({p.name for p in (up.SYSTEMD / up.DROPIN).iterdir()} == {up.CREDENTIAL, up.NATIVE},
                   'Expected only the two stock Recorder drop-ins.')
        for root, names in ((self.base, up.PROGRAM), (up.SYSTEMD, up.UNITS)):
            for name in sorted(names):
                up.require(up.sha(root / name) == self.inventory[name], f'Installed 0.4.23 bytes differ: {name}.')
        self.loaded()
        self.keeper = {unit: up.running_invocation(unit) for unit in (up.RECORDER, SFU)}
        up.require(all(self.keeper.values()), 'Expected the Recorder and local LiveKit services to be running.')
        self.config = up.read_config()
        self.worker = up.check_spool(self.uid)
        self.idle(ready=True)
        self.probe('ready', 'baci-render/1', 2)

    def idle(self, ready=False):
        up = self.up
        state = up.guard(self.config, self.uid, self.worker)
        if ready:
            up.require(state['native_ready'], 'The school did not confirm a healthy native Recorder heartbeat.')

    def same_capture(self):
        up = self.up
        up.require(all(up.running_invocation(unit) == prior for unit, prior in self.keeper.items()),
                   'Recorder or LiveKit restarted independently during maintenance.')

    def probe(self, command, answer, timeout):
        up = self.up
        up.directory(up.RUNTIME, uid=self.uid, private=True)
        endpoint = up.RUNTIME / 'render.sock'
        info = endpoint.lstat()
        up.require(stat.S_ISSOCK(info.st_mode) and info.st_uid == self.uid and not info.st_mode & 0o077,
                   'Renderer socket is not private.')
        pid = up.properties(up.POSTPROCESS, 'MainPID').get('MainPID', '')
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(timeout)
                client.connect(str(endpoint))
                peer, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
                if uid != self.uid or str(peer) != pid:
                    raise ValueError('wrong renderer peer')
                client.sendall(json.dumps({'probe': command}).encode('ascii') + b'\n')
                with client.makefile('rb') as fp:
                    result = json.loads(fp.readline(512))
            if result != {'result': answer}:
                raise ValueError('unexpected probe result')
        except (OSError, ValueError, RecursionError):
            reason = 'The repaired renderer did not pass its sandboxed MP4 self-test.' if command != 'ready' \
                     else 'The renderer readiness socket did not respond as expected.'
            raise up.Refused(reason) from None

    def stage(self, directory):
        up = self.up
        stage = directory / PATCH_DIR
        stage.mkdir()
        stage.chmod(0o755)
        for name, data in self.files.items():
            with (stage / name).open('xb') as fp:
                fp.write(data)
                os.fchmod(fp.fileno(), 0o644)
                fp.flush()
                os.fsync(fp.fileno())
        unit = directory / DROPIN
        unit.write_text('[Service]\n' + ''.join(f'BindReadOnlyPaths={self.patch / name}:{self.base / name}\n' for name in FIXED))
        up.require(not up.exists(self.patch), 'Renderer repair path appeared during staging.')
        stage.rename(self.patch)
        return unit

    def apply(self):
        up = self.up
        switched = drop_installed = patch_installed = drop_created = start_attempted = False
        try:
            with tempfile.TemporaryDirectory(prefix='.renderer-r1-', dir=self.base.parent) as temp:
                unit = self.stage(Path(temp))
                patch_installed = True
                self.same_capture()
                self.idle(ready=True)
                old_renderer = up.running_invocation(up.POSTPROCESS)
                up.require(old_renderer, 'The original renderer is no longer running.')
                switched = True
                up.sys_command('systemctl', 'stop', up.POSTPROCESS)
                self.idle()  # Recheck after the old renderer has stopped, before installing the drop-in.
                self.drop.parent.mkdir(mode=0o755)
                drop_created = True
                self.drop.parent.chmod(0o755)
                up.install_unit(unit, self.drop)
                drop_installed = True
                up.sys_command('systemctl', 'daemon-reload')
                self.idle()
                start_attempted = True
                up.sys_command('systemctl', 'start', up.POSTPROCESS)
                self.loaded(fixed=True)
                current = up.running_invocation(up.POSTPROCESS)
                up.require(current and current != old_renderer, 'The repaired renderer did not start as a new invocation.')
                self.idle()
                self.probe('self-test-mp4', 'baci-render/1 tested mp4', 21)
                # The original capture process is not restarted. Outlive the last
                # pre-repair heartbeat before accepting the school's ready status.
                time.sleep(19)
                self.idle(ready=True)
                self.same_capture()
                up.require(up.running_invocation(up.POSTPROCESS) == current, 'The repaired renderer restarted during the check.')
                return
        except (Exception, KeyboardInterrupt) as error:
            try:
                if start_attempted:
                    # Do not interrupt a newly queued film to roll back. If we
                    # cannot confirm idle, leave the installed pair for inspection.
                    if up.properties(up.POSTPROCESS, 'ActiveState').get('ActiveState') not in ('failed', 'inactive'):
                        self.idle()
                    up.sys_command('systemctl', 'stop', up.POSTPROCESS)
                if drop_installed:
                    self.drop.unlink()
                if drop_created:
                    self.drop.parent.rmdir()
                if drop_installed:
                    up.sys_command('systemctl', 'daemon-reload')
                if switched:
                    up.sys_command('systemctl', 'start', up.POSTPROCESS)
                    self.loaded()
                    self.probe('ready', 'baci-render/1', 2)
                if patch_installed:
                    for name in FIXED:
                        (self.patch / name).unlink()
                    self.patch.rmdir()
            except (Exception, KeyboardInterrupt):
                raise up.Refused('Renderer rollback could not be confirmed. Do not reapply or delete recordings; check the renderer service locally.') from None
            reason = str(error) if isinstance(error, up.Refused) else type(error).__name__
            detail = 'Original renderer is running again.' if switched else 'No service was stopped.'
            raise up.Refused(f'{reason} {detail} No recording files were changed by the repair.') from None


def run(args):
    up, inventory, files = read_patch(args.archive)
    try:
        with installation_lock(up):
            repair = Repair(up, inventory, files)
            repair.stock()
            print('Verified stock Recorder 0.4.23, native heartbeat, no open rooms or pending recordings, and the reviewed renderer ZIP.', flush=True)
            if args.apply:
                print('Updating the renderer only; checking a synthetic MP4 and the school heartbeat.', flush=True)
                repair.apply()
                print('Renderer 0.4.23-r1 is ready (sandboxed H.264/AAC MP4 and school heartbeat verified).')
            else:
                print('Check only; no service or installed file was changed.')
    except up.Refused as error:
        raise ValueError(str(error)) from None


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check-only', action='store_true')
    mode.add_argument('--apply', action='store_true')
    cli.add_argument('archive', type=Path, help='local, SHA-256-pinned renderer repair ZIP')
    args = cli.parse_args()
    if os.geteuid() != 0:
        cli.error('Run this verified script as root on the Recorder VPS.')
    os.umask(0o077)

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        run(args)
    except ValueError as error:
        print(f'Renderer repair refused: {error}', file=sys.stderr)
        return 1
    except (OSError, KeyError, BadZipFile, KeyboardInterrupt) as error:
        print(f'Renderer repair could not run ({type(error).__name__}); check paths and systemd locally.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
