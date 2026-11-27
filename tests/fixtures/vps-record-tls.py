"""QA TLS edge in the test client's network, outside the classroom VPS."""
import base64
from email import policy
from email.parser import BytesParser
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import ssl
import sys


SCHOOL = 'school.example.test'
EVENTS = '/campus/plugins/school-plugins.php?plugin=school-classroom&route=events'
RECORDING = EVENTS.replace('route=events', 'route=recording')
RECORDER = 'Baci-Classroom-Recorder/0.4.23'
VIDEO_ENDPOINTS = {
    'telegram': (SCHOOL, '/fixture-bot/sendVideo'),
    'bale': ('tapi.bale.ai', '/botQA-fixture/sendVideo'),
}
RUBIKA_API = 'botapi.rubika.ir'
RUBIKA_UPLOAD = 'upload.rubika.ir'
RUBIKA_PATH = '/v3/QA-fixture/'


class Peer(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, code, content_type, data, **headers):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path != '/readyz' or self.headers['Host'] != SCHOOL:
            self.send_error(404)
            return
        try:
            with socket.create_connection(('127.0.0.1', 18391), timeout=1):
                self.reply(200, 'text/plain', b'ready\n')
        except OSError:
            self.send_error(503)

    def multipart(self, content, body):
        # Count attempts: refused/duplicate deliveries must not even upload.
        with (self.server.directory / 'uploads.log').open('ab') as log:
            log.write(b'upload\n')
        assert content.startswith('multipart/form-data; boundary=')
        parsed = BytesParser(policy=policy.default).parsebytes(b'Content-Type: ' + content.encode() + b'\r\n\r\n' + body)
        return {section.get_param('name', header='content-disposition'): section.get_payload(decode=True)
                for section in parsed.iter_parts()}

    def reply_json(self, data):
        self.reply(200, 'application/json', json.dumps(data).encode())

    def save_video(self, name, data):
        assert data[4:8] == b'ftyp'
        with (self.server.directory / name).open('xb') as dst:  # No duplicate delivery.
            dst.write(data)

    def provider_request(self, endpoint, content, body):
        assert 'Authorization' not in self.headers and self.headers.get('User-Agent') != RECORDER
        platform = self.server.platform
        if endpoint == VIDEO_ENDPOINTS.get(platform):
            fields = self.multipart(content, body)
            assert fields['chat_id'] == b'-100321' and fields['supports_streaming'] == b'true' and fields['caption']
            self.save_video('received.mp4', fields['video'])
            self.reply_json({'ok': True, 'result': {'chat': {'id': -100321}, 'message_id': 321}})
        elif platform == 'rubika' and endpoint == (RUBIKA_API, RUBIKA_PATH + 'requestSendFile'):
            assert json.loads(body) == {'type': 'Video'}
            (self.server.directory / 'rubika-requested').touch(exist_ok=False)
            self.reply_json({'status': 'OK', 'data': {'upload_url': f'https://{RUBIKA_UPLOAD}/fixture'}})
        elif platform == 'rubika' and endpoint == (RUBIKA_UPLOAD, '/fixture'):
            assert (self.server.directory / 'rubika-requested').is_file()
            fields = self.multipart(content, body)
            assert set(fields) == {'file'}
            self.save_video('uploaded.mp4', fields['file'])
            self.reply_json({'status': 'OK', 'data': {'file_id': 'qa-video-321'}})
        elif platform == 'rubika' and endpoint == (RUBIKA_API, RUBIKA_PATH + 'sendFile'):
            payload = json.loads(body)
            assert payload['chat_id'] == 'g0fixture321' and payload['file_id'] == 'qa-video-321' and payload['text']
            self.save_video('received.mp4', (self.server.directory / 'uploaded.mp4').read_bytes())
            self.reply_json({'status': 'OK', 'data': {'chat_id': 'g0fixture321', 'message_id': '321'}})
        else:
            self.send_error(404)

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            assert 0 < length <= 10_000_000
            body = self.rfile.read(length)
            assert len(body) == length
            content = self.headers['Content-Type']
            endpoint = self.headers['Host'], self.path
            if endpoint == (SCHOOL, EVENTS):
                encoded, claims, signature = self.headers['Authorization'].removeprefix('Bearer ').split('.')

                def b64url(text):
                    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))

                pair = self.server.pair
                assert hmac.compare_digest(b64url(signature), hmac.digest(pair['api_secret'].encode(),
                                                                          f'{encoded}.{claims}'.encode(), 'sha256'))
                claims = json.loads(b64url(claims))
                assert claims['iss'] == pair['api_key'] and claims['sha256'] == base64.b64encode(hashlib.sha256(body).digest()).decode()
                self.reply(200, 'application/json', b'{"ok":true}')
            elif endpoint == (SCHOOL, RECORDING):
                # Model the observed ingress rejection before PHP authorization.
                assert self.headers.get('User-Agent') == RECORDER and length <= 32768
                conn = http.client.HTTPConnection('127.0.0.1', 18391, timeout=8)
                try:
                    headers = {'Content-Type': content, 'Authorization': self.headers['Authorization']}
                    if 'Range' in self.headers:
                        headers['Range'] = self.headers['Range']
                    conn.request('POST', self.path, body=body, headers=headers)
                    rsp = conn.getresponse()
                    forwarded = {k: v for k, v in rsp.getheaders() if k.lower() in ('content-range', 'cache-control')}
                    self.reply(rsp.status, rsp.getheader('Content-Type', 'application/json'), rsp.read(17 * 1024 * 1024), **forwarded)
                finally:
                    conn.close()
            else:
                self.provider_request(endpoint, content, body)
        except (AssertionError, KeyError, ValueError, OSError, http.client.HTTPException):
            self.send_error(403)


if __name__ == '__main__':
    directory = Path(sys.argv[1])
    with ThreadingHTTPServer(('172.30.124.2', 443), Peer) as server:
        server.directory = directory
        server.platform = sys.argv[2]
        server.pair = json.loads((directory / 'pair.json').read_text())
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(directory / 'school.pem', directory / 'school.key')
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        server.serve_forever()
