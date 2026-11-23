"""Protocol 2 source capture. Encoded RTP payloads and board deltas, never a live film."""
import asyncio
import base64
from contextlib import asynccontextmanager, contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import struct
from threading import RLock
import time
from urllib.parse import parse_qs

from capture import CaptureError, HEX, ORIGIN, Renderer, SIGNAL, lease_ms
from transport import ProtocolError

MIB = 1024 * 1024
MAX_DURATION = 7_250_000  # ms per bell (two hours + handoff margin)
MAX_SOURCES = 16
SOURCE_LIMIT = 384 * MIB   # per bell, including cached documents
SPACE_LIMIT = 3072 * MIB   # whole spool, including retained and finished files
RENDER_RESERVE = 768 * MIB
MIN_FREE = 512 * MIB
DOC_LIMIT = 16 * MIB
DOC_TYPES = {'application/pdf', 'image/png', 'image/jpeg', 'image/webp'}
ROOM = re.compile(r'test-[a-f0-9]{20}-v[34]-c[1-9][0-9]{0,9}-[a-f0-9]{24}\Z')
SOURCE = re.compile(r'source-[0-9]{3}\.baci\Z')
MAGIC = b'BACI2\0'
DURATION = struct.Struct('>I')
RECORD = struct.Struct('>III')  # ingress time (ms), UTF-8 JSON header size, unmodified media bytes


class SpaceError(CaptureError):
    pass


class StoragePressure(CaptureError):
    """The bounded live writer cannot keep up with its source."""


def flush_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sources(path):
    result = sorted(path.glob('source-*.baci'))
    if len(result) > MAX_SOURCES or any(p.is_symlink() or not p.is_file() or not SOURCE.fullmatch(p.name) for p in result):
        raise CaptureError('invalid source collection')
    return result


class Budget:
    """Shared accounting for three writers. Keep a whole-film reserve *and* OS free space."""
    def __init__(self, root):
        self.root = root
        self.total, self.rendered, self.per_job = 0, 0, {}
        self.measured = -float('inf')
        self.lock = RLock()
        self.reserved, self.inflight = {}, 0

    def measure(self):
        with self.lock:
            if time.monotonic() - self.measured < 2:
                return
            self.total = self.rendered = 0
            self.per_job = {}
            for path in self.root.iterdir():
                if HEX.fullmatch(path.name) and not path.is_symlink() and path.is_dir():
                    amount = 0
                    for file in path.rglob('*'):
                        try:
                            if not file.is_symlink() and file.is_file():
                                size = file.stat().st_size
                                if file.parent.name == '.render':
                                    self.rendered += size
                                else:
                                    self.total += size
                                amount += size
                        except FileNotFoundError:
                            pass  # The independent finishing/cleanup worker may rename a part.
                    self.per_job[path] = amount
            self.measured = time.monotonic()

    def room(self, size=0):
        with self.lock:
            self.measure()
            size += self.inflight
            return self.total + size <= SPACE_LIMIT - RENDER_RESERVE \
                and shutil.disk_usage(self.root).free >= MIN_FREE + max(0, RENDER_RESERVE - self.rendered) + size

    def check(self, path, size):
        if not self.room(size) or self.per_job.get(path, 0) + self.reserved.get(path, 0) + size > SOURCE_LIMIT:
            raise SpaceError('temporary source space limit')

    def add(self, path, size):
        with self.lock:
            self.check(path, size)
            self.per_job[path] = self.per_job.get(path, 0) + size
            self.total += size

    @contextmanager
    def writing(self, path, size):
        with self.lock:
            self.check(path, size)
            self.inflight += size
            self.reserved[path] = self.reserved.get(path, 0) + size
        try:
            yield
        finally:
            # No quota lock is held across append, fsync or document download.
            # If a rescan saw some of this write, count it conservatively until
            # the next one, including any partial bytes from a failed write.
            with self.lock:
                self.inflight -= size
                self.reserved[path] -= size
                if not self.reserved[path]:
                    self.reserved.pop(path)
                self.per_job[path] = self.per_job.get(path, 0) + size
                self.total += size

    def can_render(self):
        with self.lock:
            self.measure()
            return self.total + self.inflight <= SPACE_LIMIT - RENDER_RESERVE and self.rendered < RENDER_RESERVE \
                and shutil.disk_usage(self.root).free >= MIN_FREE + (RENDER_RESERVE - self.rendered)

    def writing_film(self):
        with self.lock:
            self.measure()
            if self.rendered >= RENDER_RESERVE or shutil.disk_usage(self.root).free < MIN_FREE:
                raise SpaceError('temporary film space limit')


