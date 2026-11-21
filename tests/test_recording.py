"""Recorder delivery/retention. Provider replies are simulated; never send live messages."""
import json
import socket
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server/recorder'))
from transport import ProtocolError, School, post, send_video
from recording import Runner, atomic


def reply(data, status=200):
    return status, {}, json.dumps(data).encode()


class BotTest(unittest.TestCase):
    def setUp(self):
        self.bale = {'provider': 'bale', 'prefix': 'https://tapi.bale.ai/botfixture/', 'chat': '-123', 'caption': 'فیلم کلاس'}
        self.rubika = {'provider': 'rubika', 'prefix': 'https://botapi.rubika.ir/v3/fixture/', 'chat': 'g0aabb', 'caption': 'فیلم کلاس'}
        self.path = Path('test-fixture.mp4')

    def test_bale_and_telegram_use_exact_group_and_message_receipts(self):
        for permit in (self.bale, self.bale | {'provider': 'telegram', 'prefix': 'https://api.telegram.org/botfixture/'}):
            calls = []

            def send(url, fields, key, path):
                calls.append((url, fields['chat_id'], key, path))
                return reply({'ok': True, 'result': {'message_id': 391, 'chat': {'id': -123}}})

            self.assertEqual(send_video(permit, self.path, upload=send), ('accepted', '391'))
            self.assertEqual(calls, [(permit['prefix'] + 'sendVideo', '-123', 'video', self.path)])
        for result in ({'ok': True}, {'ok': True, 'result': {'message_id': 9, 'chat': {'id': -67}}}):
            self.assertEqual(send_video(self.bale, self.path, upload=lambda *_: reply(result)), ('uncertain', ''))
        self.assertEqual(send_video(self.bale, self.path, upload=lambda *_: reply({'ok': False}, 403)), ('rejected', ''))
        self.assertEqual(send_video(self.bale, self.path, upload=lambda *_: reply({'ok': False}, 429)), ('retry', ''))

    def test_rubika_stages_privately_then_sends_file_once(self):
        calls = []

        def post(url, data):
            calls.append((url, data))
            if url.endswith('requestSendFile'):
                return reply({'status': 'OK', 'data': {'upload_url': 'https://upload.rubika.ir/fixture'}})
            return reply({'status': 'OK', 'data': {'message_id': '0473'}})

        def upload(url, fields, key, path):
            calls.append((url, fields, key, path))
            return reply({'data': {'file_id': 'moviefile123'}})

        self.assertEqual(send_video(self.rubika, self.path, post_json=post, upload=upload), ('accepted', '0473'))
        self.assertEqual(calls, [
            (self.rubika['prefix'] + 'requestSendFile', {'type': 'Video'}),
            ('https://upload.rubika.ir/fixture', {}, 'file', self.path),
            (self.rubika['prefix'] + 'sendFile', {'chat_id': 'g0aabb', 'file_id': 'moviefile123', 'text': 'فیلم کلاس'}),
        ])
        calls.clear()
        self.assertEqual(send_video(self.rubika, self.path, post_json=post, upload=lambda *_: reply({'error': 'down'}, 503)), ('retry', ''))
        self.assertEqual(len(calls), 1, 'upload failure cannot produce a group post')

    def test_incomplete_http_reply_is_not_confirmed(self):
        # Exercise HTTPResponse's real Content-Length handling, using a socketpair, not a bot.
        left, right = socket.socketpair()
        with left, right:
            right.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{"ok":true}')
            right.shutdown(socket.SHUT_WR)
            with patch('transport.http.client.HTTPSConnection.connect', lambda conn: setattr(conn, 'sock', left)):
                with self.assertRaises(ProtocolError):
                    post('https://school.test.invalid/', {}, [], limit=1024, seconds=2)

    def test_no_redirection_or_alternate_upload_hosts(self):
        for unsafe in ('https://unrelated.test/video', 'http://upload.rubika.ir/', 'https://upload.rubika.ir@unrelated.test/',
                       'https://upload.rubika.ir.unrelated.test/', 'https://127.0.0.1/fixture'):
            result = send_video(self.rubika, self.path,
                                post_json=lambda *_: reply({'status': 'OK', 'data': {'upload_url': unsafe}}),
                                upload=lambda *_: self.fail('do not upload to an untrusted host'))
            self.assertNotEqual(result[0], 'accepted')
        self.assertTrue(School('https://test.example.ir/baci', 'secret', 'a'*32).url.startswith('https://test.example.ir/baci/'))
        for unsafe in ('http://test.example.ir', 'https://test.example.ir?abc', 'https://test.example.ir/../other'):
            with self.assertRaises(ProtocolError):
                School(unsafe, 'secret', 'a'*32)


class SpoolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.job = 'a' * 32
        self.folder = self.root / self.job
        self.folder.mkdir()
        self.clip = self.folder / 'clip-000-000.mp4'
        self.clip.write_bytes(b'\0\0\0\x18ftypTEST')

    async def test_receipt_survives_busy_site_and_restart_without_resending(self):
        accepts = []
        committed = False
        ready_to_commit = False

        def school(action, job, **data):
            nonlocal committed
            self.assertEqual(job, self.job)
            if action == 'poll':
                return {'command': 'purge' if committed else 'deliver', 'id': self.job, 'sent': 0}
            if action == 'claim':
                self.assertEqual(data, {'part': 1, 'total': 1})
                return {'command': 'send', 'attempt': 'c'*32}
            if action == 'result':
                if not ready_to_commit:
                    raise ProtocolError(409)  # Class lock temporarily busy.
                self.assertEqual(data['message'], '331')
                committed = True
            if action == 'cleaned':
                self.assertTrue(committed)
                self.assertFalse(self.clip.exists(), 'media already removed after confirmation')
            return {}

        def accepted(permit, path):
            accepts.append(path)
            return 'accepted', '331'

        with patch('recording.send_video', side_effect=accepted):
            first = Runner(school, self.root, None, None, None)
            with self.assertRaises(ProtocolError):
                await first.tick()
            self.assertTrue((self.folder / 'receipt.json').exists())
            self.assertFalse(self.clip.exists(), 'all provider receipts were confirmed; do not retain video on PHP failure')
            ready_to_commit = True
            restarted = Runner(school, self.root, None, None, None)
            await restarted.tick()
        self.assertEqual(accepts, [self.clip])
        self.assertFalse(self.folder.exists())

    async def test_each_accepted_part_is_unlinked_with_site_offline_but_unconfirmed_part_stays(self):
        part2 = self.folder / 'clip-000-001.mp4'
        part3 = self.folder / 'clip-001-000.mp4'
        for path in (part2, part3):
            path.write_bytes(self.clip.read_bytes())
        sent, unavailable, terminal = 0, True, False
        claims, uploads = [], []

        def school(action, job, **data):
            nonlocal sent, terminal
            self.assertEqual(job, self.job)
            if action == 'poll':
                return {'command': 'retain' if terminal else 'deliver', 'id': job, 'sent': sent}
            if action == 'claim':
                claims.append(data)
                self.assertEqual(data, {'part': sent + 1, 'total': 3})
                return {'command': 'send', 'attempt': str(sent + 1) * 32}
            if action == 'result':
                self.assertFalse(self.clip.exists())
                if unavailable:
                    raise ProtocolError(503)
                if data['result'] == 'accepted':
                    sent = data['part']
                    self.assertFalse([self.clip, part2, part3][sent-1].exists())
                else:
                    terminal = True
            return {}

        def send(permit, file):
            uploads.append(file)
            return ('accepted', '708') if file != part3 else ('uncertain', '')

        with patch('recording.send_video', side_effect=send):
            with self.assertRaises(ProtocolError):
                await Runner(school, self.root, None, None, None).tick()
            self.assertFalse(self.clip.exists(), 'a sent part cannot be left behind while the rest waits')
            self.assertTrue(part2.exists() and part3.exists())
            self.assertTrue((self.folder / 'receipt.json').exists())
            unavailable = False
            restarted = Runner(school, self.root, None, None, None)
            await restarted.tick()  # replay receipt, then claim part 2 with the original total
            await restarted.tick()  # part 3 has no provider receipt
            await restarted.tick()  # retain only unsent/unconfirmed media
        self.assertEqual(claims, [{'part':n,'total':3} for n in (1,2,3)])
        self.assertEqual(uploads, [self.clip, part2, part3])
        self.assertEqual(list(self.folder.glob('*.mp4')), [part3])
        self.assertTrue((self.folder / '.retained').exists())
        self.assertFalse((self.folder / '.cleaned').exists(), 'not a full successful delivery')

    async def test_cleanup_error_keeps_receipt_and_does_not_ack_or_retry_upload(self):
        accepted, media_removed, result_calls = [], False, 0
        extra = self.folder / 'temp-media'
        extra.mkdir()
        (extra / 'encode.tmp').write_bytes(b'media')

        def school(action, job, **data):
            nonlocal result_calls
            if action == 'poll':
                return {'command': 'purge' if result_calls else 'deliver', 'id': self.job, 'sent': 0}
            if action == 'claim':
                return {'command': 'send', 'attempt': 'b'*32}
            if action == 'result':
                self.assertFalse(self.clip.exists() or extra.exists())
                result_calls += 1
            return {}

        unlink = Path.unlink

        def deny_once(path, *args, **kwargs):
            if path == self.clip and not media_removed:
                raise PermissionError('temporary cleanup failure')
            return unlink(path, *args, **kwargs)

        def send(permit, path):
            accepted.append(path)
            return 'accepted', '22'

        with patch('recording.send_video', side_effect=send), patch.object(Path, 'unlink', deny_once):
            with self.assertRaises(PermissionError):
                await Runner(school, self.root, None, None, None).tick()
            self.assertTrue((self.folder / 'receipt.json').exists())
            self.assertEqual(result_calls, 0)
            self.assertFalse((self.folder / '.cleaned').exists())
            media_removed = True
            await Runner(school, self.root, None, None, None).tick()
        self.assertEqual(accepted, [self.clip])
        self.assertEqual(result_calls, 1)
        self.assertFalse(self.folder.exists())

    async def test_decline_purges_and_ambiguous_result_retains_until_deadline(self):
        seen = []
        status = 'retain'

        def school(action, job, **unused):
            seen.append(action)
            return {'command': status, 'id': job}

        runner = Runner(school, self.root, None, None, None)
        await runner.tick()
        self.assertTrue(self.clip.exists())
        self.assertTrue((self.folder / '.retained').exists())
        self.assertEqual(seen, ['poll'])
        now = int(time.time())
        atomic(self.folder/'meta.json', {'created': now-7200, 'expires': now-1})
        runner.swept = 0
        status = 'purge'
        await runner.tick()
        self.assertFalse(self.clip.exists())
        self.assertNotIn('claim', seen)

    async def test_expiry_erases_while_school_is_unreachable(self):
        now = int(time.time())
        atomic(self.folder / 'meta.json', {'created': now-25*3600, 'expires': now-1})
        (self.folder / '.retained').touch()

        def school(action, job, **unused):
            self.assertEqual(action, 'poll')
            raise ProtocolError(503)

        with self.assertRaises(ProtocolError):
            await Runner(school, self.root, None, None, None).tick()
        self.assertFalse(self.clip.exists())
        self.assertTrue((self.folder / '.cleaned').exists())

    async def test_interrupted_encoder_never_uploads_any_existing_clip(self):
        (self.folder / '.capturing').touch()
        calls = []

        def school(action, job, **unused):
            calls.append(action)
            return {'command': 'deliver', 'id': job, 'sent': 0}

        await Runner(school, self.root, None, None, None).tick()
        self.assertEqual(calls, ['poll', 'fault'])
