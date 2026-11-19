#!/usr/bin/env python3
"""Loopback gate for initial/renewed LiveKit signaling connections.

LiveKit OSS refreshes tokens when changing permissions. Never admit a token
that can publish: a new connection must start as a listener. The room service
may grant the microphone on the established connection after admission.
"""
import base64
import hashlib
import hmac
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

RTC_PATHS = frozenset(['/rtc', '/rtc/validate', '/rtc/v1', '/rtc/v1/validate'])
TOKEN_PART = re.compile(r'[A-Za-z0-9_-]+\Z')


def decode(part):
    if not TOKEN_PART.fullmatch(part):
        raise ValueError('invalid base64url')
    return base64.urlsafe_b64decode(part + '=' * (-len(part) % 4))


def listener_token(token, key, secret, now):
    try:
        if len(token) > 8192:
            return False
        head, body, sig = token.split('.')
        signed = hmac.digest(secret.encode(), f'{head}.{body}'.encode(), hashlib.sha256)
        if not hmac.compare_digest(decode(sig), signed):
            return False
        if json.loads(decode(head)).get('alg') != 'HS256':
            return False
        claims = json.loads(decode(body))
        grant = claims['video']
        return (
            claims.get('iss') == key
            and type(claims.get('exp')) is int and claims['exp'] > now
            and type(claims.get('nbf', 0)) is int and claims.get('nbf', 0) <= now
            and re.fullmatch(r'(student|teacher|admin)-[1-9][0-9]{0,17}', claims['sub']) is not None
            and isinstance(grant.get('room'), str) and bool(grant['room'])
            and grant.get('roomJoin') is True and grant.get('canSubscribe') is True
            and grant.get('canPublish') is False and grant.get('canPublishData') is False
            and not any(grant.get(k) for k in ('roomAdmin', 'roomCreate', 'roomList', 'roomRecord'))
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def admit(headers, config, now):
    # Caddy overwrites X-Forwarded-Uri in forward_auth; this service is loopback-only.
    try:
        uri = urlsplit(headers.get('X-Forwarded-Uri', ''))
        if uri.scheme or uri.netloc or uri.path not in RTC_PATHS:
            return False
        if headers.get('Origin', config['school_origin']) != config['school_origin']:
            return False
        query = parse_qs(uri.query, keep_blank_values=True, max_num_fields=64)
        tokens = query.get('access_token', [])
        auth = headers.get('Authorization', '')
        if len(tokens) > 1 or (auth and not auth.startswith('Bearer ')):
            return False
        if auth and tokens and auth[7:] != tokens[0]:
            return False
        token = auth[7:] if auth else (tokens[0] if tokens else '')
        return listener_token(token, config['api_key'], config['api_secret'], now)
    except (KeyError, TypeError, ValueError):
        return False


def run(config, port=7883):
    class Handler(BaseHTTPRequestHandler):
        timeout = 3

        def log_message(self, _format, *args):
            pass  # Do not log bearer tokens or URL query strings.

        def do_GET(self):
            code = 204 if self.path.split('?', 1)[0] == '/admit' and admit(self.headers, config, time.time()) else 403
            self.send_response(code)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', '0')
            self.end_headers()

    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        server.serve_forever()


if __name__ == '__main__':
    run(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
