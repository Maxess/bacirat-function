#!/usr/bin/env python3
"""Poll the local audio SFU and enforce the school plugin's supervised-room policy.

Runs on the media VPS as the existing unprivileged school-classroom user.
No new listener, firewall rule, Caddy route or change to the LiveKit configuration.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import signal
import socket
import sys
import threading
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request

INTERVAL = 3
OUTAGE_SECONDS = 12
PERMIT_SECONDS = 10
PERMIT_FILE = Path('/run/school-classroom-watch/permit.json')
MAX_ROOMS = 80
PREFIX = re.compile(r'test-[a-f0-9]{20}\Z')
PERSON = re.compile(r'(student|teacher|admin)-[1-9][0-9]{0,17}\Z')
SID = re.compile(r'[A-Za-z0-9_-]{4,72}\Z')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def json_post(url, body, headers=None, timeout=3):
    request = Request(url, data=body, headers={'Content-Type': 'application/json', **(headers or {})}, method='POST')
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
        raw = response.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError('oversized reply')
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError('invalid reply')
        return obj


def check_gate():
    with build_opener(ProxyHandler({}), NoRedirect()).open('http://127.0.0.1:7883/supervision-v2', timeout=2) as response:
        if response.status != 204:
            raise ValueError('media admission service is not supervision-aware')


def pack(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('ascii')


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=')


def rpc_token(config, video, now):
    claims = {'iss': config['api_key'], 'nbf': now - 5, 'iat': now, 'exp': now + 25, 'video': video}
    message = b64(b'{"alg":"HS256","typ":"JWT"}') + b'.' + b64(pack(claims))
    return (message + b'.' + b64(hmac.digest(config['api_secret'].encode(), message, 'sha256'))).decode('ascii')


def scope(prefix, name):
    return isinstance(name, str) and re.fullmatch(re.escape(prefix) + r'-v3-c[1-9][0-9]{0,9}-[a-f0-9]{24}', name) is not None


def watch_endpoint(origin, site_path):
    parts = urlsplit(origin)
    if not re.fullmatch(r'https://[a-z0-9-]+(?:\.[a-z0-9-]+)+', origin) or not parts.hostname:
        raise ValueError('school origin must be an HTTPS DNS name')
    if not re.fullmatch(r'(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*', site_path):
        raise ValueError('site path must be empty or a path like /school-test without a trailing slash')
    return origin + site_path + '/plugins/school-plugins.php?plugin=school-classroom&route=watch'


def signature(config, raw):
    msg = b'school-classroom-watch-v1\n' + config['school_origin'].encode('ascii') + b'\n' + raw
    return hmac.new(config['api_secret'].encode(), msg, hashlib.sha256).hexdigest()


class Watcher:
    def __init__(self, config, prefix, site_path, permit_file, post=json_post, gate=check_gate):
        if not PREFIX.fullmatch(prefix) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', config['api_secret']) \
                or not re.fullmatch(r'[A-Za-z0-9_-]{12,64}', config['api_key']):
            raise ValueError('invalid media installation or key')
        self.url = watch_endpoint(config['school_origin'], site_path)
        self.config = config
        self.prefix = prefix
        self.post = post
        self.gate = gate
        self.permit_file = permit_file

    def rpc(self, method, data, grant):
        auth = 'Bearer ' + rpc_token(self.config, grant, int(time.time()))
        return self.post('http://127.0.0.1:7880/twirp/livekit.RoomService/' + method, pack(data), {'Authorization': auth}, timeout=2)

    def room_names(self):
        reply = self.rpc('ListRooms', {}, {'roomList': True})
        rooms = reply.get('rooms', [])
        if not isinstance(rooms, list) or any(not isinstance(r, dict) for r in rooms):
            raise ValueError('malformed room list')
        return [r['name'] for r in rooms if scope(self.prefix, r.get('name'))]

    def active_people(self, room):
        try:
            reply = self.rpc('ListParticipants', {'room': room}, {'roomAdmin': True, 'room': room})
        except HTTPError as error:
            if error.code == 404:
                return {}  # A room may close between the two samples; it is absent on the next ListRooms.
            raise
        people = reply.get('participants', [])
        if not isinstance(people, list) or len(people) > 60:
            raise ValueError('malformed participant list')
        active = {}
        for person in people:
            if person.get('state') not in (2, 'ACTIVE'):
                continue
            identity, sid = person.get('identity'), person.get('sid')
            if not isinstance(identity, str) or not PERSON.fullmatch(identity) or not isinstance(sid, str) or not SID.fullmatch(sid):
                raise ValueError('unexpected participant in an owned room')
            if not identity.startswith('student-'):
                # The default ListParticipants can be a stored snapshot. This no-op RPC returns current room state
                # in 1.13.11 and avoids changing any participant permission, metadata, or server configuration.
                try:
                    current = self.rpc('UpdateParticipant', {'room': room, 'identity': identity}, {'roomAdmin': True, 'room': room})
                except HTTPError as error:
                    if error.code == 404:
                        continue
                    raise
                if current.get('state') not in (2, 'ACTIVE') or current.get('sid') != sid or current.get('identity') != identity:
                    continue
            if identity in active:
                raise ValueError('duplicate participant')
            active[identity] = sid
        return active

    def invalidate(self):
        self.permit_file.unlink(missing_ok=True)

    def publish_permits(self, permits, until):
        # Gate readers either see the previous complete sample or this complete sample.
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.permit_file.parent, prefix='.permit-', delete=False) as file:
                temp = Path(file.name)
                file.write(pack({'until': until, 'rooms': permits}))
            temp.replace(self.permit_file)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def tick(self):
        started = time.monotonic()
        self.gate()
        at = int(time.time())  # Capture before sampling rooms, so a concurrent CreateRoom is not an absence.
        names = self.room_names()
        if len(names) > MAX_ROOMS or len(set(names)) != len(names):
            raise ValueError('room limit')
        with ThreadPoolExecutor(max_workers=6) as pool:
            rooms = dict(zip(names, pool.map(self.active_people, names)))
        body = pack({'watch_version': 2, 'at': at, 'prefix': self.prefix, 'rooms': rooms})
        result = self.post(self.url, body, {'X-Classroom-Signature': signature(self.config, body)}, timeout=5)
        drops = result.get('drop')
        if result.get('ok') is not True or not isinstance(drops, list) or len(drops) > len(rooms) \
                or any(not scope(self.prefix, n) or n not in rooms for n in drops):
            raise ValueError('invalid authorization response')
        permits = result.get('permit')
        if not isinstance(permits, dict) or len(permits) > len(rooms) \
                or any(name not in rooms or name in drops or mode not in ('starting', 'live') for name, mode in permits.items()):
            raise ValueError('invalid admission policy')
        if time.monotonic() - started >= PERMIT_SECONDS:
            raise ValueError('expired admission sample')
        self.publish_permits(permits, started + PERMIT_SECONDS)
        self.drop(drops)
        return started

    def drop(self, names):
        def delete(room):
            if not scope(self.prefix, room):
                raise ValueError('unowned room')
            try:
                self.rpc('DeleteRoom', {'room': room}, {'roomCreate': True})
            except HTTPError as error:
                if error.code != 404:
                    raise
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(delete, names))

    def fail_closed(self):
        self.invalidate()
        self.drop(self.room_names())


def notify(message):
    """Use the local systemd watchdog without adding a Python dependency."""
    address = os.environ.get('NOTIFY_SOCKET')
    if not address:
        return
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.settimeout(1)
        sock.sendto(message.encode('ascii'), '\0' + address[1:] if address.startswith('@') else address)


def run(watch, stop):
    last_ok = time.monotonic()
    healthy = None
    recover = True
    watch.invalidate()
    notify('READY=1')
    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                notify('WATCHDOG=1')
                if recover:
                    watch.fail_closed()  # A restart never renews admission to an old transport.
                    recover = False
                last_ok = watch.tick()
                if healthy is not True:
                    print('classroom-watch: school and SFU reachable', flush=True)
                healthy = True
            except (OSError, ValueError, AttributeError, KeyError, TypeError):
                if healthy is not False:
                    print('classroom-watch: supervision not confirmed; retrying', file=sys.stderr, flush=True)
                healthy = False
                watch.invalidate()
                if time.monotonic() - last_ok >= OUTAGE_SECONDS:
                    recover = True
                    try:
                        watch.fail_closed()
                    except (OSError, ValueError, AttributeError, KeyError, TypeError):
                        pass  # Retry next tick; never log credentials, participant data or RPC bodies.
            stop.wait(max(0.15, INTERVAL - (time.monotonic() - started)))
    finally:
        watch.fail_closed()  # Deliberate service stop closes only this installation's supervised rooms.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prefix', help='installation prefix from the school classroom manager')
    parser.add_argument('--site-path', default='', help='school subfolder, e.g. /school-test, or empty')
    parser.add_argument('--permit-file', type=Path, default=PERMIT_FILE)
    parser.add_argument('--drain', action='store_true', help='invalidate leases and delete only this prefix’s scheduled rooms')
    parser.add_argument('--config', type=Path, default=Path('/etc/school-classroom/admission.json'))
    args = parser.parse_args()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        watch = Watcher(json.loads(args.config.read_text()), args.prefix, args.site_path, args.permit_file)
        if args.drain:
            watch.fail_closed()
        else:
            run(watch, stop)
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        sys.exit('classroom-watch: setup or safe shutdown failed; check local media and private configuration')


if __name__ == '__main__':
    main()
