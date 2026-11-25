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
import ssl
import sys


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

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            assert self.headers['Host'] == 'school.example.test' and 0 < length <= 10_000_000
            body = self.rfile.read(length)
            assert len(body) == length
            content = self.headers['Content-Type']
            if self.path == '/fixture-bot/sendVideo':
                assert content.startswith('multipart/form-data; boundary=')
                msg = BytesParser(policy=policy.default).parsebytes(b'Content-Type: ' + content.encode() + b'\r\n\r\n' + body)
                fields = {section.get_param('name', header='content-disposition'): section.get_payload(decode=True)
                          for section in msg.iter_parts()}
                assert fields['chat_id'] == b'-100321' and fields['supports_streaming'] == b'true' and fields['caption']
                assert fields['video'][4:8] == b'ftyp'
                with (self.server.directory / 'received.mp4').open('xb') as dst:  # No duplicate delivery.
                    dst.write(fields['video'])
                self.reply(200, 'application/json', json.dumps({'ok': True, 'result': {'chat': {'id': -100321}, 'message_id': 321}}).encode())
            elif self.path == '/campus/plugins/school-plugins.php?plugin=school-classroom&route=events':
                encoded, claims, signature = self.headers['Authorization'].removeprefix('Bearer ').split('.')

                def b64url(text):
                    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))

                pair = self.server.pair
                assert hmac.compare_digest(b64url(signature), hmac.digest(pair['api_secret'].encode(),
                                                                          f'{encoded}.{claims}'.encode(), 'sha256'))
                claims = json.loads(b64url(claims))
                assert claims['iss'] == pair['api_key'] and claims['sha256'] == base64.b64encode(hashlib.sha256(body).digest()).decode()
                self.reply(200, 'application/json', b'{"ok":true}')
            elif self.path == '/campus/plugins/school-plugins.php?plugin=school-classroom&route=recording':
                assert length <= 32768
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
                self.send_error(404)
        except (AssertionError, KeyError, ValueError, OSError, http.client.HTTPException):
            self.send_error(403)


if __name__ == '__main__':
    directory = Path(sys.argv[1])
    with ThreadingHTTPServer(('172.30.124.2', 443), Peer) as server:
        server.directory = directory
        server.pair = json.loads((directory / 'pair.json').read_text())
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(directory / 'school.pem', directory / 'school.key')
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        server.serve_forever()
