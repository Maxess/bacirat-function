"""Bounded, non-redirecting TLS for the school API and the existing bot APIs."""
import http.client
import json
import re
import secrets
import ssl
import time
from urllib.parse import urlsplit

HOST = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z', re.I)
PART_LIMIT = 45_000_000
CONTEXT = ssl.create_default_context()


class ProtocolError(Exception):
    def __init__(self, status=0):
        super().__init__('service request not confirmed')
        self.status = status


def safe_url(url):
    if not isinstance(url, str) or len(url) > 2048 or re.search(r'[\s\\\x00-\x1f\x7f]', url):
        raise ProtocolError()
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or not HOST.fullmatch(parts.hostname or '') or parts.netloc.lower() != parts.hostname
                or parts.fragment or not parts.path.startswith('/')):
            raise ProtocolError()
        return parts
    except ValueError:
        raise ProtocolError() from None


def post(url, headers, body, *, limit, seconds):
    parts = safe_url(url)
    path = parts.path + ('?' + parts.query if parts.query else '')
    deadline = time.monotonic() + seconds
    conn = http.client.HTTPSConnection(parts.hostname, context=CONTEXT, timeout=min(6, seconds))

    def bound():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProtocolError()
        if conn.sock is not None:
            conn.sock.settimeout(min(12, remaining))

    try:
        conn.connect()
        bound()
        conn.putrequest('POST', path, skip_accept_encoding=True)
        for name, value in headers.items():
            conn.putheader(name, value)
        conn.endheaders()
        for chunk in body:
            bound()
            conn.send(chunk)
        bound()
        res = conn.getresponse()
        raw = bytearray()
        while len(raw) <= limit:
            bound()
            data = res.read1(min(65536, limit + 1 - len(raw)))
            if not data:
                if res.length not in (0, None):  # read1() can return an early EOF with Content-Length remaining.
                    raise ProtocolError()
                return res.status, {k.lower(): v for k, v in res.getheaders()}, bytes(raw)
            raw.extend(data)
        raise ProtocolError()
    except (OSError, http.client.HTTPException, ValueError):
        raise ProtocolError() from None
    finally:
        conn.close()


def json_body(raw):
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, RecursionError):
        return {}


def json_post(url, data, headers=None, *, limit=131072, seconds=12):
    body = json.dumps(data, ensure_ascii=False).encode()
    return post(url, {'Content-Type': 'application/json', 'Content-Length': str(len(body)), **(headers or {})},
                [body], limit=limit, seconds=seconds)


class School:
    def __init__(self, site, secret, worker, protocol=2):
        origin = safe_url(site + '/')
        if origin.query or not re.fullmatch(r'(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63})*/', origin.path):
            raise ProtocolError()
        self.url = site + '/plugins/school-plugins.php?plugin=school-classroom&route=recording'
        self.headers = {'Authorization': 'Bearer ' + secret}
        self.worker, self.protocol = worker, protocol

    def __call__(self, action, job=None, **data):
        code, _, body = json_post(self.url, self.payload(action, job, data), self.headers, limit=1024 * 1024,
                                  seconds=18 if action == 'claim' else 7)
        obj = json_body(body)
        if code != 200 or obj.get('ok') is not True:
            raise ProtocolError(code)
        return obj

    def payload(self, action, job, data):
        return {'action': action, 'protocol': self.protocol, 'worker': self.worker, 'id': job, **data}

    def document(self, job, room, doc, byte_range):
        headers = self.headers.copy()
        if byte_range and len(byte_range) <= 192 and re.fullmatch(r'bytes=(?:[0-9]+-[0-9]+,){0,15}[0-9]+-[0-9]+', byte_range):
            headers['Range'] = byte_range
        return json_post(self.url, self.payload('document', job, {'room': room, 'doc': doc}), headers,
                         limit=17 * 1024 * 1024, seconds=15)


