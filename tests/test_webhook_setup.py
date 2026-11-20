"""Exercise the offline safety of the one-shot VPS webhook registration."""
import base64
from contextlib import redirect_stderr, redirect_stdout
from email.message import Message
from http.client import IncompleteRead
import hashlib
import hmac
import importlib.util
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import BaseHandler, build_opener
from urllib.response import addinfourl

SPEC = importlib.util.spec_from_file_location('school_media_install', Path(__file__).resolve().parents[1] / 'server/install.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)

DEPLOYMENT = {'school_origin': 'https://school.fixture.test', 'api_key': 'FixtureMediaKey12345', 'api_secret': 'FixtureTest_' * 4}
# The stock pre-native-hooks layout, as installed by earlier pilot releases.
STOCK_YAML = '''port: 7880
bind_addresses: [127.0.0.1]
rtc:
  node_ip: 203.0.113.50
  use_external_ip: false
  udp_port: 7882
  tcp_port: 7881
room:
  auto_create: false
  max_participants: 4
  empty_timeout: 300
  departure_timeout: 20
  enabled_codecs:
    - mime: audio/opus
turn:
  enabled: false
keys:
  "{api_key}": "{api_secret}"
logging:
  level: warn
'''.format(**DEPLOYMENT)


class WebhookSetupTest(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(temp).resolve()
        self.config_dir = root / 'cfg'
        self.config_dir.mkdir(mode=0o755)
        self.systemd_dir = root / 'systemd'
        self.systemd_dir.mkdir()
        self.enterContext(patch.multiple(installer, CFG=self.config_dir, APP=root / 'app', SYSTEMD=self.systemd_dir))
        self.enterContext(patch.object(installer, 'require_media_sudo'))
        self.enterContext(patch.object(installer.os, 'fchown'))  # File ownership is still checked in the temporary fixture.
        self.path = self.config_dir / 'livekit.yaml'
        self.base = STOCK_YAML
        self.write_private(self.path, self.base)
        self.write_private(self.config_dir / 'admission.json', json.dumps(DEPLOYMENT))
        self.service = self.systemd_dir / installer.SFU_SERVICE
        self.service.write_text(installer.sfu_unit_config(legacy=True))
        self.service.chmod(0o644)
        self.output = self.enterContext(redirect_stdout(StringIO()))
        # Unless explicitly testing local restart, keep the fixtures completely offline.
        self.real_local_rooms = installer.local_rooms
        self.rpc = self.enterContext(patch.object(installer, 'local_rooms', side_effect=AssertionError('unexpected local API call')))
        self.systemctl = self.enterContext(patch.object(installer.subprocess, 'run', side_effect=AssertionError('unexpected systemctl')))

    def write_private(self, path, data):
        path.write_text(data, encoding='utf-8')
        path.chmod(0o640)

    def webhook(self, **kwargs):
        options = {'apply': False, 'restart': False, 'verify': False, 'site_path': None} | kwargs
        installer.configure_webhook(SimpleNamespace(**options))

    def backups(self):
        return sorted(self.config_dir.glob('livekit.yaml.before-webhook-*.bak'))

    def systemctl_idle(self, rooms):
        self.systemctl.side_effect = None
        self.systemctl.return_value = SimpleNamespace(stdout=f'FragmentPath={self.service}\nDropInPaths=\nActiveState=active\nNeedDaemonReload=no\n')
        self.rpc.side_effect = None
        self.rpc.return_value = rooms

    def test_preview_is_read_only_and_constructs_correct_url(self):
        self.webhook(site_path='/school-test')
        self.assertIn('https://school.fixture.test/school-test/plugins/school-plugins.php?plugin=school-classroom&route=events', self.output.getvalue())
        self.assertIn('webhook_configured=no applied=no restarted=no', self.output.getvalue())
        self.assertEqual(self.path.read_text(), self.base)
        self.assertEqual(self.backups(), [])
        self.assertNotIn(DEPLOYMENT['api_key'], self.output.getvalue())
        self.assertNotIn(DEPLOYMENT['api_secret'], self.output.getvalue())

    def test_apply_is_atomic_and_idempotent(self):
        self.webhook(apply=True, site_path='/school-test')
        new, url = installer.registered_config(self.base, DEPLOYMENT, '/school-test')
        self.assertEqual(self.path.read_text(), new)
        self.assertIn("    - '" + url + "'", new)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].read_text(), self.base)
        self.assertEqual(stat.S_IMODE(self.backups()[0].stat().st_mode), 0o600)
        inode = self.path.stat().st_ino
        self.webhook(apply=True)  # Leaving out the path does not retarget an existing nested site.
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertEqual(len(self.backups()), 1)
        self.assertIn('changed=no restarted=no', self.output.getvalue())
        self.webhook(apply=True, site_path='/next-test')
        self.assertEqual(self.path.read_text().count('\nwebhook:\n'), 1)
        self.assertIn('https://school.fixture.test/next-test/', self.path.read_text())
        self.assertEqual(len(self.backups()), 2)
        self.webhook(apply=True, site_path=installer.site_path('/'))
        self.assertIn('https://school.fixture.test/plugins/', self.path.read_text())

    def test_refuses_unknown_or_foreign_configuration_and_unsafe_files(self):
        foreign, _ = installer.registered_config(self.base, DEPLOYMENT, '/fixture')
        for other in [self.base + 'webhook: {}\n', self.base.replace('max_participants: 4', 'max_participants: 12'),
                      foreign.replace('school.fixture.test', 'other.fixture.test'), foreign.replace('list_participants: true', 'list_participants: false')]:
            self.write_private(self.path, other)
            with self.assertRaises(ValueError):
                self.webhook(apply=True)
            self.assertEqual(self.path.read_text(), other)
        self.assertEqual(self.backups(), [])
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.webhook(apply=True)
        self.path.unlink()
        linked = self.config_dir / 'elsewhere'
        linked.write_text(self.base)
        self.path.symlink_to(linked)
        with self.assertRaises(OSError):
            self.webhook(apply=True)
        self.assertEqual(linked.read_text(), self.base)
        self.path.unlink()
        self.write_private(linked, self.base)
        os.link(linked, self.path)
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            self.webhook(apply=True)

    def test_does_not_replace_config_edited_during_preflight(self):
        def edit_after_read(*_args):
            self.write_private(self.path, self.base + '# concurrent change\n')
            return {'ok': True}
        with patch.object(installer, 'post_json', side_effect=edit_after_read):
            with self.assertRaisesRegex(ValueError, 'changed concurrently'):
                self.webhook(verify=True, apply=True)
        self.assertEqual(self.path.read_text(), self.base + '# concurrent change\n')
        self.assertEqual(list(self.config_dir.glob('.livekit-webhook-*')), [])

    def test_busy_or_overridden_service_refuses_before_write(self):
        self.systemctl_idle([{'name': 'existing-class'}])
        with self.assertRaisesRegex(ValueError, 'open rooms'):
            self.webhook(apply=True, restart=True)
        for before, after in [('DropInPaths=\n', 'DropInPaths=/etc/a.conf\n'),
                              ('NeedDaemonReload=no\n', 'NeedDaemonReload=yes\n')]:
            self.systemctl_idle([])
            self.systemctl.return_value.stdout = self.systemctl.return_value.stdout.replace(before, after)
            with self.assertRaisesRegex(ValueError, 'up-to-date pilot unit'):
                self.webhook(apply=True, restart=True)
        self.assertEqual(self.path.read_text(), self.base)
        self.assertEqual(self.backups(), [])
        self.assertTrue(all(call.args[0][1] == 'show' for call in self.systemctl.call_args_list))

    def test_restart_only_the_named_pilot_and_recheck_local_api(self):
        self.systemctl_idle([])
        self.webhook(apply=True, restart=True)
        self.assertEqual(self.systemctl.call_args.args[0], ['systemctl', 'restart', installer.SFU_SERVICE])
        self.assertEqual(self.rpc.call_count, 2)
        self.assertIn('changed=yes restarted=yes', self.output.getvalue())

    def test_restart_failure_restores_previous_file_and_service(self):
        self.systemctl_idle([])
        show = self.systemctl.return_value
        self.systemctl.side_effect = [show, subprocess.CalledProcessError(1, ['systemctl', 'restart']), None]
        with self.assertRaisesRegex(ValueError, 'previous configuration and service restored'):
            self.webhook(apply=True, restart=True)
        self.assertEqual(self.path.read_text(), self.base)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual([call.args[0][1] for call in self.systemctl.call_args_list], ['show', 'restart', 'restart'])

    def test_camera_keeps_webhook_and_can_be_applied_only_to_stock_layouts(self):
        self.webhook(apply=True, site_path='/school-test')
        before = self.path.read_text()
        self.assertNotIn('video/VP8', before, 'a webhook update must not enable video by itself')
        installer.main(['camera'])
        self.assertIn('camera_configured=no applied=no restarted=no', self.output.getvalue())
        self.assertEqual(self.path.read_text(), before)
        self.systemctl_idle([{'name': 'active-lesson'}])
        with self.assertRaisesRegex(ValueError, 'open rooms'):
            installer.configure_camera(SimpleNamespace(apply=True, restart=True))
        self.assertEqual(self.path.read_text(), before)
        self.systemctl_idle([])
        installer.configure_camera(SimpleNamespace(apply=True, restart=True))
        after = self.path.read_text()
        self.assertEqual(after, before.replace('    - mime: audio/opus\n', '    - mime: audio/opus\n    - mime: video/VP8\n'))
        self.assertEqual(self.systemctl.call_args.args[0], ['systemctl', 'restart', installer.SFU_SERVICE])
        backups = list(self.config_dir.glob('livekit.yaml.before-camera-*.bak'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), before)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        installer.main(['camera', '--apply'])
        self.assertEqual(self.path.read_text(), after)
        self.assertEqual(len(list(self.config_dir.glob('livekit.yaml.before-camera-*.bak'))), 1)
        self.webhook(apply=True, site_path='/next-test')
        self.assertEqual(self.path.read_text(), after.replace('/school-test/', '/next-test/'))
        unknown = after + '# local changes\n'
        self.write_private(self.path, unknown)
        with self.assertRaises(ValueError):
            installer.configure_camera(SimpleNamespace(apply=True, restart=False))
        self.assertEqual(self.path.read_text(), unknown)

    def test_camera_restart_failure_restores_audio_configuration(self):
        self.systemctl_idle([])
        self.systemctl.side_effect = [self.systemctl.return_value, subprocess.CalledProcessError(1, ['systemctl', 'restart']), None]
        with self.assertRaisesRegex(ValueError, 'camera change failed; previous configuration and service restored'):
            installer.configure_camera(SimpleNamespace(apply=True, restart=True))
        self.assertEqual(self.path.read_text(), self.base)
        self.assertEqual([call.args[0][1] for call in self.systemctl.call_args_list], ['show', 'restart', 'restart'])

    def test_new_pilot_enables_vp8_but_no_other_settings_change(self):
        stock = installer.engine_config('203.0.113.50', DEPLOYMENT['api_key'], DEPLOYMENT['api_secret'])
        self.assertEqual(stock.replace('    - mime: video/VP8\n', ''), self.base)
        after, _ = installer.registered_config(stock, DEPLOYMENT, '/school-test')
        self.assertTrue(after.startswith(stock + installer.WEBHOOK_MARKER))
        self.assertEqual(installer.camera_config(after, DEPLOYMENT), after)

    def test_signed_probe_binds_body_without_room_side_effects(self):
        def check(url, body, mime, auth, timeout):
            self.assertTrue(url.startswith(DEPLOYMENT['school_origin'] + '/plugins/'))
            self.assertEqual(mime, 'application/webhook+json')
            self.assertEqual(json.loads(body), {'event': 'school_classroom_webhook_check'})
            head, payload, sig = auth.split('.')  # LiveKit sends a raw token, not Bearer, for webhooks.
            def decode(value):
                return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
            claims = json.loads(decode(payload))
            self.assertEqual(claims['iss'], DEPLOYMENT['api_key'])
            self.assertEqual(claims['sha256'], base64.b64encode(hashlib.sha256(body).digest()).decode())
            self.assertEqual(decode(sig), hmac.digest(DEPLOYMENT['api_secret'].encode(), (head + '.' + payload).encode(), 'sha256'))
            return {'ok': True}
        with patch.object(installer, 'post_json', side_effect=check) as post:
            self.webhook(verify=True)
            self.assertEqual(post.call_count, 1)
        self.assertIn('webhook_endpoint_verified=yes', self.output.getvalue())
        self.assertEqual(self.backups(), [])

    def test_wrong_or_unreachable_endpoint_does_not_change_config(self):
        with patch.object(installer, 'post_json', side_effect=HTTPError('https://school.fixture.test', 403, 'Forbidden', {}, BytesIO())):
            with self.assertRaisesRegex(ValueError, 'signed probe'):
                self.webhook(verify=True, apply=True)
        self.assertEqual(self.path.read_text(), self.base)
        self.assertEqual(self.backups(), [])

    def test_unexpected_json_does_not_verify(self):
        for result in [{'ok': False}, {'ok': 1}, {'ok': True, 'page': 'not the endpoint'}, {}]:
            with self.subTest(result=result), patch.object(installer, 'post_json', return_value=result):
                with self.assertRaisesRegex(ValueError, 'did not confirm'):
                    self.webhook(verify=True, apply=True)
        self.assertEqual(self.path.read_text(), self.base)

    def test_redirect_is_not_followed_with_authorization(self):
        seen = []

        class Origin(BaseHandler):
            handler_order = 100

            def http_open(self, request):
                seen.append((request.full_url, request.get_header('Authorization')))
                headers = Message()
                headers['Location'] = 'http://elsewhere.fixture.test/not-the-webhook'
                reply = addinfourl(BytesIO(), headers, request.full_url, 302)
                reply.msg = 'Found'
                return reply

        url = 'http://school.fixture.test/plugin'
        with patch.object(installer, 'build_opener', side_effect=lambda *args: build_opener(*args, Origin())):
            with self.assertRaises(HTTPError) as error:
                installer.post_json(url, b'{}', 'application/webhook+json', 'fixture-auth', 1)
        self.assertEqual(error.exception.code, 302)
        self.assertEqual(seen, [(url, 'fixture-auth')])

    def test_list_rooms_uses_read_only_grant_and_strict_twirp_shape(self):
        with patch.object(installer, 'post_json') as post:
            for result in [{}, {'rooms': []}]:
                post.return_value = result
                self.assertEqual(self.real_local_rooms(DEPLOYMENT), [])
            url, body, mime, authorization, _timeout = post.call_args.args
            self.assertEqual(url, 'http://127.0.0.1:7880/twirp/livekit.RoomService/ListRooms')
            self.assertEqual((body, mime), (b'{}', 'application/json'))
            self.assertTrue(authorization.startswith('Bearer '))
            claims = json.loads(base64.urlsafe_b64decode(authorization.split('.')[1] + '=='))
            self.assertEqual(claims['video'], {'roomList': True})
            for result in [{'rooms': None}, {'rooms': {}}, {'error': 'not this API'}]:
                post.return_value = result
                with self.assertRaisesRegex(ValueError, 'Invalid LiveKit room list'):
                    self.real_local_rooms(DEPLOYMENT)

    def test_invalid_subpaths_refused(self):
        for path in ['//elsewhere.test', 'https://elsewhere.test', '/../other', '/nested/', '/test?route=events', '/te%73t', '/test\n']:
            with self.subTest(path=path), self.assertRaises(installer.argparse.ArgumentTypeError):
                installer.site_path(path)

    def test_cli_requires_opt_in(self):
        with redirect_stderr(StringIO()) as errors, self.assertRaises(SystemExit) as exit_code:
            installer.main(['webhook', '--restart'])
        self.assertEqual(exit_code.exception.code, 2)
        self.assertIn('--restart requires --apply', errors.getvalue())
        self.assertEqual(self.path.read_text(), self.base)
        with patch.object(installer, 'install') as install:
            installer.main(['media.fixture.test', '8.8.8.8', DEPLOYMENT['school_origin'], '--webhook', '--site-path', '/school-test'])
        self.assertTrue(install.call_args.args[0].webhook)
        self.assertEqual(install.call_args.args[0].site_path, '/school-test')

    def test_incomplete_http_response_fails_closed(self):
        with patch.object(installer, 'build_opener') as opener:
            opener.return_value.open.side_effect = IncompleteRead(b'partial')
            with self.assertRaisesRegex(ValueError, 'Incomplete server response'):
                installer.post_json('https://school.fixture.test', b'{}', 'application/json', 'opaque-test', 1)


if __name__ == '__main__':
    unittest.main()
