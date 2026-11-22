"""The isolated Chromium compositor and streaming MP4 encoder."""
import asyncio
import base64
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

from transport import PART_LIMIT, ProtocolError

# No HTTP server is exposed. Playwright fulfills this synthetic origin in the managed browser.
ORIGIN = 'http://recorder.localhost'
SIGNAL = 'ws://127.0.0.1:7880'
HEX = re.compile(r'[a-f0-9]{32}\Z')
CLIP = re.compile(r'clip-[0-9]{3}-[0-9]{3}\.mp4\Z')
PDF_PATH = re.compile(r'^/pdf/((?:runtime|cmaps|standard_fonts|wasm|iccs)/[\w.-]+)$', re.ASCII)
CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' blob: data:; "
       "font-src 'self' blob: data:; media-src 'self' blob:; worker-src 'self'; "
       "connect-src 'self' ws://127.0.0.1:7880 http://127.0.0.1:7880; frame-ancestors 'none'; base-uri 'none'")
WIDTH, HEIGHT, FPS = 1280, 720, 12
ASSETS = {'/': ('view.html', 'text/html'), '/view.css': ('view.css', 'text/css'),
          '/frame.js': ('frame.js', 'text/javascript'), '/view.js': ('view.js', 'text/javascript'),
          '/board.js': ('board.js', 'text/javascript'), '/livekit-client.umd.js': ('livekit-client.umd.js', 'text/javascript'),
          '/fonts/Vazirmatn-Regular.woff2': ('fonts/Vazirmatn-Regular.woff2', 'font/woff2'),
          '/fonts/Vazirmatn-Bold.woff2': ('fonts/Vazirmatn-Bold.woff2', 'font/woff2')}


def lease_ms(until):
    return max(0, min(7800, round((until - time.time()) * 1000)))


def encoder(ffmpeg, pattern, seconds=450):
    # 450 s at 640k video + 64k audio is ~39.6 MB before VBV/MP4 overhead,
    # leaving margin below the 45 MB per-part limit even with continuous motion.
    return [str(ffmpeg), '-nostdin', '-hide_banner', '-loglevel', 'error', '-n', '-f', 'webm',
            '-probesize', '512k', '-analyzeduration', '1200000', '-threads', '2', '-i', 'pipe:0',
            '-map', '0:v:0', '-map', '0:a:0', '-t', '7250', '-vf', f'fps={FPS}:start_time=0',
            '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', '-preset', 'veryfast',
            '-crf', '22', '-maxrate', '640k', '-bufsize', '1280k', '-g', str(FPS * 10),
            '-force_key_frames', f'expr:gte(t,n_forced*{seconds})',
            '-c:a', 'aac', '-b:a', '64k', '-ac', '1', '-ar', '48000', '-f', 'segment', '-segment_time', str(seconds),
            # AAC priming and stream timestamp rounding can shift a forced keyframe
            # just before the requested cut. Accept it instead of doubling a part.
            '-segment_time_delta', f'{1/FPS:.6f}', '-reset_timestamps', '1', '-segment_format', 'mp4',
            '-segment_format_options', 'movflags=+faststart', str(pattern)]


class CaptureError(Exception):
    pass


class Renderer:
    def __init__(self, root):
        self.pdf = ZipFile(Path(root) / 'pdf-resources.zip')
        self.static = {name: (kind, (Path(root) / filename).read_bytes()) for name, (filename, kind) in ASSETS.items()}

    async def route(self, route, capture=None):
        req = route.request
        path = urlsplit(req.url)
        # The LiveKit SDK's fallback /validate may read only the loopback SFU.
        if path.netloc == '127.0.0.1:7880' and path.scheme == 'http' and path.path in ('/rtc/validate', '/rtc/v1/validate'):
            await route.continue_()
            return
        if f'{path.scheme}://{path.netloc}' != ORIGIN:
            await route.abort()
            return
        code, headers, raw = 404, {}, b''
        if req.method == 'GET' and not path.query and path.path in self.static:
            mime, raw = self.static[path.path]
            code, headers = 200, {'content-type': mime + '; charset=utf-8' if mime.startswith('text/') else mime}
            if path.path == '/':
                headers['content-security-policy'] = CSP
        elif req.method == 'GET' and not path.query and (match := PDF_PATH.fullmatch(path.path)):
            name = match[1]
            try:
                raw = self.pdf.read(name)
                mime = 'text/javascript' if name.endswith(('.js', '.mjs')) else 'application/wasm' if name.endswith('.wasm') else 'application/octet-stream'
                code, headers = 200, {'content-type': mime}
            except KeyError:
                pass
        elif capture is not None:
            code, headers, raw = await capture.resource(path, req)
        try:
            await route.fulfill(status=code, headers={'cache-control': 'no-store', 'x-content-type-options': 'nosniff', **headers}, body=raw)
        except Exception:
            # The browser may cancel a range/delta request while changing PDF pages.
            try:
                await route.abort()
            except Exception:
                pass

    async def check(self, browser):
        async with view(browser, self) as page:
            if not await page.evaluate('recorder.ready()'):
                raise CaptureError('renderer codec or bundled font unavailable')


class view:
    def __init__(self, browser, renderer, capture=None):
        self.browser, self.renderer, self.capture = browser, renderer, capture

    async def __aenter__(self):
        self.context = await self.browser.new_context(viewport={'width': WIDTH, 'height': HEIGHT}, service_workers='block',
                                                      accept_downloads=False, reduced_motion='reduce')
        try:
            # Chromium treats a fulfilled page as a different address space from the loopback SFU.
            # Scope permission to this synthetic origin; CSP and routing still allow only loopback signaling.
            await self.context.grant_permissions(['local-network-access'], origin=ORIGIN)
            await self.context.route('**/*', lambda route: self.renderer.route(route, self.capture))
            page = await self.context.new_page()
            if self.capture:
                await page.expose_function('saveRecordingChunk', self.capture.chunk)
                page.on('pageerror', lambda _: setattr(self.capture, 'fault', True))
            await page.goto(ORIGIN + '/', wait_until='load', timeout=5000)
            return page
        except BaseException:
            await self.context.close()
            raise

    async def __aexit__(self, *unused):
        await self.context.close()