class Timeline:
    """Bounded binary records avoid a base64-sized on-disk media copy. Seal by rename + fsync."""
    def __init__(self, path, budget, *, indexed=False):
        prior = sources(path)
        if len(prior) >= MAX_SOURCES:
            raise CaptureError('too many transport changes')
        self.path, self.budget = path, budget
        self.marker = path / '.capturing'
        self.target = path / f'source-{len(prior):03d}.baci'
        self.indexed = indexed
        self.index = path / '.capturing.idx'
        self.fp = self.marker.open('xb')
        self.offset = self.fp.write(MAGIC + DURATION.pack(0))
        self.segment = 0
        self.begin = time.monotonic()

    def elapsed(self):
        return max(0, min(MAX_DURATION, round((time.monotonic() - self.begin) * 1000)))

    def pack(self, events):
        at = self.elapsed()
        data = bytearray()
        for event in events:
            payload = b''
            if event[0] == 'media':
                if len(event) != 7 or not isinstance(event[-1], str) or len(event[-1]) > 1_400_000:
                    raise CaptureError('invalid encoded frame')
                payload = base64.b64decode(event[-1], validate=True)
                event = event[:-1]
            header = json.dumps(event, ensure_ascii=False, separators=(',', ':')).encode()
            if not 0 < len(header) <= MIB or len(payload) > MIB or len(data) > 3 * MIB:
                raise CaptureError('source update too large')
            data += RECORD.pack(at, len(header), len(payload)) + header + payload
        return data

    def append(self, events):
        self.write(self.pack(events))

    def stamp(self, data):
        """Stamp receipt before queuing; never use delayed disk-write time for replay."""
        if not 0 < len(data) <= 2 * MIB:
            raise CaptureError('invalid native source batch')
        offset, at = 0, self.elapsed()
        while offset < len(data):
            if len(data) - offset < RECORD.size:
                raise CaptureError('truncated native source batch')
            reserved, size, raw = RECORD.unpack_from(data, offset)
            end = offset + RECORD.size + size + raw
            if reserved or not 0 < size <= 512 or raw > MIB or end > len(data):
                raise CaptureError('invalid native source record')
            DURATION.pack_into(data, offset, at)
            offset = end
        return data

    def write(self, data):
        with self.budget.writing(self.path, len(data)):
            self.fp.write(data)
            self.fp.flush()

    def checkpoint(self, elapsed=None):
        self.fp.flush()
        end = self.fp.tell()
        if end == self.offset:
            return
        elapsed = max(self.segment, self.elapsed() if elapsed is None else elapsed)
        row = f'{self.segment} {elapsed} {self.offset} {end}\n'.encode('ascii')
        with self.budget.writing(self.path, len(row)):
            # Data first, then the sparse time/offset index. Files remain unsealed
            # after a crash; a checkpoint never implies consent to use partial media.
            os.fdatasync(self.fp.fileno())
            with self.index.open('ab') as fp:
                fp.write(row)
                fp.flush()
                os.fdatasync(fp.fileno())
            if not self.segment:
                flush_dir(self.path)
        self.offset, self.segment = end, elapsed

    def close(self, duration):
        try:
            if duration:
                if self.indexed:
                    self.checkpoint(duration)
                self.fp.seek(len(MAGIC))
                self.fp.write(DURATION.pack(duration))
                self.fp.flush()
                os.fsync(self.fp.fileno())
        finally:
            self.fp.close()
        if duration:
            if self.indexed and self.index.exists():
                self.index.replace(self.target.with_suffix('.idx'))
            self.marker.replace(self.target)
            flush_dir(self.path)
        else:
            if self.indexed:
                self.index.unlink(missing_ok=True)
            self.marker.unlink(missing_ok=True)


