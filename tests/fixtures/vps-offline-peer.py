"""Synthetic HTTPS school for the disconnected QEMU integration test only."""
import base64
import hashlib
import hmac
import http.server
import json
from pathlib import Path
import ssl
import sys
import threading
import time

sys.path.insert(0, '/opt/school-classroom')
import vps_media as media
from importlib.machinery import SourceFileLoader
from importlib.util import spec_from_loader, module_from_spec

PAIR = json.loads(Path('/etc/school-classroom/site-pairing.json').read_text())
SET = Path('/etc/school-classroom/offline-vps.json')
EVENTS = Path('/run/baci-fixture-events.json')
BEAT = Path('/run/baci-fixture-beat.json')


def hex_digest(raw):
    return base64.b64encode(hashlib.sha256(raw).digest()).decode('ascii')


def decode(raw):
    return base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))


class School(http.server.BaseHTTPRequestHandler):
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(min(32768, int(self.headers.get('Content-Length', '0'))))
        try:
            data = json.loads(raw)
            auth = self.headers['Authorization']
            with self.lock:
                if self.path == '/campus' + media.EVENT_ROUTE:
                    head, body, sig = auth.removeprefix('Bearer ').split('.')
                    claims = json.loads(decode(body))
                    assert hmac.compare_digest(decode(sig), hmac.digest(PAIR['api_secret'].encode(), f'{head}.{body}'.encode(), 'sha256'))
                    assert claims['iss'] == PAIR['api_key'] and hmac.compare_digest(claims['sha256'], hex_digest(raw))
                    if data.get('room', {}).get('name') == 'offline-fixture-only':
                        with EVENTS.open('a') as file:
                            file.write(data['event'] + '\n')
                    result = {'ok': True}
                elif self.path == '/campus' + media.EVENT_ROUTE.replace('route=events', 'route=recording'):
                    assert hmac.compare_digest(auth, 'Bearer ' + PAIR['recording_secret']) and data['protocol'] == 2
                    if data['action'] == 'plan':
                        assert data['native_release'] == '0.4.23'
                        if data.get('available') is True:
                            BEAT.write_text(data['worker'])
                        result = {'ok': True, 'capture': [], 'pending': [], 'settle': [], 'work': None, 'lease': 0}
                    elif data['action'] == 'maintenance':
                        match = BEAT.exists() and data['worker'] == BEAT.read_text()
                        result = {'ok': True, 'release': '0.4.23', 'idle': True, 'worker_ok': True, 'native_ready': match}
                    else:
                        raise ValueError('unexpected synthetic request')
                else:
                    raise ValueError('wrong site path')
        except (ValueError, AssertionError, TypeError, KeyError):
            self.send_error(403)
            return
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def exercise():
    loader = SourceFileLoader('setup', '/opt/school-classroom/baci-vps.py')
    spec = spec_from_loader(loader.name, loader)
    setup = module_from_spec(spec)
    loader.exec_module(setup)
    config = json.loads(SET.read_text())
    if config.get('profile', 'full') == 'full':
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and not BEAT.exists():
            time.sleep(.5)
        assert BEAT.exists(), 'Missing real Recorder heartbeat to synthetic site'
    deployment = media.pilot_deployment()
    name = 'offline-fixture-only'
    for rpc, data in [('CreateRoom', {'name': name}), ('ListRooms', {}), ('DeleteRoom', {'room': name})]:
        code, _, body = setup.probe_edge(config, '/twirp/livekit.RoomService/' + rpc, method='POST', body=json.dumps(data).encode(), headers={
            'Authorization': 'Bearer ' + media.jwt(deployment, video={'roomCreate': True, 'roomList': True}), 'Content-Type': 'application/json'})
        assert code == 200, f'Signed {rpc} did not pass Caddy and LiveKit'
        if rpc == 'ListRooms':
            assert [room['name'] for room in json.loads(body)['rooms']] == [name]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if EVENTS.exists() and EVENTS.read_text().splitlines() == ['room_started', 'room_finished']:
            print('Created and retired synthetic room; native signed event delivery confirmed.')
            return
        time.sleep(.25)
    raise SystemExit('Missing real signed LiveKit events for synthetic room')


if sys.argv[1] == '--exercise':
    exercise()
else:
    server = http.server.ThreadingHTTPServer(('172.30.123.2', 443), School)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(*sys.argv[1:3])
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
