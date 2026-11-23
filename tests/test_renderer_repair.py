"""Renderer r1 release bytes and guarded cutover with disposable stock state."""
import contextlib
import fcntl
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from test_recorder_upgrade import ARTIFACTS, PAYLOAD, REPO, SOURCE, UPGRADE, WORKER, digest

SCRIPT = SOURCE / 'repair-renderer-v0.4.23-r1.py'
PATCH = ARTIFACTS / 'baci-classroom-renderer-v0.4.23-r1.zip'
spec = importlib.util.spec_from_file_location('renderer_repair', SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class PackageTest(unittest.TestCase):
    def test_patch_is_reproducible_and_contains_only_the_two_renderer_modules_and_guards(self):
        with tempfile.TemporaryDirectory() as temp, ZipFile(PAYLOAD) as stock:
            subprocess.run([sys.executable, str(REPO / 'scripts/build-managed-school.py'), '--renderer-repair',
                            '--output-dir', temp], check=True, capture_output=True, timeout=12)
            self.assertEqual((Path(temp) / PATCH.name).read_bytes(), PATCH.read_bytes())
            _, hashes, files = installer.read_patch(PATCH)
            self.assertEqual(hashes, {name: digest(stock.read(name)) for name in stock.namelist()})
            self.assertEqual(set(files), {'movie.py', 'postprocess.py'})
            for name, data in files.items():
                self.assertEqual(data, (SOURCE / name).read_bytes())
                self.assertNotEqual(data, stock.read(name))
            with ZipFile(PATCH) as repair:
                self.assertEqual(repair.read('maintenance.py'), UPGRADE.read_bytes())

    def test_modified_zip_and_symlink_fail_before_loading_helpers(self):
        with tempfile.TemporaryDirectory() as temp:
            bad, link = Path(temp) / 'modified.zip', Path(temp) / 'link.zip'
            bad.write_bytes(PATCH.read_bytes() + b'x')
            link.symlink_to(PATCH)
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                installer.read_patch(bad)
            with self.assertRaises(OSError):
                installer.read_patch(link)


class CutoverTest(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(contextlib.ExitStack())
        temp = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        up, inventory, files = installer.read_patch(PATCH)
        self.up = up
        for key in ('ROOT', 'SPOOL', 'SYSTEMD', 'CONFIG', 'ADMISSION', 'CGROUP'):
            setattr(up, key, temp / key)
        regular, directory = up.regular, up.directory
        stack.enter_context(patch.object(up, 'regular', lambda p, **kw: regular(p, uid=os.geteuid(), forbidden=kw.get('forbidden', 0o022))))
        stack.enter_context(patch.object(up, 'directory', lambda p, **kw: directory(p, uid=os.geteuid(), private=kw.get('private', False))))
        stack.enter_context(patch.object(up.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.geteuid() or 999)))
        stack.enter_context(patch.object(up.platform, 'machine', return_value='x86_64'))
        for path in (up.ROOT, up.SPOOL, up.SYSTEMD):
            path.mkdir(mode=0o700)
        up.CGROUP.write_text('cpu memory io')
        self.repair = installer.Repair(up, inventory, files)
        with ZipFile(PAYLOAD) as stock:
            for name in up.PROGRAM:
                stock.extract(name, self.repair.base)
            for name in up.UNITS:
                stock.extract(name, up.SYSTEMD)
        self.stored = up.SPOOL / WORKER
        self.stored.mkdir(mode=0o700)
        for path, content in ((up.SPOOL / 'worker.json', json.dumps({'id': WORKER})), (up.SPOOL / '.lock', ''),
                              (self.stored / '.retained', ''), (self.stored / 'source-000.baci', 'prior retained data'),
                              (up.CONFIG, json.dumps({'site': 'https://school.test/sub', 'secret': 'c'*32})),
                              (up.ADMISSION, json.dumps({'school_origin': 'https://school.test', 'api_key': 's'*12, 'api_secret': 'j'*32}))):
            path.write_text(content)
            path.chmod(0o600)
        self.remote = {'idle': True, 'worker_ok': True, 'native_ready': True}
        self.site = stack.enter_context(patch.object(up, 'school_status', side_effect=lambda *_: self.remote.copy()))
        self.rooms = stack.enter_context(patch.object(up, 'livekit_rooms', return_value=[]))
        self.command = stack.enter_context(patch.object(up, 'sys_command', side_effect=self.systemd))
        self.probe = stack.enter_context(patch.object(installer.Repair, 'probe', side_effect=self.handshake))
        self.wait = stack.enter_context(patch.object(installer.time, 'sleep'))
        stack.enter_context(patch.object(installer, 'read_patch', return_value=(up, inventory, files)))
        self.events = []
        self.running, self.serial, self.bad_test = True, 1, False
        self.baseline = self.snapshot()

    def snapshot(self):
        return {str(p): (p.read_bytes(), p.stat().st_mode) for root in (self.up.ROOT, self.up.SYSTEMD, self.up.SPOOL)
                for p in root.rglob('*') if p.is_file()}

    def systemd(self, program, verb, *argv, read=False):
        up = self.up
        self.assertEqual(program, 'systemctl')
        if verb == 'show':
            unit, keys = argv[-1], argv[-2].split('=', 1)[1].split(',')
            drops = [up.SYSTEMD / up.DROPIN / name for name in (up.CREDENTIAL, up.NATIVE)] if unit == up.RECORDER else []
            if unit == up.POSTPROCESS and self.repair.drop.is_file():
                drops = [self.repair.drop]
            active = unit != up.POSTPROCESS or self.running
            invocation = {up.RECORDER: 'a'*32, installer.SFU: 'b'*32, up.POSTPROCESS: f'{self.serial:032x}'}[unit]
            data = {'FragmentPath': str(up.SYSTEMD / unit), 'DropInPaths': ' '.join(map(str, drops)), 'NeedDaemonReload': 'no',
                    'SubState': 'running' if active else 'dead', 'ActiveState': 'active' if active else 'inactive', 'InvocationID': invocation}
            return '\n'.join(f'{name}={data[name]}' for name in keys)
        self.events.append((verb, *argv))
        if verb in ('start', 'stop'):
            self.assertEqual(argv, (up.POSTPROCESS,))
            self.running = verb == 'start'
            self.serial += self.running
        else:
            self.assertEqual((verb, argv), ('daemon-reload', ()))

    def handshake(self, command, answer, timeout):
        self.assertTrue(self.running)
        if command == 'self-test-mp4':
            self.assertEqual(answer, 'baci-render/1 tested mp4')
            self.assertTrue(self.repair.drop.is_file())
            if self.bad_test:
                raise self.up.Refused('Synthetic MP4 did not finalize.')
        else:
            self.assertEqual((command, answer), ('ready', 'baci-render/1'))

    def run_repair(self, apply=False):
        mode = os.umask(0o077)
        try:
            with contextlib.redirect_stdout(open_text := StringIO()):
                installer.run(SimpleNamespace(archive=PATCH, apply=apply))
            return open_text.getvalue()
        finally:
            os.umask(mode)

    def test_check_only_does_not_touch_units_or_recordings_or_request_synthetic_render(self):
        self.assertIn('Check only', self.run_repair())
        self.assertEqual(self.snapshot(), self.baseline)
        self.assertEqual(self.events, [])
        self.probe.assert_called_once_with('ready', 'baci-render/1', 2)

    def test_locked_installer_and_custom_dropin_fail_before_stopping_renderer(self):
        fd = os.open(self.up.ROOT, os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError, 'Another Recorder'):
                self.run_repair(True)
        finally:
            os.close(fd)
        self.repair.drop.parent.mkdir()
        with self.assertRaisesRegex(ValueError, 'custom drop-in'):
            self.run_repair(True)
        self.assertEqual(self.events, [])

    def test_open_room_remote_queue_and_pending_receipt_are_each_refused(self):
        self.rooms.return_value = [{}]
        with self.assertRaisesRegex(ValueError, 'LiveKit has open rooms'):
            self.run_repair(True)
        self.rooms.return_value = []
        self.remote['idle'] = False
        with self.assertRaisesRegex(ValueError, 'open bell, a pending job'):
            self.run_repair(True)
        self.remote['idle'] = True
        (self.stored / 'receipt.json').touch()
        with self.assertRaisesRegex(ValueError, 'receipt is still pending'):
            self.run_repair(True)
        self.assertEqual(self.events, [])

    def test_altered_stock_bytes_and_extra_bytecode_are_rejected(self):
        module = self.repair.base / 'movie.py'
        original = module.read_bytes()
        module.write_bytes(original + b'x')
        with self.assertRaisesRegex(ValueError, 'bytes differ: movie.py'):
            self.run_repair(True)
        module.write_bytes(original)
        (self.repair.base / '__pycache__').mkdir()
        with self.assertRaisesRegex(ValueError, 'no extra modules or caches'):
            self.run_repair(True)
        self.assertEqual(self.events, [])

    def test_only_renderer_restarts_and_patch_files_are_read_only_inside_its_mount_namespace(self):
        answer = self.run_repair(True)
        self.assertIn('sandboxed H.264/AAC MP4 and school heartbeat verified', answer)
        post = self.up.POSTPROCESS
        self.assertEqual(self.events, [('stop', post), ('daemon-reload',), ('start', post)])
        updated = self.snapshot()
        self.assertEqual({name: updated[name] for name in self.baseline}, self.baseline)
        self.assertEqual(set(updated) - set(self.baseline), {str(self.repair.drop), *(str(self.repair.patch / n) for n in installer.FIXED)})
        lines = self.repair.drop.read_text().splitlines()
        self.assertEqual(lines, ['[Service]', *(f'BindReadOnlyPaths={self.repair.patch / n}:{self.repair.base / n}' for n in installer.FIXED)])
        with self.assertRaisesRegex(ValueError, 'Do not apply again'):
            self.run_repair(True)

    def test_bell_opened_after_stop_prevents_dropin_and_old_renderer_is_restarted(self):
        self.site.side_effect = lambda *_: self.remote | {'idle': self.running}
        with self.assertRaisesRegex(ValueError, 'Original renderer is running again'):
            self.run_repair(True)
        self.assertEqual(self.events, [('stop', self.up.POSTPROCESS), ('start', self.up.POSTPROCESS)])
        self.assertEqual(self.snapshot(), self.baseline)
        self.assertFalse(self.repair.patch.exists())
        self.assertFalse(self.repair.drop.parent.exists())

    def test_failed_real_probe_rolls_back_when_idle(self):
        self.bad_test = True
        with self.assertRaisesRegex(ValueError, 'Original renderer is running again'):
            self.run_repair(True)
        post = self.up.POSTPROCESS
        self.assertEqual(self.events, [('stop', post), ('daemon-reload',), ('start', post)] * 2)
        self.assertEqual(self.snapshot(), self.baseline)
        self.assertFalse(self.repair.patch.exists())
        self.assertFalse(self.repair.drop.parent.exists())

    def test_no_restart_for_rollback_if_new_work_appears_after_cutover(self):
        self.bad_test = True
        self.site.side_effect = lambda *_: self.remote | {'idle': self.probe.call_count < 2}
        with self.assertRaisesRegex(ValueError, 'rollback could not be confirmed'):
            self.run_repair(True)
        self.assertTrue(self.repair.drop.is_file())
        self.assertEqual(self.events, [('stop', self.up.POSTPROCESS), ('daemon-reload',), ('start', self.up.POSTPROCESS)])


if __name__ == '__main__':
    unittest.main()