def multipart(url, fields, key, file):
    if file.is_symlink() or not file.is_file() or not 0 < (size := file.stat().st_size) <= PART_LIMIT:
        raise ProtocolError()
    boundary = 'baci-' + secrets.token_hex(24)
    head = b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode()
                    for k, v in fields.items())
    head += (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="lesson.mp4"\r\n'
             'Content-Type: video/mp4\r\n\r\n').encode()
    tail = f'\r\n--{boundary}--\r\n'.encode()
    with file.open('rb') as fp:
        def chunks():
            yield head
            yield from iter(lambda: fp.read(65536), b'')
            yield tail

        return post(url, {'Content-Type': 'multipart/form-data; boundary=' + boundary,
                          'Content-Length': str(len(head) + size + len(tail))}, chunks(), limit=131072, seconds=150)


def identifier(value):
    return str(value) if type(value) in (int, str) else ''


def send_video(permit, path, *, post_json=json_post, upload=multipart):
    """Return (result, message_id). Never retry a possibly accepted group POST."""
    provider, chat, prefix, caption = (permit[k] for k in ('provider', 'chat', 'prefix', 'caption'))
    try:
        parts = safe_url(prefix)
        if provider not in ('bale', 'rubika', 'telegram') or parts.query or not prefix.endswith('/'):
            return 'rejected', ''
        if provider == 'bale' and parts.hostname != 'tapi.bale.ai' or provider == 'rubika' and parts.hostname != 'botapi.rubika.ir':
            return 'rejected', ''
    except ProtocolError:
        return 'rejected', ''
    if provider == 'rubika':
        # requestSendFile and the upload stage cannot post a message to a group.
        try:
            status, _, raw = post_json(prefix + 'requestSendFile', {'type': 'Video'})
            obj = json_body(raw)
            if status != 200 or obj.get('status') != 'OK' or not isinstance(obj.get('data'), dict):
                return ('retry' if status == 429 or status >= 500 else 'rejected'), ''
            url = obj['data'].get('upload_url')
            host = safe_url(url).hostname
            if not (host.endswith(('.rubika.ir', '.iranlms.ir')) or host in ('rubika.ir', 'iranlms.ir')):
                return 'rejected', ''
            status, _, raw = upload(url, {}, 'file', path)
            obj = json_body(raw)
            data = obj.get('data', obj)
            file_id = identifier(data.get('file_id', data.get('fileId'))) if isinstance(data, dict) else ''
            if not 200 <= status < 300 or obj.get('status', 'OK') != 'OK' or not re.fullmatch(r'[A-Za-z0-9_\-]{1,256}', file_id):
                return ('retry' if status == 429 or status >= 500 else 'rejected'), ''
        except ProtocolError:
            return 'retry', ''
    try:
        if provider == 'rubika':
            status, _, raw = post_json(prefix + 'sendFile', {'chat_id': chat, 'file_id': file_id, 'text': caption})
        else:
            status, _, raw = upload(prefix + 'sendVideo', {'chat_id': chat, 'caption': caption, 'supports_streaming': 'true'}, 'video', path)
    except ProtocolError:
        return 'uncertain', ''
    obj = json_body(raw)
    rubika = provider == 'rubika'
    info = obj.get('data') if rubika and obj.get('status') == 'OK' else obj.get('result') if not rubika and obj.get('ok') is True else None
    if 200 <= status < 300 and isinstance(info, dict):
        receipt = identifier(info.get('message_id'))
        dest = info.get('chat_id') if rubika else info.get('chat', {}).get('id') if isinstance(info.get('chat'), dict) else None
        if re.fullmatch(r'[A-Za-z0-9_-]{1,96}', receipt) and (dest is None or identifier(dest) == chat):
            return 'accepted', receipt
    denied = obj.get('ok') is False if not rubika else obj.get('status') in ('ERROR', 'FAILED', 'INVALID_INPUT')
    if denied and status == 429:
        return 'retry', ''
    if denied and (status == 200 or status in (400, 401, 403, 404, 413)):
        return 'rejected', ''
    return 'uncertain', ''