def duration(fp):
    size = fp.read(len(MAGIC) + DURATION.size)
    if len(size) != len(MAGIC) + DURATION.size or not size.startswith(MAGIC):
        raise CaptureError('invalid source header')
    ms, = DURATION.unpack(size[len(MAGIC):])
    if not 0 < ms <= MAX_DURATION:
        raise CaptureError('invalid source duration')
    return ms


def events(fp, headers_only=False):
    previous = 0
    end = os.fstat(fp.fileno()).st_size if headers_only else None
    while header := fp.read(RECORD.size):
        if len(header) != RECORD.size:
            raise CaptureError('truncated source record')
        at, size, raw = RECORD.unpack(header)
        if not previous <= at <= MAX_DURATION or not 0 < size <= MIB or raw > MIB:
            raise CaptureError('invalid source record')
        previous = at
        content = fp.read(size)
        if headers_only:
            if fp.tell() + raw > end:
                raise CaptureError('truncated source record')
            fp.seek(raw, os.SEEK_CUR)
        else:
            data = fp.read(raw)
            if len(data) != raw:
                raise CaptureError('truncated source record')
        if len(content) != size:
            raise CaptureError('truncated source record')
        event = json.loads(content)
        if not isinstance(event, list) or not event or (event[0] == 'media') != bool(raw):
            raise CaptureError('invalid source event')
        if raw and not headers_only:
            event.append(base64.b64encode(data).decode('ascii'))
        yield at, event


class ArchiveRenderer(Renderer):
    def __init__(self, root):
        super().__init__(root)
        for name, filename in {'/ingest': 'ingest.html', '/ingest.js': 'ingest.js', '/encoded.js': 'encoded.js',
                               '/replay.js': 'replay.js'}.items():
            self.static[name] = ('text/html' if filename.endswith('.html') else 'text/javascript',
                                 (Path(root) / filename).read_bytes())
        mime, body = self.static['/']
        self.static['/replay'] = (mime, body.replace(b'<script defer src="/livekit-client.umd.js"></script>', b'')
                                           .replace(b'/view.js', b'/replay.js'))

    @asynccontextmanager
    async def open(self, browser, *, capture=None, document=None, ingest=False):
        context = await browser.new_context(service_workers='block', accept_downloads=False, reduced_motion='reduce')
        try:
            if ingest:
                await context.grant_permissions(['local-network-access'], origin=ORIGIN)
            await context.route('**/*', lambda route: self.route(route, document, signaling=ingest))
            page = await context.new_page()
            if capture is not None:
                await page.expose_function('saveArchiveEvents', capture.chunk)
                page.on('pageerror', lambda _: capture.set_fault(CaptureError('capture script failed')))
            await page.goto(ORIGIN + ('/ingest' if ingest else '/replay'), wait_until='load', timeout=5000)
            yield page
        finally:
            await context.close()

    async def check(self, browser):
        async with self.open(browser, ingest=True) as page:
            if not await page.evaluate('ingest.ready()'):
                raise CaptureError('encoded receiver unavailable')
        await self.check_replay(browser)

    async def check_replay(self, browser):
        async with self.open(browser) as page:
            if not await page.evaluate('replay.ready()'):
                raise CaptureError('archive decoder or bundled font unavailable')


