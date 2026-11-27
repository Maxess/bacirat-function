#!/usr/bin/python3
"""Guarded User-Agent repair for an idle stock v1.3.1 VPS with Recorder 0.4.23."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import sys
import time
from types import ModuleType

APP = Path('/opt/school-classroom')
RUNTIME = Path('/run/school-classroom-recorder-credential/config.json')
PATCH_DIR = '0.4.23-school-http'
PATCH_DROPIN = '75-school-http.conf'
# Exact v1.3.1 installer helpers, and exact files from the frozen Recorder ZIP.
VPS_MEDIA = '6dd018f843995131fc69d6c434d900b4f10be1a84ac547c97d9f1833bd1c6993'
VPS_SETUP = '0f5340b2746820926ccc178fa37413c77e21edf0da6f330c61bd402c87b552a3'
STOCK_TRANSPORT = '1f621489fcb38901662d9c106f4e49c944ce37bbd7eb2845bd4732cadec3791d'
STOCK_ENTRY = 'c9d4c71c143a18658ad827d8d3b3a880fe17b6bdf25c18eb5422c9724f880392'
STOCK_UNIT = 'cd3543f13478fccddf934d9d15fc1fe79f6f863753c3d7ae27e555c540ec93e0'
STOCK_DROPS = {
    '10-recorder-credential.conf': 'd5476cfe080468d43c818a871cfb4f06fea77c6837bf06152d3f316cac06600a',
    '60-native-recorder.conf': '534c49ff73d126998889de67d82eb74ab6c314b710de84a72e564f98b1504116',
}
COMPACT_DROP = b'[Service]\nMemoryHigh=112M\nMemoryMax=160M\nMemorySwapMax=0\n'
IDENTITY = 'Baci-Classroom-Recorder/0.4.23'
HEX = re.compile(r'[a-f0-9]{32}')


class Refused(Exception):
    pass


def need(condition, message):
    if not condition:
        raise Refused(message)


def directory(path, *, uid=0, private=False):
    info = path.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and path.resolve() == path
         and not info.st_mode & (0o077 if private else 0o022), f'Unexpected directory: {path.name}.')


def reviewed(path, digest):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
             and not info.st_mode & 0o022 and 0 < info.st_size <= 128 * 1024,
             f'Unexpected installed file: {path.name}.')
        data = stream.read(128 * 1024 + 1)
    need(hashlib.sha256(data).hexdigest() == digest, f'Installed bytes differ: {path.name}.')
    return data


def module(name, path, data):
    loaded = ModuleType(name)
    loaded.__file__ = str(path)
    exec(compile(data, str(path), 'exec'), loaded.__dict__)
    return loaded


def vps_helpers():
    directory(APP)
    previous, saved_path = sys.modules.get('vps_media'), sys.path[:]
    sys.modules['vps_media'] = module('vps_media', APP / 'vps_media.py', reviewed(APP / 'vps_media.py', VPS_MEDIA))
    try:
        return module('_baci_verified_vps', APP / 'baci-vps.py', reviewed(APP / 'baci-vps.py', VPS_SETUP))
    finally:
        sys.path[:] = saved_path
        if previous is None:
            sys.modules.pop('vps_media', None)
        else:
            sys.modules['vps_media'] = previous


def fixed_transport(stock):
    need(hashlib.sha256(stock).hexdigest() == STOCK_TRANSPORT, 'Not the published Recorder transport.')
    old = b"        self.headers = {'Authorization': 'Bearer ' + secret}\n"
    new = ("        # Identify the machine client: some school hosts reject requests without User-Agent.\n"
           "        self.headers = {'Authorization': 'Bearer ' + secret, 'User-Agent': '" + IDENTITY + "'}\n").encode('ascii')
    need(stock.count(old) == 1, 'Unexpected transport layout.')
    return stock.replace(old, new, 1)


@contextmanager
def installation_lock(path):
    directory(path)
    fd = os.open(path, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('Another Recorder maintenance process is running.') from None
        yield
    finally:
        os.close(fd)


class Repair:
    def __init__(self, vps):
        self.vps = vps
        self.patch = vps.REC_PROGRAM.with_name(PATCH_DIR)
        self.target = vps.REC_PROGRAM / 'transport.py'
        self.unit_path = vps.SYSTEMD / vps.RECORDER
        self.drop = self.unit_path.with_name(vps.RECORDER + '.d') / PATCH_DROPIN
        self.payload = self.patch / self.target.name
        self.unit_text = f'[Service]\nBindReadOnlyPaths={self.payload}:{self.target}\n'

    def invocation(self, *, fixed=False):
        vps = self.vps
        text = vps.run('systemctl', 'show', '--no-pager',
                       '--property=FragmentPath,DropInPaths,NeedDaemonReload,SubState,User,InvocationID',
                       vps.RECORDER, output=True, timeout=7).decode()
        props = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        drops = {str(self.drop.parent / name) for name in self.drops}
        if fixed:
            drops.add(str(self.drop))
        current = props.get('InvocationID', '')
        need(props.get('FragmentPath') == str(self.unit_path) and props.get('SubState') == 'running'
             and props.get('NeedDaemonReload') == 'no' and props.get('User') == 'school-recorder'
             and set(props.get('DropInPaths', '').split()) == drops and HEX.fullmatch(current) and int(current, 16),
             'Expected the running, loaded stock Recorder and only the reviewed drop-ins.')
        return current

    def spool(self):
        vps = self.vps
        directory(vps.SPOOL, uid=self.uid, private=True)
        for path in vps.SPOOL.iterdir():
            if path.name == '.cache':
                directory(path, uid=self.uid)
            else:
                info = path.lstat()
                need(path.name in ('worker.json', '.lock') and stat.S_ISREG(info.st_mode)
                     and info.st_uid == self.uid and not info.st_mode & 0o077,
                     'Local Recorder spool is not empty of jobs. No recording may be deleted for this repair.')
        worker = vps.json_file(vps.SPOOL / 'worker.json').get('id')
        need(isinstance(worker, str) and HEX.fullmatch(worker), 'Invalid existing worker identity.')
        return worker

    def status(self):
        try:
            result = self.school('maintenance')
        except self.transport.ProtocolError as error:
            raise Refused(f'The school did not accept the identified Recorder request (HTTP {error.status}).') from None
        need(result.get('release') == '0.4.23' and all(type(result.get(key)) is bool
             for key in ('idle', 'worker_ok', 'native_ready')), 'Unexpected school maintenance response.')
        return result

    def idle(self, *, runtime=True):
        vps = self.vps
        need(self.spool() == self.worker, 'Local worker identity changed.')
        need(vps.json_file(vps.REC_CFG) == self.expected, 'Recorder credential on disk no longer matches.')
        if runtime:
            directory(RUNTIME.parent, uid=self.uid, private=True)
            need(vps.json_file(RUNTIME) == self.expected, 'Running Recorder credential does not match.')
        result = self.status()
        need(result['idle'] and result['worker_ok'], 'School has an open lesson, a pending recording, or a different active Recorder.')
        rooms = vps.media.local_rooms(vps.media.pilot_deployment())
        need(isinstance(rooms, list) and not rooms, 'LiveKit has open rooms or its room list is unavailable.')
        return result

    def check(self):
        vps = self.vps
        for path in (vps.CFG, vps.SYSTEMD, self.drop.parent, self.target.parent.parent, self.target.parent):
            directory(path)
        need(not os.path.lexists(self.patch) and not os.path.lexists(self.drop),
             'HTTP repair already present or partially installed. Do not reapply.')
        need(not any(os.path.lexists(path) for path in (self.target.with_suffix('.pyc'), self.target.parent / '__pycache__')),
             'Unexpected bytecode in the Recorder release directory.')
        self.config = vps.json_file(vps.STATE)
        need(self.config.get('version') == '1.3.1' and self.config.get('profile') in ('compact', 'full'),
             'This repair is for a managed v1.3.1 installation with recording.')
        self.drops = STOCK_DROPS.copy()
        if self.config['profile'] == 'compact':
            self.drops['80-compact.conf'] = hashlib.sha256(COMPACT_DROP).hexdigest()
        reviewed(self.unit_path, STOCK_UNIT)
        reviewed(self.target.parent / 'recording.py', STOCK_ENTRY)
        for name, digest in self.drops.items():
            reviewed(self.drop.parent / name, digest)
        self.original = self.invocation()
        self.media_invocations = self.live_invocations()
        need(all(HEX.fullmatch(value) and int(value, 16) for value in self.media_invocations),
             'Expected LiveKit, admission and HTTPS to be running.')
        vps.local_checks(self.config)
        self.uid = pwd.getpwnam('school-recorder').pw_uid
        need(self.uid > 0, 'Expected an unprivileged Recorder account.')
        self.worker = self.spool()
        pair = vps.json_file(vps.PAIR)
        need(pair.get('site') == self.config['school'] and isinstance(pair.get('recording_secret'), str)
             and re.fullmatch(r'[A-Za-z0-9_-]{32,128}', pair['recording_secret']), 'Invalid saved recording pairing.')
        self.expected = {'site': pair['site'], 'secret': pair['recording_secret']}
        stock = reviewed(self.target, STOCK_TRANSPORT)
        self.fixed = fixed_transport(stock)
        old_transport = module('_baci_stock_transport', self.target, stock)
        self.transport = module('_baci_fixed_transport', self.payload, self.fixed)
        self.school = self.transport.School(self.expected['site'], self.expected['secret'], self.worker)
        need(not self.idle()['native_ready'], 'School already confirms a fresh Recorder heartbeat. No repair needed now.')
        try:
            old_transport.School(self.expected['site'], self.expected['secret'], self.worker)('maintenance')
        except old_transport.ProtocolError as error:
            need(error.status == 403, 'The original HTTP request no longer fails with the expected 403.')
        else:
            raise Refused('The original request is accepted now. This repair was not applied.')

    def live_invocations(self):
        vps = self.vps
        return tuple(vps.info('-p', 'InvocationID', unit) for unit in vps.LIVE_UNITS)

    def apply(self):
        vps = self.vps
        staged = installed = restart_attempted = False
        try:
            self.patch.mkdir(mode=0o700)
            staged = True
            vps.write(self.payload, self.fixed)
            self.patch.chmod(0o755)
            vps.write(self.drop, self.unit_text)
            installed = True
            vps.run('systemctl', 'daemon-reload', timeout=15)
            need(self.invocation(fixed=True) == self.original and self.live_invocations() == self.media_invocations,
                 'A service restarted independently during maintenance.')
            self.idle()
            restart_attempted = True
            # The renderer has PartOf=Recorder; LiveKit, admission and Caddy do not.
            vps.run('systemctl', 'restart', vps.RECORDER, timeout=70)
            current = self.invocation(fixed=True)
            need(current != self.original, 'Recorder did not start a new invocation.')
            time.sleep(19)  # The school considers a previous heartbeat fresh for at most 18 seconds.
            need(self.status()['native_ready'] and self.invocation(fixed=True) == current,
                 'No verified fresh school heartbeat from the new Recorder.')
            vps.local_checks(self.config)
            need(self.live_invocations() == self.media_invocations, 'A live media service restarted independently.')
        except (Exception, KeyboardInterrupt) as error:
            try:
                if restart_attempted:
                    self.idle(runtime=False)  # Never interrupt a new job just to roll back.
                if installed:
                    reviewed(self.drop, hashlib.sha256(self.unit_text.encode()).hexdigest())
                    self.drop.unlink()
                    vps.run('systemctl', 'daemon-reload', timeout=15)
                if restart_attempted:
                    vps.run('systemctl', 'restart', vps.RECORDER, timeout=70)
                    self.invocation()
                if staged:
                    if self.payload.exists():
                        reviewed(self.payload, hashlib.sha256(self.fixed).hexdigest())
                        self.payload.unlink()
                    self.patch.rmdir()
            except (Exception, KeyboardInterrupt):
                raise Refused('Repair not verified and safe rollback could not be confirmed. Do not reapply or delete any files.') from None
            reason = str(error) if isinstance(error, (Refused, vps.Refused)) else type(error).__name__
            raise Refused(f'{reason} Original configuration restored; no recording files changed.') from None


def run(apply):
    vps = vps_helpers()
    try:
        vps.system_preflight()
        with installation_lock(vps.REC):
            repair = Repair(vps)
            repair.check()
            print('preflight=ok stock_recorder=0.4.23 http_403_reproduced=yes room_and_queue_idle=yes', flush=True)
            if apply:
                print('Restarting only Recorder and its dependent renderer; checking a fresh school heartbeat…', flush=True)
                repair.apply()
                print('repair=ok school_recorder=ready live_services=unchanged')
            else:
                print('check_only=yes no_changes=yes')
    except vps.Refused as error:
        raise Refused(str(error)) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check-only', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or not sys.flags.isolated:
        parser.error('Run the verified script with sudo /usr/bin/python3 -IB.')
    os.umask(0o077)
    sys.dont_write_bytecode = True

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        run(args.apply)
    except Refused as error:
        print(f'Repair refused: {error}', file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt) as error:
        print(f'Repair unconfirmed ({type(error).__name__}); no private data is shown.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
