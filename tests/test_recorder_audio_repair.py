"""Byte-reproducible r2 audio repair and read-only/atomic cutovers on private fixture files."""
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts'
REPAIR = ARTIFACTS / 'baci-recorder-audio-repair-v0.4.23-r2.py'
RECORDER = ARTIFACTS / 'baci-classroom-recorder-v0.4.23.zip'
R1 = ARTIFACTS / 'baci-classroom-renderer-v0.4.23-r1.zip'
spec = importlib.util.spec_from_file_location('audio_repair', REPAIR)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PackageTest(unittest.TestCase):
    def test_reviewed_replay_helpers_and_standalone_payload_are_reproducible(self):
        with tempfile.TemporaryDirectory() as temp, ZipFile(RECORDER) as stock, ZipFile(R1) as repair:
            subprocess.run([sys.executable, str(ROOT / 'scripts/build-managed-school.py'), '--audio-repair',
                            '--output-dir', temp], check=True, capture_output=True, timeout=12)
            self.assertEqual((Path(temp) / REPAIR.name).read_bytes(), REPAIR.read_bytes())
            before, after = installer.reviewed_payloads()
            self.assertEqual(before, stock.read('replay.js'))
            self.assertEqual(after, (ROOT / 'server/recorder/replay.js').read_bytes())
            self.assertNotEqual(before, after)
            self.assertEqual(installer.RENDERER_ASSETS, {name: installer.digest(raw) for name, raw in (
                ('archive.py', stock.read('archive.py')), ('movie.py', repair.read('movie.py')),
                ('postprocess.py', repair.read('postprocess.py')))})
            self.assertEqual(installer.UNIT_SHA, installer.digest(stock.read('school-classroom-postprocess.service')))
            self.assertEqual(installer.COMPACT_SHA, sha(ROOT / 'server/offline/compact-renderer.py'))
            self.assertEqual(installer.HELPERS, {'vps_media.py': sha(ROOT / 'server/install.py'),
                                                'baci-vps.py': sha(ROOT / 'server/offline/setup.py')})
            self.assertIn(sha(REPAIR), (ROOT / 'docs/repair-recorder-audio.fa.md').read_text())

    def test_payload_is_refused_if_modified(self):
        with patch.object(installer, 'FIXED', installer.STOCK):
            with self.assertRaisesRegex(installer.Refused, 'payload checksum mismatch'):
                installer.reviewed_payloads()


