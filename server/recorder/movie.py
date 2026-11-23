"""Offline archive compositor and single final H.264/AAC encode."""
import asyncio
import base64
from contextlib import asynccontextmanager, suppress
import hashlib
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile
import zlib

from archive import Documents, MAX_DURATION, SpaceError, duration, events, flush_dir, sources
from capture import FPS, HEIGHT, HEX, WIDTH, CaptureError, clips
from clock import CLOCK, Clocks, MAX_SHIFT

SAMPLE_RATE = 48_000
SAMPLES_PER_FRAME = SAMPLE_RATE // FPS
SILENCE = bytes(2 * SAMPLES_PER_FRAME)
LOOKAHEAD_MS = 460  # Covers the live receiver batching window and bounded Opus clock rebase.


class RenderTimeout(TimeoutError):
    """Fixed stage labels only; never log contents of a frame, PDF, or FFmpeg stderr."""


async def bounded(awaitable, seconds, phase):
    try:
        return await asyncio.wait_for(awaitable, seconds)
    except TimeoutError:
        raise RenderTimeout(phase) from None


def _ebml_size(length):
    # A VINT with all data bits set is reserved for an unknown size.
    width = ((length + 1).bit_length() + 6) // 7
    return (1 << (7 * width) | length).to_bytes(width, 'big')


