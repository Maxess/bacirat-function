"""Pure checks for the installer/pairing release; distinct from the disconnected VM rehearsal."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from email.message import Message
from hashlib import sha256
import importlib.util
from io import BytesIO, StringIO
import json
from pathlib import Path
import runpy
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
    def test_fresh_memory_check_accepts_512_mib_vps_after_linux_reservations(self):
        self.assertEqual(setup.MIN_RAM_KIB, 393216)
        for total in (setup.MIN_RAM_KIB, 440000, 524288, 4194304):
            with self.subTest(total=total):
                self.assertEqual(setup.check_memory(f'MemTotal: {total} kB\nMemAvailable: 32768 kB\nSwapTotal: 0 kB\n'), total)
        with self.assertRaisesRegex(setup.Refused, 'Linux sees 393215 KiB.*swap does not count'):
            setup.check_memory('MemTotal: 393215 kB\nSwapTotal: 4194304 kB\n')
        with self.assertRaisesRegex(setup.Refused, 'Cannot determine the RAM'):
            setup.check_memory('MemAvailable: 600000 kB\n')

    def test_default_keeps_recording_or_refuses_instead_of_silently_installing_live(self):
        self.assertEqual(setup.install_profile('auto', 469200, 524284), 'compact')
        self.assertEqual(setup.install_profile('auto', 984740), 'compact')
        self.assertEqual(setup.install_profile('auto', setup.FULL_RAM_KIB - 1), 'compact')
        with self.assertRaisesRegex(setup.Refused, '512 MiB of active swap'):
            setup.install_profile('auto', 469200)
        with self.assertRaisesRegex(setup.Refused, 'not swap'):
            setup.install_profile('auto', setup.COMPACT_RAM_KIB - 1, 524284)
        self.assertEqual(setup.install_profile('auto', setup.FULL_RAM_KIB), 'full')
        self.assertEqual(setup.install_profile('full', 4194304), 'full')
        self.assertEqual(setup.install_profile('live', 4194304), 'live')
        self.assertEqual(setup.install_profile('compact', 469200, 524284), 'compact')
        self.assertEqual(setup.install_profile('compact', 984744), 'compact')
        with self.assertRaisesRegex(setup.Refused, '512 MiB of active swap'):
            setup.install_profile('compact', 469200, setup.COMPACT_SWAP_KIB - 1)
        with self.assertRaisesRegex(setup.Refused, 'not swap'):
            setup.install_profile('compact', setup.COMPACT_RAM_KIB - 1)
        with self.assertRaisesRegex(setup.Refused, 'use --profile live'):
            setup.install_profile('full', 469200)

    def test_installer_stages_the_verified_school_client_without_changing_the_frozen_zip(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            root = Path(folder)
            rec = root / 'installed-recorder'
            rec.mkdir()
            program = rec / 'releases/0.4.23'
            fix = program.with_name('0.4.23-renderer-r1')
            payload = root / 'payload'
            payload.mkdir()
            for name in (setup.REC_ASSET, setup.PATCH_ASSET):
                (payload / name).symlink_to(ARTIFACTS / name)
                self.assertEqual(sha256((payload / name).read_bytes()).hexdigest(), setup.PAYLOAD[name])
            school_client = payload / setup.SCHOOL_HTTP_ASSET
            school_client.write_bytes((ROOT / 'server/recorder/transport.py').read_bytes())
            for field, path in [('HERE', root), ('REC', rec), ('REC_PROGRAM', program), ('PATCH', fix)]:
                stack.enter_context(patch.object(setup, field, path))
            stack.enter_context(patch.object(setup.os, 'chown'))
            stack.enter_context(patch.object(setup.os, 'fchown'))
            setup.renderer_program()
            with ZipFile(ARTIFACTS / setup.REC_ASSET) as stock:
                self.assertNotEqual(stock.read('transport.py'), school_client.read_bytes())
                self.assertEqual(stock.read('recording.py'), (program / 'recording.py').read_bytes())
            self.assertEqual((program / 'transport.py').read_bytes(), school_client.read_bytes())
            school_client.write_bytes(school_client.read_bytes() + b'\n')
            with self.assertRaisesRegex(setup.Refused, 'did not match its SHA-256'):
                setup.school_http_bytes(school_client)

    def test_maintenance_uses_the_installed_transport_and_does_not_leak_replies(self):
        cfg = {'school': 'https://school.example.ir/campus'}
        keys = {'recording_secret': 'a' * 64}
        worker = 'b' * 32
        response = {'ok': True, 'release': '0.4.23', 'idle': True, 'worker_ok': True, 'native_ready': True}
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            program = Path(folder)
            transport = program / 'transport.py'
            transport.write_bytes((ROOT / 'server/recorder/transport.py').read_bytes())
            stack.enter_context(patch.object(setup, 'HERE', setup.APP))
            stack.enter_context(patch.object(setup, 'REC_PROGRAM', program))
            connect = stack.enter_context(patch.object(setup.http.client, 'HTTPSConnection'))
            conn = connect.return_value
            reply = conn.getresponse.return_value
            reply.status, reply.length, reply.getheaders.return_value = 200, 0, []
            reply.read1.side_effect = [json.dumps(response).encode(), b'']
            self.assertEqual(setup.recorder_status(cfg, keys, worker), response)
            self.assertEqual(connect.call_args.args, ('school.example.ir',))
            conn.putrequest.assert_called_once_with('POST', '/campus/plugins/school-plugins.php?plugin=school-classroom&route=recording',
                                                    skip_accept_encoding=True)
            conn.putheader.assert_any_call('User-Agent', 'Baci-Classroom-Recorder/0.4.23')
            conn.putheader.assert_any_call('Authorization', 'Bearer ' + keys['recording_secret'])
            self.assertEqual(json.loads(conn.send.call_args.args[0]), {'action': 'maintenance', 'protocol': 2, 'worker': worker, 'id': None})
            reply.status, reply.read1.side_effect = 403, [b'private server reply', b'']
            with self.assertRaises(setup.RecorderProbeFailed) as denied:
                setup.recorder_status(cfg, keys, worker)
            self.assertEqual(denied.exception.reason, 'http_403')
            self.assertNotIn('private server reply', str(denied.exception))
            transport.write_bytes(b'invalid')
            connect.reset_mock()
            with self.assertRaises(setup.RecorderProbeFailed) as invalid:
                setup.recorder_status(cfg, keys, worker)
            self.assertEqual(invalid.exception.reason, 'local_client_invalid')
            connect.assert_not_called()

    def test_online_doctor_requires_the_matching_worker_heartbeat(self):
        cfg = {'media': 'media.example.test', 'origin': 'https://school.example.test',
               'school': 'https://school.example.test', 'site_path': '', 'profile': 'compact'}
        secret, worker = 'a' * 64, 'b' * 32
        expected = {'site': cfg['school'], 'secret': secret}
        values = {setup.PAIR: {'site': cfg['school'], 'recording_secret': secret},
                  setup.SPOOL / 'worker.json': {'id': worker}, setup.REC_CFG: expected, setup.REC_CREDENTIAL: expected.copy()}
        with ExitStack() as stack:
            for name in ('local_checks', 'edge_certificate', 'check_media_dns'):
                stack.enter_context(patch.object(setup, name, return_value='certificate'))
            for name in ('pilot_deployment', 'verify_receiver'):
                stack.enter_context(patch.object(setup.media, name))
            stack.enter_context(patch.object(setup, 'json_file', side_effect=values.__getitem__))
            clock = stack.enter_context(patch.object(setup.time, 'sleep'))
            check = stack.enter_context(patch.object(setup, 'recorder_status'))
            out = stack.enter_context(redirect_stdout(StringIO()))
            waiting = {'worker_ok': True, 'native_ready': False}
            check.return_value = waiting
            self.assertFalse(setup.doctor(cfg, True))
            self.assertIn('school_recorder=paired_waiting_for_heartbeat', out.getvalue())
            clock.assert_not_called()
            check.side_effect = [waiting, {'worker_ok': True, 'native_ready': True}]
            self.assertTrue(setup.doctor(cfg, True, wait=True))
            self.assertIn('school_recorder=ready', out.getvalue())
            clock.assert_called_once_with(2)
            check.reset_mock(side_effect=True)
            values[setup.REC_CREDENTIAL] = expected | {'secret': 'c' * 64}
            self.assertFalse(setup.doctor(cfg, True))
            self.assertIn('reason=local_pairing_mismatch', out.getvalue())
            self.assertNotIn(secret, out.getvalue())
            check.assert_not_called()

    def test_compact_encoder_preserves_r1_streams_and_segment_rules(self):
        compact = runpy.run_path(str(OFFLINE / 'compact-ffmpeg.py'))['command']
        old = ['-thread_queue_size', '8', '-f', 'matroska', '-threads', '2', '-i', 'pipe:0',
               '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-threads', '2', '-filter_threads', '1',
               '-pix_fmt', 'yuv420p', '-maxrate', '640k', '-c:a', 'aac', '-f', 'segment', '-segment_time', '450',
               '-segment_format', 'mp4', '-segment_format_options', 'movflags=+faststart', 'clip-000-%03d.mp4']
        new = compact(old)
        self.assertEqual(new[0], '/usr/bin/ffmpeg')
        self.assertEqual(new[1:3], ['-thread_queue_size', '2'])
        self.assertEqual([new[i+1] for i, arg in enumerate(new) if arg == '-threads'], ['1', '1'])
        self.assertEqual(new[new.index('-tune')+1], 'zerolatency')
        self.assertEqual(new[new.index('-pix_fmt'):], old[old.index('-pix_fmt'):])
        self.assertEqual(old[1], '8')
        with self.assertRaises(ValueError):
            compact([*old, '-threads', '5'])

    def test_live_apt_and_readiness_do_not_touch_recorder_components(self):
        with patch.object(setup, 'run') as run, redirect_stdout(StringIO()):
            setup.deb_install('live')
            self.assertEqual(run.call_args.args[3:], ('ca-certificates', 'openssl', 'python3'))
            setup.deb_install('full')
            self.assertEqual(run.call_args.args[3:], ())
        with patch.object(setup, 'info', return_value='running') as info, patch.object(setup, 'json_file') as file, \
                patch.object(setup, 'probe_renderer') as renderer:
            setup.program_checks({'profile': 'live'})
            self.assertEqual([call.args[-1] for call in info.call_args_list], [setup.SFU, setup.GATE])
            file.assert_not_called()
            renderer.assert_not_called()
        self.assertTrue(setup.with_recording({}))  # v1.1 installed-state compatibility
        self.assertEqual(setup.Fresh('live').enabled, setup.LIVE_UNITS)
        self.assertEqual(setup.Fresh('full').enabled, setup.ENABLED)

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

    def test_assigned_ipv4_uses_one_ice_socket_without_widening_the_http_listener(self):
        config = setup.names('https://school.example.ir/sub', 'rtc.example.ir', '192.0.0.9')
        keys = setup.pairing(config)
        with patch.object(setup, 'local_ipv4', return_value=True):
            direct = setup.sfu_config(config, keys)
        with patch.object(setup, 'local_ipv4', return_value=False):
            forwarded = setup.sfu_config(config, keys)
        address_filter = '  ips:\n    includes: [192.0.0.9/32]\n'
        self.assertIn('bind_addresses: [127.0.0.1]', direct)
        self.assertIn("    - 'https://school.example.ir/sub" + setup.media.EVENT_ROUTE + "'", direct)
        self.assertIn(address_filter, direct)
        self.assertEqual(direct.replace(address_filter, ''), forwarded)

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
                    'check_media_dns', 'check_acme_access', 'deb_preflight', 'deb_install', 'Fresh', 'export_pair', 'cert_inputs', 'validate_cert')}
                stack.enter_context(patch.object(setup.Path, 'is_file', return_value=True))
                stack.enter_context(patch.object(setup.os, 'umask'))
                out = stack.enter_context(redirect_stdout(StringIO()))
                err = stack.enter_context(redirect_stderr(StringIO()))
                mocks['STATE'].is_file.return_value = False
                mocks['TRUST_ROOTS'].is_file.return_value = True
                mocks['fresh_preflight'].return_value = (469200, 'live')
                supplied = (b'certificate', b'key') if mode == 'files' else None
                mocks['cert_inputs'].return_value = supplied
                mocks['tls_selection'].return_value = (mode, 'cert.pem', 'key.pem')
                self.assertEqual(setup.main(flags + ['--check', '--tls', mode]), 0)
                self.assertIn(f'preflight=ok tls={mode} profile=live ram_kib=469200', out.getvalue())
                mocks['deb_preflight'].assert_called_once_with('live')
                for name in ('deb_install', 'Fresh', 'export_pair'):
                    mocks[name].assert_not_called()
                if mode == 'files':
                    mocks['check_media_dns'].assert_not_called()
                    mocks['check_acme_access'].assert_not_called()
                    mocks['validate_cert'].assert_called_once_with('new.example.ir', *supplied)
                else:
                    mocks['check_acme_access'].assert_called_once()
                    mocks['cert_inputs'].assert_not_called()
                out.truncate(0)
                out.seek(0)
                mocks['deb_preflight'].side_effect = setup.Refused('Offline APT plan conflict')
                self.assertEqual(setup.main(flags + ['--check', '--tls', mode]), 1)
                self.assertNotIn('preflight=ok', out.getvalue())
                mocks['deb_install'].assert_not_called()
                mocks['Fresh'].assert_not_called()
                txn = mocks['Fresh'].return_value
                txn.apply.side_effect = setup.Refused('untrusted media TLS')
                self.assertEqual(setup.main(flags + ['--tls', mode]), 1)
                config, actual = txn.apply.call_args.args
                self.assertEqual((config['tls_mode'], config['profile'], actual), (mode, 'live', supplied))
                mocks['deb_install'].assert_called_once_with('live')
                mocks['Fresh'].assert_called_once_with('live')
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

    def test_compact_install_has_recording_and_rolls_back_only_addon_units(self):
        self.assertTrue(setup.with_recording({'profile': 'compact'}))
        self.assertEqual(setup.Fresh('compact').enabled, setup.ENABLED)
        addition = setup.RecorderAddition('compact')
        addition.starting = addition.enabling = True
        with patch.object(setup, 'run') as run:
            addition.cleanup()
        self.assertEqual([x.args for x in run.call_args_list], [
            ('systemctl', 'disable', setup.RECORDER),
            ('systemctl', 'stop', setup.RECORDER, setup.RENDERER),
            ('systemctl', 'daemon-reload'),
        ])

    def test_add_recorder_requires_explicit_path_and_check_is_read_only(self):
        with ExitStack() as stack:
            patcher = lambda name, **kwargs: stack.enter_context(patch.object(setup, name, **kwargs))
            patcher('system_preflight')
            patcher('STATE').is_file.return_value = True
            config = {'profile': 'live', 'version': '1.2.0'}
            patcher('json_file', return_value=config)
            patcher('doctor', return_value=True)
            preflight = patcher('addition_preflight', return_value=('compact', {'recording_secret': 'not-logged'}, 469200))
            apt_check = patcher('deb_preflight')
            apt = patcher('deb_install')
            txn = patcher('RecorderAddition', wraps=setup.RecorderAddition)
            stack.enter_context(patch.object(setup.os, 'umask'))
            stdout = stack.enter_context(redirect_stdout(StringIO()))
            stack.enter_context(redirect_stderr(StringIO()))
            self.assertEqual(setup.main(['--profile', 'compact']), 1)
            self.assertIn('not an in-place upgrade', stdout.getvalue())
            preflight.assert_not_called()
            self.assertEqual(setup.main(['--add-recorder', '--check', '--site', 'https://other.example.ir']), 1)
            preflight.assert_not_called()
            self.assertEqual(setup.main(['--add-recorder', '--check']), 0)
            self.assertEqual(preflight.call_args.args[:2], (config, 'auto'))
            self.assertIn('add_recorder_preflight=ok profile=compact', stdout.getvalue())
            apt_check.assert_called_once_with('compact', addition=True)
            self.assertNotIn('not-logged', stdout.getvalue())
            txn.assert_not_called()
            apt.assert_not_called()

    def test_v131_live_is_still_eligible_for_the_guarded_addon(self):
        class ReachedMemoryCheck(Exception):
            pass

        with patch.object(setup.Path, 'read_text', side_effect=ReachedMemoryCheck):
            for version in ('1.2.0', '1.3.0', '1.3.1', setup.VERSION):
                with self.subTest(version=version), self.assertRaises(ReachedMemoryCheck):
                    setup.addition_preflight({'version': version, 'profile': 'live', 'tls_mode': 'files'}, 'auto', 'b' * 32)
            with self.assertRaisesRegex(setup.Refused, 'only for a completed Baci offline live install'):
                setup.addition_preflight({'version': '1.3.1', 'profile': 'compact', 'tls_mode': 'files'}, 'auto', 'b' * 32)

    def test_add_recorder_fences_both_the_sfu_and_school(self):
        inputs = {'school': 'https://school.example.ir'}
        with patch.object(setup.media, 'pilot_deployment', return_value={}), \
                patch.object(setup.media, 'local_rooms', return_value=[{}]) as rooms, \
                patch.object(setup, 'recorder_status') as status:
            with self.assertRaisesRegex(setup.Refused, 'still has rooms'):
                setup.recorder_idle(inputs, {}, '')
            status.assert_not_called()
            rooms.return_value = []
            for idle, worker_ok in [(False, True), (True, False), (True, True)]:
                status.return_value = {'idle': idle, 'worker_ok': worker_ok}
                if idle and worker_ok:
                    setup.recorder_idle(inputs, {}, '')
                else:
                    with self.assertRaisesRegex(setup.Refused, 'open lesson'):
                        setup.recorder_idle(inputs, {}, '')

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

    def test_frozen_vps_payload_and_pairing_companion(self):
        for name in (setup.REC_ASSET, setup.PATCH_ASSET):
            self.assertEqual(sha256((ARTIFACTS / name).read_bytes()).hexdigest(), setup.PAYLOAD[name])
        # The installer keeps the original 1.3.2 pairing bytes, independent of future site-only updates.
        self.assertEqual(sha256((ARTIFACTS / build.SITE).read_bytes()).hexdigest(),
                         '9d2c3545558a0bec1e698b6bb54cf03a40b7c26db207eeb5442c6f3b525d8237')
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
                self.assertIn(b"const MPM_VERSION = '0.1.31';", updater.read(GATEWAY))
                with ZipFile(BytesIO(site.read(CLASSROOM))) as plugin:
                    digest = sha256(plugin.read('plugin.json')).hexdigest()
                self.assertIn(f"'0.4.24' => '{digest}'".encode('ascii'), updater.read(GATEWAY))

    def test_shell_syntax_and_isolation(self):
        for path in (OFFLINE / 'install.sh', OFFLINE / 'apt-local.sh', ROOT / 'scripts/test-vps-offline-vm.sh'):
            subprocess.run(['bash', '-n', path], check=True)
        apt = (OFFLINE / 'apt-local.sh').read_text()
        self.assertIn('file:%s/apt', apt)
        self.assertIn('Dir::Etc::sourceparts "$work/empty";', apt)
        self.assertIn('unshare --net', apt)
        self.assertIn('--no-remove', apt)
        self.assertNotIn('--allow-downgrades', apt)
        self.assertIn("'--no-index'", (OFFLINE / 'setup.py').read_text())


if __name__ == '__main__':
    unittest.main()
