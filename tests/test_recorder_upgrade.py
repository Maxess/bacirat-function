"""Release bytes, guarded cutover and rollback on disposable VPS state."""
import contextlib
import hashlib
import importlib.util
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / 'server/recorder'
ARTIFACTS = REPO / 'artifacts'
UPGRADE = SOURCE / 'upgrade-v0.4.23.py'
PAYLOAD = ARTIFACTS / 'baci-classroom-recorder-v0.4.23.zip'
SITE = ARTIFACTS / 'school-classroom-plugin-v0.4.23.zip'
WORKER = 'e' * 32

spec = importlib.util.spec_from_file_location('upgrader_0423', UPGRADE)
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checksums(data):
    return {name: sha for sha, name in (line.split('  ', 1) for line in data.decode().splitlines())}


class ReleaseTest(unittest.TestCase):
    def test_all_distributables_match_pins_and_separate_site_from_vps(self):
        outer = ARTIFACTS / 'baci-classroom-upgrade-v0.4.23.zip'
        release_hashes = checksums((ARTIFACTS / 'SHA256SUMS-v0.4.23.txt').read_bytes())
        self.assertEqual(set(release_hashes), {PAYLOAD.name, SITE.name, outer.name,
                        'baci-plugin-manager-update-v0.1.29.zip', 'baci-plugin-manager-v0.1.29.zip'})
        for name, expected in release_hashes.items():
            self.assertEqual(digest((ARTIFACTS / name).read_bytes()), expected, name)
        self.assertEqual(release_hashes[PAYLOAD.name], up.PAYLOAD_SHA256)
        with ZipFile(outer) as bundle, ZipFile(PAYLOAD) as rec, ZipFile(SITE) as site:
            contained = checksums(bundle.read('SHA256SUMS.txt'))
            self.assertEqual(set(bundle.namelist()), contained.keys() | {'SHA256SUMS.txt'})
            for name in set(release_hashes) - {outer.name}:
                self.assertEqual(digest(bundle.read(name)), release_hashes[name])
            for name, expected in contained.items():
                self.assertEqual(digest(bundle.read(name)), expected, name)
            # The external VPS updater may be revised without changing the shipped release ZIPs.
            self.assertEqual(digest(bundle.read(UPGRADE.name)), '49f08faf1e84df77c0c71a8030e6dbfbbeb198d800c097b3979ea3b5db6abb17')
            self.assertEqual(set(rec.namelist()), up.PROGRAM | up.UNITS)
            self.assertEqual(stat.S_IMODE(rec.getinfo(up.BINARY).external_attr >> 16), 0o755)
            for name in ('board.js','pdf-resources.zip','livekit-client.umd.js'):
                self.assertEqual(rec.read(name), site.read('assets/' + name))
            for name in {'postprocess.py','native_capture.py','disk_writer.py','recording.py','README-fa.md'} | up.UNITS:
                self.assertEqual(rec.read(name), (SOURCE / name).read_bytes())
            manifest = json.loads(site.read('plugin.json'))
            self.assertEqual(manifest['version'], '0.4.23')
            self.assertIn('0.4.22', manifest['preserve_settings_from'])
            self.assertEqual({f['key']:f['type'] for f in manifest['settings']}['class_media_modes'], 'class-modes')
            with ZipFile(BytesIO(bundle.read('baci-plugin-manager-update-v0.1.29.zip'))) as update:
                manager = update.read('plugins/school-plugins.php')
                self.assertEqual(manager, (REPO / 'plugin-manager/plugins/school-plugins.php').read_bytes())
                self.assertIn(f"'0.4.23' => '{digest(site.read('plugin.json'))}'".encode(), manager)
                self.assertNotIn(up.BINARY, update.namelist())

    @unittest.skipUnless(platform.system()=='Linux' and platform.machine()=='x86_64', 'binary targets Linux amd64')
    def test_shipped_elf_starts_with_pinned_protocol(self):
        with tempfile.TemporaryDirectory() as tmp, ZipFile(PAYLOAD) as rec:
            binary = Path(rec.extract(up.BINARY, tmp))
            binary.chmod(0o755)
            self.assertEqual(subprocess.check_output([binary, '--check'], timeout=3), b'baci-rtp/2\n')


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(contextlib.ExitStack())
        temp = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.uid = os.geteuid()
        self.service_uid = self.uid or 999
        # Test actual filesystem validation in a non-root test runner, with a fixture account.
        regular, directory = up.regular, up.directory
        stack.enter_context(patch.object(up, 'regular', lambda p, uid=0, **kw: regular(p, uid=self.uid, **kw)))
        stack.enter_context(patch.object(up, 'directory', lambda p, uid=0, **kw: directory(p, uid=self.uid, **kw)))
        paths = {name:temp/name for name in ('ROOT','SPOOL','SYSTEMD','CONFIG','ADMISSION','CGROUP')}
        stack.enter_context(patch.multiple(up, **paths))
        for folder in (up.ROOT, up.SYSTEMD, up.SPOOL):
            folder.mkdir(mode=0o700)
        up.CGROUP.write_text('cpu memory io\n')
        stack.enter_context(patch.object(up.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=self.service_uid)))
        stack.enter_context(patch.object(up.platform, 'machine', return_value='x86_64'))
        self.records = up.SPOOL / WORKER
        self.records.mkdir(mode=0o700)
        self._file(self.records / '.retained', b'')
        self._file(self.records / 'source-1.baci', b'prior retained data')
        self._file(up.SPOOL / 'worker.json', json.dumps({'id':WORKER}).encode())
        self._file(up.SPOOL / '.lock', b'')
        self._file(up.CONFIG, json.dumps({'site':'https://school.test/sample','secret':'z'*32}).encode())
        self._file(up.ADMISSION, json.dumps({'school_origin':'https://school.test','api_key':'k'*12,'api_secret':'s'*32}).encode())
        self.command = stack.enter_context(patch.object(up, 'sys_command', side_effect=self.systemd))
        self.school = stack.enter_context(patch.object(up, 'school_status', side_effect=self.status))
        self.sfu = stack.enter_context(patch.object(up, 'livekit_rooms', return_value=[]))
        self.renderer = stack.enter_context(patch.object(up, 'test_renderer'))
        self.ready = stack.enter_context(patch.object(up, 'wait_ready', side_effect=self.wait_ready))
        self.remote = {'idle':True,'worker_ok':True,'native_ready':True}
        self.events, self.was_started = [], False
        self.release = up.ROOT / 'releases' / up.VERSION

    def _file(self,path, data):
        path.write_bytes(data)
        path.chmod(0o600)

    def install(self, version, credential=True):
        with ZipFile(ARTIFACTS / f'baci-classroom-recorder-v{version}.zip') as old:
            for name in up.COMMON | (up.DEFERRED if version == '0.4.22' else set()):
                old.extract(name, up.ROOT)
            (up.SYSTEMD / up.RECORDER).write_bytes(old.read(up.RECORDER))
        if credential:
            (up.SYSTEMD / (up.RECORDER + '.d')).mkdir()
            (up.SYSTEMD / up.DROPIN / up.CREDENTIAL).write_bytes((SOURCE / up.DROPIN / up.CREDENTIAL).read_bytes())
        return (up.ROOT / 'recording.py').read_bytes()

    def fontconfig(self):
        root = up.SPOOL / '.cache'
        path = root / 'fontconfig'
        path.mkdir(parents=True)
        root.chmod(0o755)
        path.chmod(0o755)
        for item in ('CACHEDIR.TAG', 'a'*32 + '-le64.cache-8', 'b'*32 + '-le64.cache-11'):
            file = path / item
            self._file(file, b'fixture cache (not a recording)')
            if self.uid == 0:
                os.chown(file, self.service_uid, -1)
        return path

    def status(self, site, _, worker):
        self.assertEqual((site.hostname, site.path, worker), ('school.test','/sample',WORKER))
        return self.remote.copy()

    def systemd(self, program, op, *argv, read=False):
        self.assertEqual(program, 'systemctl')
        if op != 'show':
            self.events.append((op, *argv))
            return
        unit, keys = argv[-1], argv[-2].split('=',1)[1].split(',')
        if unit == up.RECORDER:
            drops = [str(up.SYSTEMD/up.DROPIN/name) for name in (up.CREDENTIAL, up.NATIVE) if (up.SYSTEMD/up.DROPIN/name).exists()]
            all_ = {'FragmentPath':str(up.SYSTEMD / up.RECORDER),'DropInPaths':' '.join(drops),
                    'NeedDaemonReload':'no','SubState':'running'}
        else:
            all_ = {'LoadState':'not-found','DropInPaths':'','ActiveState':'active','SubState':'running','InvocationID':'a'*32}
        return '\n'.join(f'{key}={all_[key]}' for key in keys)

    def wait_ready(self, marker, check=None):
        if check is not None:
            self.was_started = True
            self.assertTrue(check(), 'checked both the renderer and the new school heartbeat')
        self.events.append(('ready', marker))

    def resolve(self, apply=False, archive=PAYLOAD):
        umask = os.umask(0o077)
        try:
            with contextlib.redirect_stdout(StringIO()):
                up.upgrade(SimpleNamespace(archive=archive, apply=apply))
        finally:
            os.umask(umask)

    def test_read_only_preflight_for_stock_21_without_legacy_dropin(self):
        # For .21 the runtime credential drop-in can be missing; the upgrader adds it on apply.
        self.install('0.4.21', credential=False)
        self.fontconfig()
        self.resolve()
        self.assertEqual(self.events, [])
        self.assertFalse(self.release.exists())
        self.assertFalse((up.SYSTEMD/up.DROPIN).exists())

    def test_success_preserves_worker_credential_browser_and_old_runtime(self):
        old = self.install('0.4.22')
        fontconfig = self.fontconfig()
        fonts_before = {file.name:file.read_bytes() for file in fontconfig.iterdir()}
        config, worker, credential = up.CONFIG.read_bytes(), (up.SPOOL/'worker.json').read_bytes(), (up.SYSTEMD/up.DROPIN/up.CREDENTIAL).read_bytes()
        (up.ROOT/'browsers').mkdir()
        (up.ROOT/'browsers/unchanged').touch()
        self.resolve(True)
        self.assertTrue(self.was_started)
        self.assertEqual({file.name:file.read_bytes() for file in fontconfig.iterdir()}, fonts_before)
        self.assertEqual(stat.S_IMODE(self.release.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.release/'fonts').stat().st_mode), 0o755)
        self.assertEqual((up.ROOT / 'recording.py').read_bytes(), old)
        self.assertTrue((up.ROOT/'browsers/unchanged').is_file())
        self.assertEqual((self.records / 'source-1.baci').read_bytes(), b'prior retained data')
        self.assertEqual((up.CONFIG.read_bytes(), (up.SPOOL/'worker.json').read_bytes(), (up.SYSTEMD/up.DROPIN/up.CREDENTIAL).read_bytes()),
                         (config, worker, credential))
        self.assertEqual([event for event in self.events if event[0] in ('stop','start')],
                         [('stop',up.RECORDER),('start',up.POSTPROCESS),('start',up.RECORDER)])
        with self.assertRaisesRegex(up.Refused,'already exists'):
            self.resolve(True)

    def test_stock_home_cache_does_not_hide_a_local_recording_or_a_receipt(self):
        self.install('0.4.22')
        fontconfig = self.fontconfig()
        self.resolve()  # read-only pass with state directory also serving as HOME
        self.assertTrue((fontconfig / 'CACHEDIR.TAG').is_file())
        active = up.SPOOL / ('a' * 32)
        active.mkdir(mode=0o700)
        with self.assertRaisesRegex(up.Refused,'render or delivery is pending'):
            self.resolve(True)
        active.rmdir()
        self._file(self.records / 'receipt.json', b'{}')
        with self.assertRaisesRegex(up.Refused,'receipt is still pending'):
            self.resolve(True)
        self.assertEqual(self.events, [])

    def test_unrecognized_entries_are_never_skipped_in_the_spool_or_cache(self):
        self.install('0.4.21')
        fontconfig = self.fontconfig()
        for entry in (up.SPOOL / 'unknown\nitem', fontconfig.parent / 'other', fontconfig / 'receipt.json'):
            with self.subTest(entry=entry.name):
                entry.touch()
                with self.assertRaisesRegex(up.Refused,'Unexpected Recorder spool entry') as refusal:
                    self.resolve(True)
                self.assertNotIn('\n', str(refusal.exception))
                self.assertTrue(entry.exists())
                entry.unlink()
        self.assertEqual(self.events, [])

    def test_cache_dirs_and_files_cannot_be_symlinks_or_writable_by_other_users(self):
        self.install('0.4.22')
        fontconfig = self.fontconfig()
        fontconfig.parent.chmod(0o777)
        with self.assertRaisesRegex(up.Refused,'Unsafe directory'):
            self.resolve(True)
        fontconfig.parent.chmod(0o755)
        saved = fontconfig.with_name('elsewhere')
        fontconfig.rename(saved)
        fontconfig.symlink_to(saved, target_is_directory=True)
        with self.assertRaisesRegex(up.Refused,'Unsafe directory|Unexpected Recorder spool entry'):
            self.resolve(True)
        fontconfig.unlink()
        saved.rename(fontconfig)
        slot = fontconfig / ('d'*32 + '-le64.cache-8')
        slot.symlink_to(self.records / 'source-1.baci')
        with self.assertRaisesRegex(up.Refused,'Unexpected Recorder spool entry'):
            self.resolve(True)
        slot.unlink()
        file = fontconfig / 'CACHEDIR.TAG'
        file.chmod(0o666)
        with self.assertRaisesRegex(up.Refused,'Unexpected Recorder spool entry'):
            self.resolve(True)
        self.assertEqual(self.events, [])

    def test_reject_tampered_zip_or_installed_files(self):
        self.install('0.4.22')
        wrong = up.ROOT.parent / 'wrong.zip'; wrong.write_bytes(PAYLOAD.read_bytes()+b'x')
        with self.assertRaisesRegex(up.Refused,'SHA-256'):
            self.resolve(True, wrong)
        (up.ROOT/'transport.py').write_text('# modified')
        with self.assertRaisesRegex(up.Refused,'do not match stock'):
            self.resolve(True)
        self.assertEqual(self.events, [])

    def test_reject_live_room_remote_queue_and_local_receipt(self):
        self.install('0.4.22')
        self.remote['idle'] = False
        with self.assertRaisesRegex(up.Refused,'pending job'):
            self.resolve(True)
        self.remote['idle'] = True
        self.sfu.return_value = [{}]
        with self.assertRaisesRegex(up.Refused,'open rooms'):
            self.resolve(True)
        self._file(self.records/'receipt.json', b'{}')
        with self.assertRaisesRegex(up.Refused,'receipt is still pending'):
            self.resolve(True)
        self.assertEqual(self.events, [])
        self.assertTrue((self.records/'receipt.json').is_file())

    def test_queue_race_after_stop_does_not_install_any_files(self):
        self.install('0.4.22')
        self.sfu.side_effect = [[], [{}]]
        with self.assertRaisesRegex(up.Refused,'Previous Recorder 0.4.22 restarted'):
            self.resolve(True)
        self.assertIn(('start',up.RECORDER), self.events)
        self.assertNotIn(('start',up.POSTPROCESS), self.events)
        self.assertFalse(self.release.exists())

    def test_renderer_failure_restores_origin_without_creating_mixed_install(self):
        old = self.install('0.4.21', credential=False)
        self.renderer.side_effect = up.Refused('fixture renderer failure')
        with self.assertRaisesRegex(up.Refused,'Previous Recorder 0.4.21 restarted'):
            self.resolve(True)
        self.assertEqual(self.events[-1], ('ready','recorder: compositor and encoder available (0.4.21)'))
        self.assertEqual((up.ROOT/'recording.py').read_bytes(), old)
        self.assertFalse((up.SYSTEMD/up.POSTPROCESS).exists())
        self.assertFalse((up.SYSTEMD/up.DROPIN).exists())
        self.assertFalse(self.release.exists())

    def test_no_rollback_onto_newly_active_recording(self):
        self.install('0.4.22')
        self.sfu.side_effect = [[], [], [], [], [{}]]
        self.ready.side_effect = up.Refused('fixture startup error')
        with self.assertRaisesRegex(up.Refused,'rollback could not be confirmed'):
            self.resolve(True)
        self.assertTrue(self.release.exists())
        self.assertIn(('start',up.RECORDER), self.events)
        self.assertEqual(self.events.count(('stop',up.RECORDER)), 1)


if __name__ == '__main__':
    unittest.main()