class SourceCapture:
    def __init__(self, browser, renderer, school, directory, room, budget):
        self.browser, self.renderer, self.school = browser, renderer, school
        self.path, self.room, self.job, self.budget = directory, room, directory.name, budget
        self.context = self.page = self.log = self.board_task = self.fault = None
        self.opened = self.board_seen = False
        self.documents = {}
        self.documents_open = True
        self.document_slot = asyncio.Semaphore(1)

    def set_fault(self, error):
        if self.fault is None:
            self.fault = error
        self.documents_open = False

    def record(self, rows):
        self.log.append(rows)

    async def layout_changed(self, archive):
        pass

    async def chunk(self, updates):
        try:
            if not self.log or self.fault or not isinstance(updates, list) or len(updates) > 768:
                raise CaptureError('invalid source batch')
            self.log.append(updates)
        except (CaptureError, OSError, ValueError, TypeError, IndexError) as error:
            self.set_fault(error)
            # Do not serialize captured data or tokens into an exception.
            raise CaptureError('source write failed') from None

    async def start(self, reply):
        self.context = self.renderer.open(self.browser, capture=self, ingest=True)
        self.page = await self.context.__aenter__()
        self.opened = True
        self.begin(reply)
        if not await asyncio.wait_for(self.page.evaluate('args => ingest.start(args)',
                {'url': SIGNAL, 'token': reply['token'], 'leaseMs': lease_ms(reply['lease'])}), 10):
            return False
        self.board_task = asyncio.create_task(self.watch_board())
        return True

    def header(self, reply, native=False):
        title = reply.get('title') or {}
        heading = {key: title[key][:n] for key, n in [('school', 150), ('subject', 100)] if isinstance(title.get(key), str)}
        meta = {'lesson': self.job, 'epoch': self.room[-24:], 'title': heading,
                'teacher': reply.get('teacher'), 'archive': reply.get('archive')}
        if native:
            meta['clock'] = 'rtcp-v1'
        return ['start', 0, 0, meta]

    def begin(self, reply):
        self.log = Timeline(self.path, self.budget)
        self.record([self.header(reply)])
        self.log.begin = time.monotonic()

    async def status(self):
        if self.fault:
            raise self.fault
        state = await asyncio.wait_for(self.page.evaluate('ingest.state()'), 2)
        if state['failure']:
            raise CaptureError('encoded receiver failed')
        return state

    async def renew(self, until):
        await asyncio.wait_for(self.page.evaluate('ms => ingest.renew(ms)', lease_ms(until)), 2)

    async def copy_doc(self, doc):
        async with self.document_slot:
            await self.download_doc(doc)

    def store_doc(self, path, data):
        with self.budget.writing(self.path, len(data)):
            temp = path.with_suffix('.tmp')
            with temp.open('wb') as fp:
                fp.write(data)
                fp.flush()
                os.fsync(fp.fileno())
            temp.replace(path)

    async def download_doc(self, doc):
        key, size, mime = doc.get('id'), doc.get('bytes'), doc.get('mime')
        if not HEX.fullmatch(key or '') or type(size) is not int or not 0 < size <= DOC_LIMIT or mime not in DOC_TYPES:
            raise CaptureError('invalid board attachment')
        path = self.path / f'doc-{key}.{mime.split("/")[1]}'
        def present():
            return path.is_file() and path.stat().st_size == size
        if await asyncio.to_thread(present):
            return
        for attempt in range(3):
            if not self.documents_open:
                break  # Never start a queued fetch or retry after close/revocation.
            try:
                code, headers, data = await asyncio.to_thread(self.school.document, self.job, self.room, key, None)
                if code == 200 and len(data) == size and headers.get('content-type', '').split(';')[0] == mime:
                    writing = asyncio.create_task(asyncio.to_thread(self.store_doc, path, data))
                    try:
                        await asyncio.shield(writing)
                    except asyncio.CancelledError:
                        await asyncio.gather(writing, return_exceptions=True)  # no writer after cleanup
                        raise
                    return
            except ProtocolError:
                pass
            if attempt < 2:
                await asyncio.sleep(.5)
        raise CaptureError('board file was not captured before closure')

    async def watch_board(self):
        rev, ink, previous, at = 0, 0, None, time.monotonic()
        try:
            while True:
                delay = .85
                try:
                    asked = self.log.elapsed()
                    result = await asyncio.to_thread(self.school, 'board', self.job,
                        room=self.room, board=rev, ink=ink, compact=True, wire=1)
                except ProtocolError as error:
                    if error.status in (403, 404, 410):
                        self.documents_open = False
                        return
                    # A close/epoch change is allowed to race with one final board read.
                    if time.monotonic() - at > 30:
                        raise CaptureError('board timeline not confirmed') from None
                else:
                    at = time.monotonic()
                    board, archive = result['board'], result['archive']
                    rev, ink = board['rev'], board['cursor']
                    stamp = (rev, ink, board.get('view'), board.get('laser'), archive)
                    if stamp != previous or board.get('ops'):
                        self.record([['board', 0, self.log.elapsed(), board, archive, asked]])
                        self.board_seen = True
                        await self.layout_changed(archive)
                    previous = stamp
                    if (doc := board.get('doc')) and doc['id'] not in self.documents:
                        if len(self.documents) >= 12:
                            raise CaptureError('too many board documents')
                        self.documents[doc['id']] = asyncio.create_task(self.copy_doc(doc))
                    for task in self.documents.values():
                        if task.done():
                            task.result()
                    if board.get('more'):
                        delay = .10
                    elif board.get('moving') or board.get('viewMoving') or board.get('laser'):
                        delay = .28
                await asyncio.sleep(delay)
        except (CaptureError, OSError, ValueError, KeyError, TypeError) as error:
            self.set_fault(error)

    async def stop(self, seal):
        self.documents_open = False
        ms = 0
        try:
            if self.board_task:
                self.board_task.cancel()
                await asyncio.gather(self.board_task, return_exceptions=True)
            ms = await self.finish_receiver(seal)
            if seal and self.fault:
                raise self.fault
            if not seal:
                for task in self.documents.values():
                    task.cancel()
            outcome = await asyncio.gather(*self.documents.values(), return_exceptions=True)
            if seal:
                for result in outcome:
                    if isinstance(result, BaseException):
                        raise CaptureError('board source was not finalized') from None
            if seal and (not self.board_seen or not 0 < ms <= MAX_DURATION):
                raise CaptureError('recorded source is incomplete')
        except BaseException:
            ms = 0
            for task in self.documents.values():
                task.cancel()
            await asyncio.gather(*self.documents.values(), return_exceptions=True)
            raise
        finally:
            try:
                await self.close_receiver()
            finally:
                await self.close_log(ms if seal else 0)

    async def close_log(self, ms):
        if self.log:
            log, self.log = self.log, None
            log.close(ms)

    async def finish_receiver(self, seal):
        if self.page:
            if not await asyncio.wait_for(self.page.evaluate('ingest.finish()'), 6) and seal:
                raise CaptureError('could not seal encoded input')
            if seal:
                return (await self.status())['ms']
        return 0

    async def close_receiver(self):
        if self.opened:
            await self.context.__aexit__(None, None, None)


class Documents:
    """The offline page can read only its own copied docs, never the school or SFU."""
    def __init__(self, path, scope):
        self.path, self.scope = path, scope

    async def resource(self, url, request):
        if url.path != '/document' or request.method != 'GET' or len(url.query) > 256:
            return 404, {}, b''
        query = parse_qs(url.query)
        doc = query.get('doc', [''])[0]
        if HEX.fullmatch(doc) and all(query.get(k) == [self.scope[k]] for k in ('lesson', 'epoch')):
            for mime in DOC_TYPES:
                path = self.path / f'doc-{doc}.{mime.split("/")[1]}'
                if not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= DOC_LIMIT:
                    # PDF.js has a complete-response fallback. This is a bounded local copy,
                    # not an extra download from the school's web host during rendering.
                    return 200, {'content-type': mime}, path.read_bytes()
        return 404, {}, b''
