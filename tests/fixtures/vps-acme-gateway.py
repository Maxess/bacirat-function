"""Test-only HTTPS adapter: present /directory for a local Pebble ACME server.

Runs inside the disposable VM's isolated CA namespace. No test override is shipped
in the production installer; only the fixture's transport root is trusted in QA.
"""
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import ssl
import sys

HOST = 'acme-v02.api.letsencrypt.org'
CA_ADDRESS = '172.30.123.3'
TRANSPORT_CA = sys.argv[1]
BACKEND_CONTEXT = ssl.create_default_context(cafile=TRANSPORT_CA)


class Pebble(HTTPSConnection):
    def connect(self):
        with socket.create_connection(('127.0.0.1', 14000), timeout=8) as sock:
            self.sock = BACKEND_CONTEXT.wrap_socket(sock, server_hostname=HOST)


class Gateway(BaseHTTPRequestHandler):
    def do_GET(self):
        self.forward()

    def do_HEAD(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        count = int(self.headers.get('Content-Length', '0'))
        if self.headers.get('Host') != HOST or not 0 <= count <= 16384:
            self.send_error(400)
            return
        url = '/dir' if self.path == '/directory' and self.command == 'GET' else self.path
        headers = {name: self.headers[name] for name in ('Content-Type', 'User-Agent') if name in self.headers}
        with Pebble(HOST, timeout=8) as req:
            req.request(self.command, url, self.rfile.read(count) if count else None, {'Host': HOST, **headers})
            with req.getresponse() as response:
                raw = response.read(128 * 1024)
                self.send_response(response.status)
                for name in ('Content-Type', 'Replay-Nonce', 'Location', 'Link', 'Retry-After', 'Cache-Control'):
                    for value in response.headers.get_all(name, []):
                        self.send_header(name, value)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(raw)


cert, key = sys.argv[2:4]
server = ThreadingHTTPServer((CA_ADDRESS, 443), Gateway)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(cert, key)
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
