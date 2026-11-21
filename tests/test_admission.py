import base64
import hashlib
import hmac
import sys
import json
import unittest
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from admission import admit, listener_token


class AdmissionTests(unittest.TestCase):
    config = {'api_key': 'test-key', 'api_secret': 'fake-testing-secret', 'school_origin': 'https://school.test.invalid'}
    now = 1700000000

    def token(self, video=None, **extra):
        def part(obj):
            return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b'=')
        grant = {'room': 'test-school-c1', 'roomJoin': True, 'canSubscribe': True, 'canPublish': False, 'canPublishData': False}
        grant.update(video or {})
        body = {'sub': 'student-1', 'iss': self.config['api_key'], 'exp': self.now+120, 'video': grant, **extra}
        message = part({'alg': 'HS256'}) + b'.' + part(body)
        sig = hmac.digest(self.config['api_secret'].encode(), message, hashlib.sha256)
        return (message + b'.' + base64.urlsafe_b64encode(sig).rstrip(b'=')).decode()

    def headers(self, token):
        return {'X-Forwarded-Uri': '/rtc?' + urlencode({'access_token': token}), 'Origin': self.config['school_origin']}

    def test_listener(self):
        self.assertTrue(admit(self.headers(self.token()), self.config, self.now))
        headers = {'X-Forwarded-Uri': '/rtc/v1/validate', 'Authorization': 'Bearer ' + self.token()}
        self.assertTrue(admit(headers, self.config, self.now))

    def test_reject_publisher_refresh_and_other_grants(self):
        for grant in ({'canPublish': True}, {'canPublish': None}, {'canPublishData': True}, {'roomAdmin': True}, {'roomJoin': False}):
            self.assertFalse(admit(self.headers(self.token(grant)), self.config, self.now))

    def test_recorder_token_cannot_enter_through_public_signaling(self):
        recorder = self.token({'hidden': True, 'recorder': True}, sub='recorder-'+'a'*32)
        self.assertFalse(admit(self.headers(recorder), self.config, self.now))

    def test_invalid_credentials_and_origins(self):
        token = self.token()
        for candidate in ('', 'x.y.z', self.token(exp=self.now), self.token(nbf=self.now+1), self.token(sub='rogue'), self.token(iss='other')):
            self.assertFalse(admit(self.headers(candidate), self.config, self.now))
        self.assertFalse(admit({**self.headers(token), 'Origin': 'https://other.example'}, self.config, self.now))
        self.assertFalse(admit({**self.headers(token), 'Authorization': 'Bearer ' + self.token({'canPublish':True})}, self.config, self.now))
        self.assertFalse(admit({'X-Forwarded-Uri': '/rtc?access_token='+token+'&access_token='+token}, self.config, self.now))
        self.assertFalse(listener_token('x' + token, self.config['api_key'], self.config['api_secret'], self.now))


if __name__ == '__main__':
    unittest.main()
