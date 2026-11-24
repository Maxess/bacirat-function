"""Pure checks for the installer/pairing release; distinct from the disconnected VM rehearsal."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from email.message import Message
from hashlib import sha256
import importlib.util
from io import BytesIO, StringIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
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

    def test_auto_tls_preserves_routing_and_limits_issuance_to_acme_on_443(self):
        config = setup.names('https://school.example.ir/sub', 'new.example.ir', '192.0.0.9') | {'tls_mode': 'auto'}
        caddy = setup.edge_config(config)
        for line in ('admin off', 'auto_https disable_redirects', 'issuer acme {', 'disable_http_challenge',
                     f'dir {setup.ACME_DIRECTORY}', 'bind 0.0.0.0', 'protocols h1 h2'):
            self.assertIn(line, caddy)
        for line in ('fullchain.pem', 'tls internal', 'on_demand', 'disable_tlsalpn_challenge', 'auto_https off'):
            self.assertNotIn(line, caddy)
        self.assertIn('StateDirectory=school-classroom-https\nStateDirectoryMode=0700', setup.edge_unit())
        self.assertIn(f'XDG_DATA_HOME={setup.HTTPS_STATE}', setup.edge_unit())
        self.assertEqual(caddy[caddy.index('    @classroomPreflight'):],
                         setup.media.caddy_config(config['media'], config['origin']).split(' {\n', 1)[1])

    def test_explicit_and_inferred_tls_modes_never_mix(self):
        for files in (set(), {'fullchain.pem'}, {'fullchain.pem', 'privkey.pem'}):
            with self.subTest(files=files), patch.object(setup.os.path, 'lexists', side_effect=lambda p: str(p) in files):
                self.assertEqual(setup.tls_selection('auto', None, None), ('auto', None, None))
                if not files:
                    self.assertEqual(setup.tls_selection(None, None, None), ('auto', None, None))
                elif len(files) == 2:
                    self.assertEqual(setup.tls_selection(None, None, None), ('files', Path('fullchain.pem'), Path('privkey.pem')))
                else:
                    with self.assertRaisesRegex(setup.Refused, 'incomplete'):
                        setup.tls_selection(None, None, None)
        with patch.object(setup.os.path, 'lexists', return_value=False), self.assertRaisesRegex(setup.Refused, 'incomplete'):
            setup.tls_selection('files', None, None)
        with self.assertRaisesRegex(setup.Refused, 'cannot be combined'):
            setup.tls_selection('auto', Path('cert.pem'), None)
        with patch.object(setup.os.path, 'lexists', return_value=True):
            self.assertEqual(setup.tls_selection(None, Path('cert.pem'), None), ('files', Path('cert.pem'), Path('privkey.pem')))

    def test_media_dns_requires_exact_single_ipv4(self):
        config = {'media': 'new.example.ir', 'ipv4': '192.0.0.9'}
        def record(address):
            return (None, None, None, '', (address, 443))
        with patch.object(setup.socket, 'getaddrinfo', return_value=[record('192.0.0.9')] * 2):
            setup.check_media_dns(config)
        for addresses in [[], ['192.0.0.10'], ['192.0.0.9', '2001:db8::1'], ['192.0.0.9', '192.0.0.10']]:
            with self.subTest(addresses=addresses), self.assertRaisesRegex(setup.Refused, 'Media DNS is not ready'), \
                    patch.object(setup.socket, 'getaddrinfo', return_value=[record(ip) for ip in addresses]):
                setup.check_media_dns(config)
        with patch.object(setup.socket, 'getaddrinfo', side_effect=OSError), self.assertRaises(setup.Refused):
            setup.check_media_dns(config)

    def test_acme_preflight_is_a_bounded_verified_directory_get(self):
        headers = Message()
        headers['Content-Type'] = 'application/json'
        response = Mock(status=200, headers=headers)
        response.read.return_value = b'{"newNonce":"https://a.local/n","newAccount":"https://a.local/a","newOrder":"https://a.local/o"}'
        for body, exception in [(response.read.return_value, False), (b'{}', True), (b'{' * 16385, True)]:
            response.read.return_value = body
            with patch.object(setup, 'tls_context') as ctx, patch.object(setup, 'build_opener') as factory:
                opener = factory.return_value
                opener.open.return_value.__enter__.return_value = response
                if exception:
                    with self.assertRaisesRegex(setup.Refused, 'Cannot reach the trusted'):
                        setup.check_acme_access()
                else:
                    setup.check_acme_access()
                self.assertEqual(factory.call_args.args[0].proxies, {})
                self.assertIsInstance(factory.call_args.args[1], setup.media.NoRedirect)
                self.assertIs(factory.call_args.args[2]._context, ctx.return_value)
                request = opener.open.call_args.args[0]
                self.assertEqual((request.full_url, request.get_method(), request.data), (setup.ACME_DIRECTORY, 'GET', None))
                self.assertEqual(opener.open.call_args.kwargs, {'timeout': 7})
                response.read.assert_called_with(16385)

    def test_check_never_writes_and_tls_failure_never_exports_a_pair(self):
        flags = ['--site', 'https://school.example.ir/sub', '--media', 'new.example.ir', '--ipv4', '192.0.0.9']
        for mode in ('auto', 'files'):
            with self.subTest(mode=mode), ExitStack() as stack:
                mocks = {attr: stack.enter_context(patch.object(setup, attr)) for attr in (
                    'system_preflight', 'fresh_preflight', 'verify_payloads', 'tls_selection', 'STATE', 'TRUST_ROOTS',
                    'check_media_dns', 'check_acme_access', 'deb_install', 'Fresh', 'export_pair', 'cert_inputs', 'validate_cert')}
                stack.enter_context(patch.object(setup.Path, 'is_file', return_value=True))
                stack.enter_context(patch.object(setup.os, 'umask'))
                out = stack.enter_context(redirect_stdout(StringIO()))
                err = stack.enter_context(redirect_stderr(StringIO()))
                mocks['STATE'].is_file.return_value = False
                mocks['TRUST_ROOTS'].is_file.return_value = True
                supplied = (b'certificate', b'key') if mode == 'files' else None
                mocks['cert_inputs'].return_value = supplied
                mocks['tls_selection'].return_value = (mode, 'cert.pem', 'key.pem')
                self.assertEqual(setup.main(flags + ['--check', '--tls', mode]), 0)
                self.assertIn(f'preflight=ok tls={mode}', out.getvalue())
                for name in ('deb_install', 'Fresh', 'export_pair'):
                    mocks[name].assert_not_called()
                if mode == 'files':
                    mocks['check_media_dns'].assert_not_called()
                    mocks['check_acme_access'].assert_not_called()
                    mocks['validate_cert'].assert_called_once_with('new.example.ir', *supplied)
                else:
                    mocks['check_acme_access'].assert_called_once()
                    mocks['cert_inputs'].assert_not_called()
                txn = mocks['Fresh'].return_value
                txn.apply.side_effect = setup.Refused('untrusted media TLS')
                self.assertEqual(setup.main(flags + ['--tls', mode]), 1)
                config, actual = txn.apply.call_args.args
                self.assertEqual((config['tls_mode'], actual), (mode, supplied))
                self.assertNotIn('installation=ok', out.getvalue())
                self.assertIn('untrusted media TLS', err.getvalue())
                txn.cleanup.assert_called_once()
                mocks['export_pair'].assert_not_called()

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