class CutoverTest(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(contextlib.ExitStack())
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stock, self.fixed = installer.reviewed_payloads()
        self.vps = SimpleNamespace(Refused=ValueError, CFG=self.root / 'etc', SPOOL=self.root / 'spool',
                                   REC=self.root / 'program', SYSTEMD=self.root / 'systemd',
                                   RENDERER='school-classroom-postprocess.service', RECORDER='school-classroom-recorder.service',
                                   LIVE_UNITS=('sfu', 'admission', 'https'), system_preflight=lambda: None,
                                   run=self.system, info=self.invocation, recorder_status=self.school,
                                   media=SimpleNamespace(local_rooms=lambda _: list(self.rooms), pilot_deployment=lambda: {}))
        v = self.vps
        v.REC_PROGRAM, v.STATE, v.PAIR = v.REC / 'releases/0.4.23', v.CFG / 'version', v.CFG / 'pairing'
        for folder in (v.CFG, v.SPOOL, v.REC_PROGRAM, v.SYSTEMD):
            folder.mkdir(mode=0o700, parents=True)
        self.config = {'version': '1.3.2', 'profile': 'compact', 'school': 'https://school.test/site'}
        self.write(v.STATE, json.dumps(self.config), 0o600)
        self.write(v.PAIR, json.dumps({'site': self.config['school'], 'recording_secret': 'fixture-only'}), 0o600)
        self.write(v.SPOOL / 'worker.json', json.dumps({'id': 'b'*32}), 0o600)
        self.replay = v.REC_PROGRAM / 'replay.js'
        self.write(self.replay, self.stock)
        unit = v.SYSTEMD / v.RENDERER
        drop = unit.with_name(unit.name + '.d')
        drop.mkdir()
        r1_program = v.REC_PROGRAM.with_name('0.4.23-renderer-r1')
        with ZipFile(RECORDER) as stock, ZipFile(R1) as r1:
            self.write(v.REC_PROGRAM / 'archive.py', stock.read('archive.py'))
            for name in ('movie.py', 'postprocess.py'):
                self.write(v.REC_PROGRAM / name, r1.read(name))
            self.write(unit, stock.read(v.RENDERER))
        self.write(drop / '70-renderer-r1.conf', '[Service]\n' + ''.join(
            f'BindReadOnlyPaths={r1_program / name}:{v.REC_PROGRAM / name}\n' for name in ('movie.py', 'postprocess.py')))
        self.write(v.REC_PROGRAM / 'compact-renderer.py', (ROOT / 'server/offline/compact-renderer.py').read_bytes())
        self.roots = (v.CFG, v.SPOOL, v.REC, v.SYSTEMD)
        self.remote = dict(idle=True, worker_ok=True, native_ready=True)
        self.rooms, self.calls, self.mount_overrides = [], [], {}
        self.break_probe = False
        self.changed_on_probe = False
        self.serial = '1'*32
        file, folder = installer.file, installer.directory
        stack.enter_context(patch.object(installer, 'file', lambda p, *, uid=0, private=False:
                                         file(p, uid=os.geteuid(), private=private)))
        stack.enter_context(patch.object(installer, 'directory', lambda p, uid=0, *, private=False:
                                         folder(p, os.geteuid(), private=private)))
        stack.enter_context(patch.object(installer.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1001)))
        stack.enter_context(patch.object(installer.Repair, 'proc_file', side_effect=self.mounted))
        stack.enter_context(patch.object(installer.Repair, 'probe', side_effect=self.probe))

    @staticmethod
    def write(path, data, mode=0o644):
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        path.chmod(mode)

    def snapshot(self):
        return {str(path): (path.read_bytes(), path.stat().st_mode) for root in self.roots
                for path in root.rglob('*') if path.is_file()}

    def invocation(self, *argv):
        self.assertIn(argv[-1], (*self.vps.LIVE_UNITS, self.vps.RECORDER))
        self.assertEqual(argv[:-1], ('-p', 'InvocationID'))
        return 'a'*32

    def system(self, *argv, output, timeout):
        self.assertEqual(argv[:3], ('systemctl', 'show', '--no-pager'))
        self.assertEqual(argv[-1], self.vps.RENDERER)
        unit = self.vps.SYSTEMD / self.vps.RENDERER
        names = ['70-renderer-r1.conf'] + (['80-compact.conf'] if self.config['profile'] == 'compact' else [])
        paths = ' '.join(str(unit.with_name(unit.name + '.d') / name) for name in names)
        fields = {'FragmentPath': unit, 'DropInPaths': paths, 'NeedDaemonReload': 'no', 'SubState': 'running',
                  'User': 'school-recorder', 'MainPID': '450', 'InvocationID': self.serial}
        return '\n'.join(f'{key}={value}' for key, value in fields.items()).encode()

    def school(self, config, keys, worker):
        self.assertEqual(config['school'], keys['site'])
        self.assertEqual(worker, 'b'*32)
        return self.remote.copy()

    def mounted(self, name):
        return installer.file(self.mount_overrides.get(name, self.vps.REC_PROGRAM / name))

    def probe(self, test=False):
        self.calls.append(test)
        if test and self.changed_on_probe:
            self.serial = '2'*32
        if test and self.break_probe:
            raise installer.Refused('Synthetic encoder refused')

    def run_repair(self, mode):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            installer.run(self.vps, mode, (self.stock, self.fixed))
            return out.getvalue()

    def test_preflight_apply_idempotence_and_rollback_touch_only_the_replay_file(self):
        original = self.snapshot()
        self.assertIn('no_changes=yes', self.run_repair('check-only'))
        self.assertEqual(self.calls, [False])
        self.assertEqual(self.snapshot(), original)
        self.assertIn('installed=r2 mp4_check=ok services=unchanged', self.run_repair('apply'))
        self.assertEqual(self.snapshot(), original | {str(self.replay): (self.fixed, self.replay.stat().st_mode)})
        inode = self.replay.stat().st_ino
        self.assertIn('already_selected=yes', self.run_repair('apply'))
        self.assertEqual(inode, self.replay.stat().st_ino)
        self.assertIn('installed=stock mp4_check=ok services=unchanged', self.run_repair('rollback'))
        self.assertEqual(self.snapshot(), original)

    def test_no_apply_with_spool_site_or_media_activity(self):
        job = self.vps.SPOOL / ('c'*32)
        job.mkdir(mode=0o700)
        self.write(job / 'source-000.baci', 'private retained data', 0o600)
        snapshot = self.snapshot()
        with self.assertRaisesRegex(installer.Refused, 'spool still has jobs'):
            self.run_repair('apply')
        self.assertEqual(self.snapshot(), snapshot)
        shutil.rmtree(job)
        self.remote['idle'] = False
        with self.assertRaisesRegex(installer.Refused, 'pending job'):
            self.run_repair('apply')
        self.remote['idle'] = True
        self.rooms.append({'name': 'fixture-room'})
        with self.assertRaisesRegex(installer.Refused, 'open rooms'):
            self.run_repair('apply')
        self.assertEqual(self.replay.read_bytes(), self.stock)
        self.assertFalse(self.calls)

    def test_refuse_unknown_bytes_overlaid_mount_symlink_or_other_version(self):
        self.write(self.replay, b'custom local replay')
        with self.assertRaisesRegex(installer.Refused, 'neither the reviewed'):
            self.run_repair('apply')
        self.assertEqual(self.replay.read_bytes(), b'custom local replay')
        self.write(self.replay, self.stock)
        alternate = self.root / 'other'
        self.write(alternate, self.stock)
        self.mount_overrides['replay.js'] = alternate
        with self.assertRaisesRegex(installer.Refused, 'running renderer does not see'):
            self.run_repair('apply')
        self.mount_overrides.clear()
        self.replay.unlink()
        self.replay.symlink_to(alternate)
        with self.assertRaises(OSError):
            self.run_repair('apply')
        self.assertTrue(self.replay.is_symlink())
        self.replay.unlink()
        self.write(self.replay, self.stock)
        self.write(self.vps.STATE, json.dumps(self.config | {'version': '1.3.1'}), 0o600)
        with self.assertRaisesRegex(installer.Refused, 'only for managed VPS 1.3.2'):
            self.run_repair('apply')
        self.assertEqual(self.replay.read_bytes(), self.stock)
        self.assertFalse(self.calls)

    def test_failed_synthetic_mp4_or_changed_invocation_restores_prior_bytes(self):
        original = self.snapshot()
        self.break_probe = True
        with self.assertRaisesRegex(installer.Refused, 'Previous replay file restored'):
            self.run_repair('apply')
        self.assertEqual(self.snapshot(), original)
        self.break_probe = False
        self.changed_on_probe = True
        with self.assertRaisesRegex(installer.Refused, 'changed independently'):
            self.run_repair('apply')
        self.assertEqual(self.snapshot(), original)

    def test_full_layout_and_concurrent_maintenance(self):
        self.config['profile'] = 'full'
        self.write(self.vps.STATE, json.dumps(self.config), 0o600)
        self.assertIn('installed=r2', self.run_repair('apply'))
        fd = os.open(self.vps.REC, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(installer.Refused, 'maintenance process'):
                self.run_repair('rollback')
        finally:
            os.close(fd)
        self.assertEqual(self.replay.read_bytes(), self.fixed)
