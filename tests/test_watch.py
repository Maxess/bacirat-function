import hashlib
import hmac
import importlib.util
import json
import tempfile
from urllib.error import HTTPError
from unittest.mock import patch
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('watch', Path(__file__).resolve().parents[1] / 'server' / 'classroom-watch.py')
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class WatchTests(unittest.TestCase):
    config = {'school_origin': 'https://school.test.example', 'api_key': 'k'*24, 'api_secret': 's'*64}
    prefix = 'test-' + 'a'*20
    own = prefix + '-v3-c22-' + 'e'*24
    old = prefix + '-' + 'a'*24
    foreign = 'test-' + 'b'*20 + '-v3-c22-' + 'e'*24

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.file = Path(temp.name) / 'permit.json'

    def worker(self, post, path=''):
        return watch.Watcher(self.config, self.prefix, path, self.file, post=post, gate=lambda: None)

    def test_only_known_own_new_rooms_are_deleted(self):
        deletes = []

        def post(url, body, headers, timeout):
            data = json.loads(body)
            if url.endswith('ListRooms'):
                return {'rooms': [{'name': r} for r in [self.own, self.old, self.foreign]]}
            if url.endswith('ListParticipants'):
                self.assertEqual(data['room'], self.own)
                return {'participants': [
                    {'identity': 'teacher-1', 'sid': 'PA_staff', 'state': 'ACTIVE'},
                    {'identity': 'admin-1', 'sid': 'PA_stale_admin', 'state': 'ACTIVE'},
                    {'identity': 'student-1', 'sid': 'PA_student', 'state': 2},
                    {'identity': 'student-2', 'sid': 'PA_joining', 'state': 'JOINING'},
                ]}
            if url.endswith('UpdateParticipant'):
                self.assertEqual(set(data), {'room', 'identity'})  # Observe staff; do not change any grants.
                if data['identity']=='admin-1':
                    raise HTTPError(url,404,'not found',{},None)
                return {'identity':'teacher-1','sid':'PA_staff','state':'ACTIVE'}
            if url.endswith('DeleteRoom'):
                deletes.append(data['room'])
                return {}
            self.assertEqual(url, self.config['school_origin'] + '/nested-school/plugins/school-plugins.php?plugin=school-classroom&route=watch')
            expected = hmac.new(self.config['api_secret'].encode(), b'school-classroom-watch-v1\nhttps://school.test.example\n' + body, hashlib.sha256).hexdigest()
            self.assertEqual(headers['X-Classroom-Signature'], expected)
            self.assertEqual(data['watch_version'],2)
            self.assertEqual(data['rooms'], {self.own: {'teacher-1': 'PA_staff', 'student-1': 'PA_student'}})
            return {'ok': True, 'drop': [self.own], 'permit': {}}

        worker = self.worker(post, '/nested-school')
        worker.tick()
        self.assertEqual(json.loads(self.file.read_text())['rooms'], {})
        worker.fail_closed()
        self.assertFalse(self.file.exists())
        self.assertEqual(deletes, [self.own, self.own])
        with self.assertRaises(ValueError):
            worker.drop([self.foreign])
        self.assertEqual(len(deletes), 2)

    def test_requests_cannot_redirect_to_an_arbitrary_site_path(self):
        for bad in ['//bad.example.com', '/../outside', '/%2e%2e', '/x?redirect=1', '/site/']:
            with self.assertRaises(ValueError):
                watch.watch_endpoint(self.config['school_origin'], bad)
        with self.assertRaises(ValueError):
            watch.watch_endpoint('http://school.test.example', '')

    def test_cannot_delete_unobserved_generation_from_reply(self):
        rogue = self.prefix + '-v3-c22-' + 'a'*24
        def post(url, *args, **kwargs):
            if url.endswith('ListRooms'):
                return {'rooms':[{'name':self.own}]}
            if url.endswith('ListParticipants'):
                return {'participants':[]}
            if url.endswith('route=watch'):
                return {'ok':True,'drop':[rogue]}
            self.fail('unsafe room deletion')
        worker = self.worker(post)
        with self.assertRaises(ValueError):
            worker.tick()

    def test_recovery_drains_before_renewing_and_outage_invalidates(self):
        class Clock:
            now = 0
            def is_set(self): return self.now >= 24
            def wait(self, seconds): self.now += seconds

        clock = Clock()
        actions = []
        def post(url, body, headers, timeout):
            if url.endswith('ListRooms'):
                return {'rooms':[{'name':self.own}, {'name':self.foreign}]}
            if url.endswith('DeleteRoom'):
                actions.append(('drop',clock.now,json.loads(body)['room']))
                return {}
            if url.endswith('ListParticipants'):
                return {'participants':[]}
            actions.append(('sample',clock.now))
            if 0 < clock.now < 18:
                if clock.now >= 6: self.assertFalse(self.file.exists())
                raise OSError('simulated site outage')
            return {'ok':True,'drop':[],'permit':{self.own:'starting'}}
        with patch.object(watch.time, 'monotonic', lambda: clock.now), patch('builtins.print'):
            watch.run(self.worker(post), clock)
        self.assertEqual(actions[0], ('drop',0,self.own))
        self.assertEqual(actions[1], ('sample',0))
        self.assertIn(('drop',12,self.own), actions)
        self.assertLess(actions.index(('drop',18,self.own)),actions.index(('sample',18)))
        self.assertEqual(actions[-1],('drop',24,self.own))
        self.assertFalse(self.file.exists())


if __name__ == '__main__':
    unittest.main()