class Capture:
    def __init__(self, browser, renderer, school, room, job, directory, ffmpeg):
        self.view = view(browser, renderer, self)
        self.school, self.room, self.job = school, room, job
        self.directory, self.ffmpeg = directory, ffmpeg
        self.marker = directory / '.capturing'
        self.page = self.process = None
        self.fault = self.bytes = 0

    async def resource(self, path, req):
        try:
            if path.path == '/board' and not path.query and req.method == 'POST' and len(req.post_data or '') <= 1024:
                data = json.loads(req.post_data)
                board, ink = data.get('board'), data.get('ink')
                if all(type(n) is int and 0 <= n <= 2147483647 for n in (board, ink)):
                    result = await asyncio.to_thread(self.school, 'board', self.job, room=self.room, board=board, ink=ink, compact=True)
                    return 200, {'content-type': 'application/json'}, json.dumps(result).encode()
            if path.path == '/document' and req.method == 'GET' and len(path.query) < 256:
                query = parse_qs(path.query, max_num_fields=4)
                docs = query.get('doc', [])
                if len(docs) == 1 and HEX.fullmatch(docs[0]) and query.get('lesson') == [self.job] and query.get('epoch') == [self.room[-24:]]:
                    code, headers, raw = await asyncio.to_thread(self.school.document, self.job, self.room, docs[0], req.headers.get('range'))
                    if code in (200, 206, 416):
                        return code, {k: v for k, v in headers.items() if k in ('content-type', 'content-range', 'accept-ranges')}, raw
        except (ProtocolError, ValueError, AttributeError, TypeError):
            pass
        return 409, {}, b''

    async def chunk(self, encoded):
        try:
            if not isinstance(encoded, str) or not 0 < len(encoded) <= 1_400_000 or self.process.returncode is not None:
                raise CaptureError('bad encoder chunk')
            packet = base64.b64decode(encoded, validate=True)
            self.process.stdin.write(packet)
            await asyncio.wait_for(self.process.stdin.drain(), timeout=4)
            self.bytes += len(packet)
        except (OSError, ValueError, CaptureError, TimeoutError):
            self.fault = True
            raise CaptureError('encoder input failed') from None

    async def start(self, token, lease, title=None):
        parts = sorted(p.name for p in self.directory.glob('clip-*') if CLIP.fullmatch(p.name))
        generation = int(parts[-1][5:8]) + 1 if parts else 0
        if len(parts) >= 32 or generation >= 32 or self.marker.exists():
            raise CaptureError('capture limit or interrupted segment')
        self.marker.write_text('encoding\n', encoding='ascii')
        self.process = await asyncio.create_subprocess_exec(*encoder(self.ffmpeg, self.directory / f'clip-{generation:03d}-%03d.mp4'),
                                                            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                                                            stderr=asyncio.subprocess.DEVNULL)
        try:
            self.page = await self.view.__aenter__()
            # Optional protocol-v1 metadata, bounded plain text. Older sites omit it.
            names = {key: title[key][:limit] for key, limit in [('school', 150), ('subject', 100)]
                     if isinstance(title, dict) and isinstance(title.get(key), str)}
            return await asyncio.wait_for(self.page.evaluate('config => recorder.start(config)',
                {'url': SIGNAL, 'token': token, 'lesson': self.job, 'epoch': self.room[-24:],
                 'leaseMs': lease_ms(lease), 'title': names}), 12)
        except Exception:
            raise CaptureError('renderer did not start') from None

    async def running(self):
        if self.fault or self.process.returncode is not None:
            raise CaptureError('capture stopped unexpectedly')
        try:
            state = await asyncio.wait_for(self.page.evaluate('recorder.state()'), 2)
        except Exception:
            raise CaptureError('renderer did not respond') from None
        if state['failure']:
            raise CaptureError('compositor failed')
        return state['running']

    async def renew(self, lease):
        try:
            await asyncio.wait_for(self.page.evaluate('ms => recorder.renew(ms)', lease_ms(lease)), 2)
        except Exception:
            raise CaptureError('renderer did not respond') from None

    async def stop(self, verify=True):
        valid = not self.fault
        try:
            if self.page:
                valid = await asyncio.wait_for(self.page.evaluate('recorder.finish()'), 7) and valid
        except Exception:
            valid = False
        finally:
            if self.page:
                try:
                    await asyncio.wait_for(self.view.__aexit__(), 5)
                except Exception:
                    self.fault = True
                    valid = False
            if self.process:
                self.process.stdin.close()
                try:
                    await asyncio.wait_for(self.process.wait(), 15)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
                valid = valid and (self.bytes == 0 or self.process.returncode == 0)
        if verify and not valid:
            raise CaptureError('encoder did not finalize')
        if verify and self.directory.exists():
            self.marker.unlink(missing_ok=True)
            clips(self.directory)


def check_clip(path):
    if not CLIP.fullmatch(path.name) or path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= PART_LIMIT:
        raise CaptureError('video part invalid')
    with path.open('rb') as sample:
        if sample.read(8)[4:] != b'ftyp':
            raise CaptureError('video part not finalized')
    return path


def clips(directory):
    files = sorted(directory.glob('clip-*'))
    if len(files) > 32:
        raise CaptureError('too many video parts')
    return [check_clip(path) for path in files]