def _element(tag, payload):
    return tag.to_bytes((tag.bit_length() + 7) // 8, 'big') + _ebml_size(len(payload)) + payload


def _number(tag, value):
    return _element(tag, value.to_bytes(max(1, (value.bit_length() + 7) // 8), 'big'))


def _header():
    """A streaming Matroska segment, two fixed tracks, and no index or seeking."""
    ebml = _element(0x1A45DFA3, _number(0x4286, 1) + _number(0x42F7, 1) + _number(0x42F2, 4)
        + _number(0x42F3, 8) + _element(0x4282, b'matroska') + _number(0x4287, 4) + _number(0x4285, 2))
    segment = bytes.fromhex('1853806701ffffffffffffff')  # unknown Segment length, until stdin EOF
    info = _element(0x1549A966, _number(0x2AD7B1, 1000) + _element(0x4D80, b'Baci') + _element(0x5741, b'Baci'))

    def track(number, kind, codec):
        return _number(0xD7, number) + _number(0x73C5, number) + _number(0x83, kind) + _number(0x9C, 0) + _element(0x86, codec)

    # Matroska's VFW mapping uses a BITMAPINFOHEADER and the standard PNG fourcc.
    png = struct.pack('<IiiHH4sIiiII', 40, WIDTH, HEIGHT, 1, 24, b'MPNG', 0, 0, 0, 0, 0)
    video = track(1, 1, b'V_MS/VFW/FOURCC') + _element(0x63A2, png) + _number(0x23E383, 1_000_000_000 // FPS)
    video += _element(0xE0, _number(0xB0, WIDTH) + _number(0xBA, HEIGHT))
    audio = track(2, 2, b'A_PCM/INT/LIT') + _element(0xE1, _element(0xB5, struct.pack('>f', SAMPLE_RATE))
        + _number(0x9F, 1) + _number(0x6264, 16))
    return ebml + segment + info + _element(0x1654AE6B, _element(0xAE, video) + _element(0xAE, audio))


def _cluster(index, image, pcm):
    # Both independently decodable blocks start at the same cluster timestamp,
    # rounded to the nearest microsecond; no accumulated A/V rounding error.
    timestamp = _number(0xE7, (index * 1_000_000 + FPS // 2) // FPS)
    video = b'\xa3' + _ebml_size(4 + len(image)) + b'\x81\0\0\x80'
    audio = _element(0xA3, b'\x82\0\0\x80' + pcm)
    size = len(timestamp) + len(video) + len(image) + len(audio)
    return b'\x1f\x43\xb6\x75' + _ebml_size(size) + timestamp + video, image, audio


def encoder(ffmpeg, pattern, seconds):
    return [str(ffmpeg), '-nostdin', '-hide_banner', '-loglevel', 'error', '-n',
            '-thread_queue_size', '8', '-f', 'matroska', '-threads', '2',
            '-probesize', '32', '-analyzeduration', '1', '-i', 'pipe:0',
            '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-threads', '2', '-filter_threads', '1',
            '-pix_fmt', 'yuv420p', '-preset', 'veryfast', '-crf', '22', '-maxrate', '640k', '-bufsize', '1280k', '-g', str(FPS * 10),
            '-force_key_frames', f'expr:gte(t,n_forced*{seconds})', '-c:a', 'aac', '-b:a', '64k', '-ac', '1',
            '-f', 'segment', '-segment_time', str(seconds), '-segment_time_delta', f'{1/FPS:.6f}',
            '-reset_timestamps', '1', '-segment_format', 'mp4', '-segment_format_options', 'movflags=+faststart', str(pattern)]


@asynccontextmanager
async def encoder_pipe(ffmpeg, pattern, seconds):
    # With two input pipes, FFmpeg can wait for PCM during stream probing or
    # scheduling while the producer is blocked on PNG. No buffer increase fixes
    # that cycle. Send one bounded pair at a time to a single demuxer instead.
    proc = await asyncio.create_subprocess_exec(*encoder(ffmpeg, pattern, seconds),
        stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL)
    try:
        proc.stdin.write(_header())

        async def write(index, image, pcm):
            for chunk in _cluster(index, image, pcm):
                proc.stdin.write(chunk)
            await bounded(proc.stdin.drain(), 20, 'encoder-feed')

        yield write
        proc.stdin.close()
        if await bounded(proc.wait(), 30, 'encoder-finish'):
            raise CaptureError('MP4 encoder did not finalize')
    finally:
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.kill()
        # Reap before the per-job staging directory can be removed or reused.
        await proc.wait()
        with suppress(BrokenPipeError, ConnectionResetError):
            await proc.stdin.wait_closed()


async def encode_source(ffmpeg, page, updates, length, pattern, wait, budget, seconds, lookahead=LOOKAHEAD_MS):
    async with encoder_pipe(ffmpeg, pattern, seconds) as write:
        look = next(updates, None)
        image = b''
        for i in range(math.ceil(length * FPS / 1000)):
            # Continue to check the school's short permit before any frame work.
            await wait()
            budget.writing_film()
            batch, media_bytes = [], 0
            while look and look[0] <= i*1000/FPS + lookahead:
                batch.append(look[1])
                if look[1][0] == 'media':
                    media_bytes += len(look[1][-1])
                if len(batch) > 5000 or media_bytes > 8*1024*1024:
                    raise CaptureError('overfull archive window')
                look = next(updates, None)
            frame = await bounded(page.evaluate('([i,updates]) => replay.frame(i,updates)', [i, batch]), 25, 'replay-frame')
            if frame['png'] is not None:
                if not isinstance(frame['png'], str) or len(frame['png']) > 5 * 1024 * 1024:
                    raise CaptureError('invalid frame from compositor')
                image = base64.b64decode(frame['png'], validate=True)
            if not image or not image.startswith(b'\x89PNG\r\n\x1a\n'):
                raise CaptureError('missing archive frame')
            pcm = base64.b64decode(frame['pcm'], validate=True) if frame['pcm'] else SILENCE
            if len(pcm) != len(SILENCE):
                raise CaptureError('invalid archive audio')
            await write(i, image, pcm)


def _probe_image():
    """A valid, incompressible frame larger than a pipe: no classroom content."""
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    pixels = hashlib.shake_128(b'Baci MP4 encoder self-test').digest(WIDTH * HEIGHT * 3)
    scanlines = b''.join(b'\0' + pixels[i:i + WIDTH * 3] for i in range(0, len(pixels), WIDTH * 3))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', WIDTH, HEIGHT, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(scanlines, 1)) + chunk(b'IEND', b''))


async def check_encoder(ffmpeg):
    image = await asyncio.to_thread(_probe_image)
    with tempfile.TemporaryDirectory(prefix='baci-encoder-check-') as temp:
        path = Path(temp)
        async with encoder_pipe(ffmpeg, path / 'clip-000-%03d.mp4', 450) as write:
            for index in range(4):
                await write(index, image, SILENCE)
        result = clips(path)
        if len(result) != 1:
            raise CaptureError('encoder self-test did not produce one MP4')
        with result[0].open('rb') as fp:
            head = fp.read(65536)  # MP4 faststart puts both track descriptions near the beginning.
        if b'avc1' not in head or b'mp4a' not in head:
            raise CaptureError('encoder self-test has no H.264/AAC tracks')


async def render(browser, renderer, ffmpeg, path, wait, budget, seconds=450):
    inputs = sources(path)
    if not inputs or (path / '.capturing').exists():
        raise CaptureError('no sealed source for this bell')
    await wait()
    stage = path / '.render'
    # An interruption can only leave uncommitted output here or next to the source.
    # The caller invokes this only before a durable part index or a send permit exists.
    if stage.exists():
        shutil.rmtree(stage)
    for old in path.glob('clip-*.mp4'):
        old.unlink()
    budget.measured = -float('inf')
    if not budget.can_render():
        raise SpaceError('insufficient temporary space to make the film')
    stage.mkdir(mode=0o700)
    total_ms = 0
    try:
        for n, source in enumerate(inputs):
            await wait()
            with source.open('rb') as fp:
                length = duration(fp)
                total_ms += length
                tape = events(fp)
                first = next(tape, (0, []))[1]
                if first[:3] != ['start', 0, 0] or len(first) != 4 or total_ms > MAX_DURATION:
                    raise CaptureError('invalid archive start')
                scope = first[3]
                if scope.get('lesson') != path.name or not isinstance(scope.get('epoch'), str) or not HEX.fullmatch(scope['epoch'] + '0'*8):
                    raise CaptureError('source belongs to a different room')
                lookahead = LOOKAHEAD_MS
                if scope.get('clock') == CLOCK:
                    # Sample reports and normalize media timestamps only off-peak. The
                    # bounded replay lookahead covers reordering from that calibration.
                    header_end = fp.tell()
                    clocks = await Clocks.scan(fp, length, wait)
                    fp.seek(header_end)
                    tape = clocks.apply(events(fp))
                    lookahead += MAX_SHIFT
                async with renderer.open(browser, document=Documents(path, scope)) as page:
                    await bounded(page.evaluate('meta => replay.start(meta)', scope), 12, 'replay-init')
                    await encode_source(ffmpeg, page, tape, length, stage / f'clip-{n:03d}-%03d.mp4', wait, budget, seconds, lookahead)
        await wait()
        result = clips(stage)
        if not result or sum(part.stat().st_size for part in result) > 768 * 1024 * 1024:
            raise CaptureError('invalid film size')
        for part in result:
            await wait()
            with part.open('rb') as fp:
                os.fsync(fp.fileno())
            part.replace(path / part.name)
        flush_dir(path)
    finally:
        shutil.rmtree(stage)
    return clips(path)
