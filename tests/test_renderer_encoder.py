"""Regression for the offline PNG/PCM pipe deadlock; synthetic data only."""
import asyncio
import base64
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server/recorder'))
import movie
from archive import ArchiveRenderer, Budget, Timeline
from capture import FPS, HEIGHT, WIDTH, clips
from postprocess import PostProcess, request_render

FFMPEG = os.environ.get('MPM_REC_FFMPEG') or shutil.which('ffmpeg')
FFPROBE = os.environ.get('MPM_TEST_FFPROBE') or shutil.which('ffprobe')
CHROMIUM = os.environ.get('MPM_TEST_CHROMIUM')
PUBLISHED = ROOT / 'artifacts/baci-classroom-recorder-v0.4.23.zip'
JOB = 'a' * 32


class EncoderCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def stall(self, during, *, cancel=False):
        entered, processes = asyncio.Event(), []
        spawn, bounded = asyncio.create_subprocess_exec, movie.bounded
        script = "import sys,time; sys.stdin.buffer.read(); time.sleep(30)" if during == 'encoder-finish' else 'import time; time.sleep(30)'
        image = await asyncio.to_thread(movie._probe_image)

        async def record(*args, **kwargs):
            proc = await spawn(*args, **kwargs)
            processes.append(proc)
            self.addAsyncCleanup(proc.wait)
            self.addCleanup(lambda: proc.kill() if proc.returncode is None else None)
            return proc

        async def observe(awaitable, seconds, phase):
            if phase == during:
                entered.set()
                seconds = .2 if not cancel else seconds
            return await bounded(awaitable, seconds, phase)

        async def encode():
            async with movie.encoder_pipe('/not-used', 'not-created-%03d.mp4', 450) as write:
                await write(0, image, movie.SILENCE)

        with patch.object(movie, 'encoder', return_value=[sys.executable, '-c', script]), \
                patch.object(asyncio, 'create_subprocess_exec', side_effect=record), patch.object(movie, 'bounded', side_effect=observe):
            task = asyncio.create_task(encode())
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if cancel:
                    task.cancel()  # Like a revoked renderer lease while the pipe is full.
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 2)
                else:
                    with self.assertRaisesRegex(movie.RenderTimeout, f'^{during}$'):
                        await asyncio.wait_for(task, 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        [proc] = processes
        self.assertIsNotNone(proc.returncode)
        self.assertTrue(proc.stdin.is_closing())

    async def test_stalled_feed_reports_stage_and_reaps_encoder(self):
        await self.stall('encoder-feed')

    async def test_stalled_finish_reports_stage_and_reaps_encoder(self):
        await self.stall('encoder-finish')

    async def test_revocation_during_feed_reaps_encoder(self):
        await self.stall('encoder-feed', cancel=True)


@unittest.skipUnless(FFMPEG, 'needs FFmpeg (MPM_REC_FFMPEG) with libx264 and AAC')
class RealEncoderTest(unittest.IsolatedAsyncioTestCase):
    async def test_probe_encodes_large_png_and_pcm(self):
        await asyncio.wait_for(movie.check_encoder(FFMPEG), 9)

    @unittest.skipUnless(FFPROBE, 'needs ffprobe (MPM_TEST_FFPROBE)')
    async def test_large_frames_do_not_block_and_parts_keep_av_timing(self):
        image = base64.b64encode(await asyncio.to_thread(movie._probe_image)).decode()
        self.assertGreater(len(image), 1_000_000)  # Much more than the pipe + asyncio buffer.
        tone = base64.b64encode(struct.pack('<4000h', *(int(6000 * math.sin(math.tau * i / 100)) for i in range(4000)))).decode()
        checked = 0

        async def guard():
            nonlocal checked
            checked += 1

        class FramePage:
            async def evaluate(self, _, args):
                index, batch = args
                assert batch == []
                return {'png': image if index == 0 else None, 'pcm': tone if index >= 6 else None}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            await asyncio.wait_for(movie.encode_source(FFMPEG, FramePage(), iter(()), 3500,
                path / 'clip-000-%03d.mp4', guard, Budget(path), 2), 15)
            parts = clips(path)
            self.assertEqual(len(parts), 2)
            self.assertEqual(checked, 42)
            frame_count = 0
            for part in parts:
                raw = await asyncio.to_thread(subprocess.check_output,
                    [FFPROBE, '-v', 'error', '-show_streams', '-of', 'json', str(part)], timeout=5)
                video, audio = json.loads(raw)['streams']
                self.assertEqual((video['codec_name'], video['width'], video['height'], video['r_frame_rate']), ('h264', WIDTH, HEIGHT, f'{FPS}/1'))
                self.assertEqual((audio['codec_name'], audio['sample_rate'], audio['channels']), ('aac', '48000', 1))
                # Keep the existing segmenter's H.264 reordering and AAC packet padding.
                self.assertAlmostEqual(float(video['duration']), float(audio['duration']), delta=2 / FPS + 1024 / 48000)
                self.assertAlmostEqual(float(video['start_time']), float(audio['start_time']), delta=1 / FPS)
                frame_count += int(video['nb_frames'])
            self.assertEqual(frame_count, 42)


OPUS_FIXTURE = '''async () => {
    const packets = [];
    let error = false;
    const encoder = new AudioEncoder({error: () => { error = true; }, output: packet => {
        const bytes = new Uint8Array(packet.byteLength);
        packet.copyTo(bytes);
        packets.push([Math.max(0, Math.round(packet.timestamp / 1000)), bytes.toBase64()]);
    }});
    encoder.configure({codec:'opus', sampleRate:48000, numberOfChannels:1, bitrate:32000});
    for (let i=0; i<150; i++) {
        const data = Float32Array.from({length:960}, (_,j) => .2*Math.sin(2*Math.PI*480*(i*960+j)/48000));
        const frame = new AudioData({data,format:'f32',sampleRate:48000,numberOfChannels:1,numberOfFrames:960,timestamp:i*20000});
        encoder.encode(frame);
        frame.close();
    }
    await encoder.flush();
    encoder.close();
    if (error || !packets.length) throw new Error('synthetic Opus encoding is unavailable');
    return packets;
}'''


def seal_fixture(spool, packets):
    path = spool / JOB
    path.mkdir(mode=0o700)
    png = movie._probe_image()
    key = 'b' * 32
    (path / f'doc-{key}.png').write_bytes(png)
    archive = {'board': True, 'screen': None, 'screenOwner': None}
    state = {'rev': 1, 'cursor': 0, 'reset': True, 'ops': [], 'docs': [], 'page': 1,
             'doc': {'id': key, 'mime': 'image/png', 'name': 'Synthetic.png', 'bytes': len(png), 'width': WIDTH, 'height': HEIGHT}}
    log = Timeline(path, Budget(spool), indexed=True)
    log.elapsed = lambda: 0
    log.append([['start', 0, 0, {'lesson': JOB, 'epoch': 'c' * 24, 'title': {'subject': 'آزمایش'},
                                'teacher': 'teacher-1', 'clock': 'rtcp-v1', 'archive': archive}],
                ['board', 0, 0, state, archive, 0], ['open', 1, 0, 'microphone', 'teacher-1', 'PA_test', 'TR_test', False]])
    log.append([['media', 1, ms, 'audio/opus', ms * 48, True, raw] for ms, raw in packets])
    log.close(3300)
    return path


@unittest.skipUnless(CHROMIUM and FFMPEG, 'needs local Chromium (MPM_TEST_CHROMIUM), Playwright and FFmpeg')
class BrowserReplayTest(unittest.IsolatedAsyncioTestCase):
    async def test_archived_board_and_opus_replay_export_through_lease(self):
        from playwright.async_api import async_playwright

        launch = {'executable_path': CHROMIUM, 'chromium_sandbox': True, 'args': ['--autoplay-policy=no-user-gesture-required']}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets, spool, socket = root / 'assets', root / 'spool', root / 'render.sock'
            spool.mkdir(mode=0o700)
            with ZipFile(PUBLISHED) as archive:
                archive.extractall(assets)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(**launch)
                try:
                    async with ArchiveRenderer(assets).open(browser) as page:
                        packets = await page.evaluate(OPUS_FIXTURE)
                finally:
                    await browser.close()
            path = await asyncio.to_thread(seal_fixture, spool, packets)
            source = path / 'source-000.baci'
            original = source.read_bytes()
            offline = PostProcess(spool, FFMPEG, assets=assets, launch=launch)
            async with await asyncio.start_unix_server(offline.handle, path=socket, limit=512):
                await asyncio.wait_for(request_render(socket, JOB, lambda: time.monotonic() + 4), 45)
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((path / '.render').exists())
            [part] = clips(path)
            raw = await asyncio.to_thread(subprocess.check_output, [FFMPEG, '-v', 'error', '-i', str(part), '-map', '0:a:0',
                '-f', 's16le', '-'], timeout=8)
            pcm = [val for val, in struct.iter_unpack('<h', raw)]
            self.assertGreater(len(pcm), 150_000)
            self.assertGreater(max(pcm), 2000)  # AAC includes the decoded Opus tone, not only inserted silence.
            pixels = await asyncio.to_thread(subprocess.check_output, [FFMPEG, '-v', 'error', '-i', str(part), '-map', '0:v:0',
                '-frames:v', '1', '-pix_fmt', 'rgb24', '-threads:v', '1', '-f', 'rawvideo', '-'], timeout=8)
            self.assertEqual(len(pixels), WIDTH * HEIGHT * 3)
            start = (320 * WIDTH + 500) * 3
            board = pixels[start:start + 280 * 3:3]
            self.assertGreater(len(set(board)), 20)  # The noisy archived document reached the film.


if __name__ == '__main__':
    unittest.main()
