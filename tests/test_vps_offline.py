"""Pure checks for the installer/pairing release; distinct from the disconnected VM rehearsal."""
from hashlib import sha256
import importlib.util
from io import BytesIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
OFFLINE = ROOT / 'server/offline'
spec = importlib.util.spec_from_file_location('build_vps_offline', ROOT / 'scripts/build-vps-offline.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
setup = build.setup
ARTIFACTS = ROOT / 'artifacts'
SITE_UPDATER = 'baci-plugin-manager-update-v0.1.31.zip'
CLASSROOM = 'school-classroom-plugin-v0.4.24.zip'
GATEWAY = 'plugins/school-plugins.php'


class OfflineSetupTest(unittest.TestCase):
    def test_school_exact_subpath_and_distinct_endpoints(self):
        link = setup.names('https://TEST-school.example.ir/Campus/secondary/', 'Rtc.Example.ir', '192.0.0.9')
        self.assertEqual(link['school'], 'https://test-school.example.ir/Campus/secondary')
        self.assertEqual(link['origin'], 'https://test-school.example.ir')
        self.assertEqual(link['media'], 'rtc.example.ir')
        for site, host, ipv4 in [
            ('http://school.example.ir', 'rtc.example.ir', '192.0.0.9'),
            ('https://x@school.example.ir', 'rtc.example.ir', '192.0.0.9'),
            ('https://school.example.ir:443', 'rtc.example.ir', '192.0.0.9'),
            ('https://school.example.ir/a?q=1', 'rtc.example.ir', '192.0.0.9'),
            ('https://school.example.ir/a#', 'rtc.example.ir', '192.0.0.9'),
            ('https://school.example.ir/a/../b', 'rtc.example.ir', '192.0.0.9'),
            ('https://school.example.ir', 'school.example.ir', '192.0.0.9'),
            ('https://school.example.ir', '127.0.0.1', '192.0.0.9'),
            ('https://school.example.ir', 'rtc.example.ir', '127.0.0.1'),
            ('https://school.example.ir', 'rtc.example.ir', '224.42.42.42'),
            ('https://school.example.ir', 'rtc.example.ir', '::1'),
        ]:
            with self.subTest(site=site, host=host, ip=ipv4), self.assertRaises(setup.Refused):
                setup.names(site, host, ipv4)

    def test_pair_is_format_bound_and_separates_recorder_key(self):
        config = setup.names('https://school.example.ir/sub', 'rtc.example.ir', '192.0.0.9')
        first, second = setup.pairing(config), setup.pairing(config)
        self.assertEqual(set(first), {'format','site','signal_url','api_key','api_secret','recording_secret'})
        self.assertEqual(first['format'], 'baci-vps-pairing/1')
        self.assertEqual(first['site'], config['school'])
        self.assertEqual(first['signal_url'], 'wss://rtc.example.ir')
        self.assertRegex(first['api_key'], r'^[a-f0-9]{24}$')
        for key in ('api_secret','recording_secret'):
            self.assertRegex(first[key], r'^[a-f0-9]{64}$')
            self.assertNotEqual(first[key], second[key])
        self.assertNotEqual(first['api_secret'], first['recording_secret'])

    def test_cert_reader_rejects_links_pipes_large_files_and_nonprivate_keys(self):
        with tempfile.TemporaryDirectory() as path:
            p = Path(path)
            cert, key = p / 'fullchain.pem', p / 'privkey.pem'
            cert.write_bytes(b'-----BEGIN CERTIFICATE-----\nYWJj\n-----END CERTIFICATE-----\n')
            key.write_bytes(b'-----BEGIN PRIVATE KEY-----\nYWJj\n-----END PRIVATE KEY-----\n')
            key.chmod(0o644)
            with self.assertRaisesRegex(setup.Refused, '0600'):
                setup.cert_inputs(cert, key)
            key.chmod(0o600)
            self.assertEqual(setup.cert_inputs(cert, key), (cert.read_bytes(), key.read_bytes()))
            alias = p / 'alias'
            alias.symlink_to(key)
            with self.assertRaises(setup.Refused):
                setup.cert_inputs(cert, alias)
            fifo = p / 'fifo'
            setup.os.mkfifo(fifo)
            with self.assertRaises(setup.Refused):
                setup.cert_inputs(fifo, key)
            key.write_bytes(b'A' * 131073)
            with self.assertRaises(setup.Refused):
                setup.cert_inputs(cert, key)

    def test_offline_services_reuse_stock_fences_with_no_acme_or_admin_listener(self):
        config = setup.names('https://school.example.ir/sub', 'rtc.example.ir', '192.0.0.9')
        caddy = setup.edge_config(config)
        for line in ('admin off','auto_https off','ocsp_stapling off','protocols h1 h2','bind 0.0.0.0','tls /etc/school-classroom/tls/fullchain.pem',
                     'forward_auth 127.0.0.1:7883','reverse_proxy 127.0.0.1:7880','header Origin https://school.example.ir'):
            self.assertIn(line, caddy)
        self.assertNotIn('https://school.example.ir/sub', caddy)  # Browser sends origin, not URL path.
        self.assertIn('User=school-classroom-tls', setup.edge_unit())
        self.assertIn('AmbientCapabilities=CAP_NET_BIND_SERVICE', setup.edge_unit())
        self.assertIn('UnsetEnvironment=GOMAXPROCS', setup.media.sfu_unit_config())
        self.assertIn(str(setup.CHROME) + ' flags=(unconfined)', setup.profile_text())
        self.assertIn('userns,', setup.profile_text())
        self.assertNotIn('sysctl', setup.profile_text())
        self.assertNotIn(setup.RENDERER, setup.ENABLED)
        with ZipFile(ARTIFACTS / setup.REC_ASSET) as recorder:
            renderer = recorder.read(setup.RENDERER).decode()
            self.assertIn('PrivateNetwork=true', renderer)
            self.assertIn('InaccessiblePaths=-/etc/school-classroom-recorder.json', renderer)
            self.assertIn(b"'chromium_sandbox':True", recorder.read('postprocess.py'))

    def test_fresh_conflict_and_rerun_do_not_migrate(self):
        with tempfile.TemporaryDirectory() as path:
            app = Path(path) / 'existing'
            app.mkdir()
            marker = app / 'marker'
            marker.write_bytes(b'untouched')
            with patch.object(setup, 'APP', app), self.assertRaisesRegex(setup.Refused, 'nothing was overwritten'):
                setup.fresh_preflight()
            self.assertEqual(marker.read_bytes(), b'untouched')
        config = {'version': setup.VERSION}
        with patch.object(setup, 'system_preflight'), patch.object(setup, 'STATE') as state, \
                patch.object(setup, 'json_file', return_value=config), patch.object(setup, 'doctor', return_value=True) as doc, \
                patch.object(setup, 'deb_install') as apt, patch.object(setup, 'export_pair') as export:
            state.is_file.return_value = True
            self.assertEqual(setup.main([]), 0)
            doc.assert_called_once_with(config, False)
            apt.assert_not_called()
            export.assert_not_called()

    def test_root_writes_are_exclusive_and_reclaim_only_failed_new_file(self):
        with tempfile.TemporaryDirectory() as path, patch.object(setup.os, 'fchown'):
            p = Path(path) / 'keys'
            setup.write(p, 'synthetic', 0o600)
            with self.assertRaises(FileExistsError):
                setup.write(p, 'must not replace', 0o600)
            self.assertEqual(p.read_text(), 'synthetic')
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            with patch.object(setup.os, 'fsync', side_effect=OSError), self.assertRaises(OSError):
                setup.write(p.with_name('fresh'), 'interrupted')
            self.assertFalse(p.with_name('fresh').exists())

    def test_frozen_vps_payload_and_current_site_deliveries(self):
        for name in (setup.REC_ASSET, setup.PATCH_ASSET):
            self.assertEqual(sha256((ARTIFACTS / name).read_bytes()).hexdigest(), setup.PAYLOAD[name])
        with ZipFile(ARTIFACTS / build.SITE) as site:
            self.assertCountEqual(site.namelist(), [SITE_UPDATER, CLASSROOM, 'START-HERE-fa.txt', 'SHA256SUMS.txt'])
            self.assertEqual(site.read(CLASSROOM), (ARTIFACTS / CLASSROOM).read_bytes())
            self.assertEqual(site.read(SITE_UPDATER), (ARTIFACTS / SITE_UPDATER).read_bytes())
            for line in site.read('SHA256SUMS.txt').decode().splitlines():
                digest, name = line.split('  ')
                self.assertEqual(sha256(site.read(name)).hexdigest(), digest)
            with ZipFile(BytesIO(site.read(SITE_UPDATER))) as updater, ZipFile(ARTIFACTS / 'baci-plugin-manager-update-v0.1.30.zip') as previous:
                self.assertCountEqual(updater.namelist(), previous.namelist())
                self.assertEqual([p for p in updater.namelist() if updater.read(p) != previous.read(p)], [GATEWAY])
                self.assertEqual(updater.read(GATEWAY), (ROOT / 'plugin-manager' / GATEWAY).read_bytes())

    def test_shell_syntax_and_isolation(self):
        for path in (OFFLINE / 'install.sh', OFFLINE / 'apt-local.sh', ROOT / 'scripts/test-vps-offline-vm.sh'):
            subprocess.run(['bash', '-n', path], check=True)
        apt = (OFFLINE / 'apt-local.sh').read_text()
        self.assertIn('file:%s/apt', apt)
        self.assertIn('Dir::Etc::sourceparts "-";', apt)
        self.assertIn('unshare --net', apt)
        self.assertIn('--no-remove', apt)
        self.assertNotIn('--allow-downgrades', apt)
        self.assertIn("'--no-index'", (OFFLINE / 'setup.py').read_text())


if __name__ == '__main__':
    unittest.main()
