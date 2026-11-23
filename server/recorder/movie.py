"""Offline archive compositor and single final H.264/AAC encode."""
import asyncio
import base64
from contextlib import suppress
import math
import os
import shutil

from archive import Documents, MAX_DURATION, SpaceError, duration, events, flush_dir, sources
from capture import FPS, HEX, CaptureError, clips
from clock import CLOCK, Clocks, MAX_SHIFT

SAMPLES_PER_FRAME = 48000 // FPS
SILENCE = bytes(2 * SAMPLES_PER_FRAME)
LOOKAHEAD_MS = 460  # Covers the live receiver batching window and bounded Opus clock rebase.


def encoder(ffmpeg, pattern, audio_fd, seconds):
    return [str(ffmpeg), '-nostdin', '-hide_banner', '-loglevel', 'error', '-n',
            '-thread_queue_size', '8', '-f', 'image2pipe', '-framerate', str(FPS), '-c:v', 'png', '-threads', '2',
            '-probesize', '32', '-analyzeduration', '1', '-i', 'pipe:0',
            '-thread_queue_size', '8', '-f', 's16le', '-ar', '48000', '-ac', '1', '-i', f'pipe:{audio_fd}',
            '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-threads', '2', '-filter_threads', '1',
            '-pix_fmt', 'yuv420p', '-preset', 'veryfast', '-crf', '22', '-maxrate', '640k', '-bufsize', '1280k', '-g', str(FPS * 10),
            '-force_key_frames', f'expr:gte(t,n_forced*{seconds})', '-c:a', 'aac', '-b:a', '64k', '-ac', '1',
            '-f', 'segment', '-segment_time', str(seconds), '-segment_time_delta', f'{1/FPS:.6f}',
            '-reset_timestamps', '1', '-segment_format', 'mp4', '-segment_format_options', 'movflags=+faststart', str(pattern)]


class AudioPipe(asyncio.Protocol):
    def __init__(self):
        self.ready = asyncio.Event()
        self.ready.set()
        self.error = None

    def pause_writing(self):
        self.ready.clear()

    def resume_writing(self):
        self.ready.set()

    def connection_lost(self, exc):
        self.error = exc or BrokenPipeError()
        self.ready.set()

    async def drain(self):
        await self.ready.wait()
        if self.error:
            raise self.error


async def encode_source(ffmpeg, page, updates, length, pattern, wait, budget, seconds, lookahead=LOOKAHEAD_MS):
    proc = transport = None
    audio_read, audio_write = os.pipe()
    try:
        try:
            proc = await asyncio.create_subprocess_exec(*encoder(ffmpeg, pattern, audio_read, seconds),
                    stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                    pass_fds=(audio_read,))
        finally:
            os.close(audio_read)
        with os.fdopen(audio_write, 'wb', buffering=0) as writer:
            audio_write = None
            transport, protocol = await asyncio.get_running_loop().connect_write_pipe(AudioPipe, writer)
            look = next(updates, None)
            image = b''
            for i in range(math.ceil(length * FPS / 1000)):
                # Renewed by the site even while this task is in flight. If a new class
                # opens, no new frame work is started; the bounded pipes drain and idle.
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
                frame = await asyncio.wait_for(page.evaluate('([i,updates]) => replay.frame(i,updates)', [i, batch]), 25)
                if frame['png'] is not None:
                    if not isinstance(frame['png'], str) or len(frame['png']) > 5 * 1024 * 1024:
                        raise CaptureError('invalid frame from compositor')
                    image = base64.b64decode(frame['png'], validate=True)
                if not image or not image.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise CaptureError('missing archive frame')
                pcm = base64.b64decode(frame['pcm'], validate=True) if frame['pcm'] else SILENCE
                if len(pcm) != len(SILENCE):
                    raise CaptureError('invalid archive audio')
                proc.stdin.write(image)
                transport.write(pcm)
                await asyncio.wait_for(asyncio.gather(proc.stdin.drain(), protocol.drain()), 20)
            proc.stdin.close()
            transport.close()
            if await asyncio.wait_for(proc.wait(), 30):
                raise CaptureError('MP4 encoder did not finalize')
    finally:
        if audio_write is not None:
            os.close(audio_write)
        if transport:
            transport.close()
        if proc and proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()


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
                    await asyncio.wait_for(page.evaluate('meta => replay.start(meta)', scope), 12)
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
