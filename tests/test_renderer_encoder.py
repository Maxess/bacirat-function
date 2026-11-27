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
PUBLISHED = ROOT / 'artifacts/baci-classroom-recorder-v0.4.23-r3.zip'
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
    encoder.configure({codec:'opus', sampleRate:48000, numberOfChannels:1, bitrate:12000});
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


def opus_with_gap(packets):
    # Receiver batching and the RTP clock are separate. Use a wrap and a gap
    # inside the first replay batch (within the 780 ms native lookahead).
    return [['media', 1, ms + 20, 'audio/opus', (0xffffefa0 + ms * 48) & 0xffffffff, True, raw]
            for ms, raw in packets if not 320 <= ms < 420]


OPUS_REPLAY_PCM = '''async ({packets, paced}) => {
    await replay.start({lesson:'a'.repeat(32), epoch:'c'.repeat(24), clock:'rtcp-v1'});
    const output = new Uint8Array(40 * 8000);
    let cursor = 0;
    for (let index=0; index<40; index++) {
        const batch=[];
        while (cursor < packets.length && (!paced || packets[cursor][2] <= index*1000/12 + 760)) {
            batch.push(packets[cursor++]);
        }
        const frame = await replay.frame(index, batch);
        if (frame.pcm) output.set(Uint8Array.fromBase64(frame.pcm),index*8000);
    }
    return output.toBase64();
}'''


CAMERA_REPLAY = '''async () => {
    const video = async color => {
        const canvas = document.createElement('canvas'); canvas.width = 64; canvas.height = 48;
        const ctx = canvas.getContext('2d'); ctx.fillStyle = color; ctx.fillRect(0,0,64,48);
        let encoded, failure;
        const encoder = new VideoEncoder({output(chunk) {
            const bytes = new Uint8Array(chunk.byteLength); chunk.copyTo(bytes); encoded = bytes.toBase64();
        }, error(error) { failure = error; }});
        encoder.configure({codec:'vp8',width:64,height:48,framerate:12,latencyMode:'realtime'});
        const frame = new VideoFrame(canvas,{timestamp:0});
        encoder.encode(frame,{keyFrame:true}); frame.close(); await encoder.flush(); encoder.close();
        if (!encoded || failure) throw new Error('VP8 fixture failed');
        return encoded;
    };
    const [teacher, cam, screen] = await Promise.all(['#3030ee','#e000ce','#25dc30'].map(video));
    const prefs = {board:true,screen:'TR_screen',screenOwner:{identity:'teacher-1',sid:'PA_teacher'},
                   camera:{identity:'student-1',sid:'PA_pupil',track:'TR_pupil'}};
    await replay.start({lesson:'a'.repeat(32),epoch:'b'.repeat(24),teacher:'teacher-1',archive:prefs});
    const source = (id, at, type, who, sid, track, data) => [
        ['open',id,at,type,who,sid,track,false], ['media',id,at,'video/vp8',at*90,true,data]];
    const first = [...source(1,0,'camera','teacher-1','PA_teacher','TR_teacher',teacher),
        ...source(2,0,'camera','student-1','PA_pupil','TR_pupil',cam),
        ...source(3,0,'screen_share','teacher-1','PA_teacher','TR_screen',screen)];
    const change = (at, archive) => ['board',0,at,{rev:1,cursor:0,reset:true,ops:[],docs:[],page:1,doc:null},archive,at];
    const nextPrefs = {...prefs,camera:{identity:'student-1',sid:'PA_next',track:'TR_next'}};
    const cases = [first, [['mute',2,60,true]], [['end',2,140], ...source(4,140,'camera','student-1','PA_next','TR_next',cam)],
        [change(230,{...prefs,camera:{...prefs.camera,track:'TR_next'}})], [change(315,nextPrefs)],
        [change(395,{...nextPrefs,screen:null})]];
    const ctx = document.getElementById('film').getContext('2d');
    const sample = x => [...ctx.getImageData(x,285,1,1).data].slice(0,3);
    const pixels = [];
    for (const [i, updates] of cases.entries()) {
        await replay.frame(i, updates); pixels.push([sample(300),sample(940)]);
    }
    return pixels;
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
    for event in opus_with_gap(packets):
        # Simulate transport batches rather than exposing the whole bell at frame zero.
        log.elapsed = lambda at=event[2]: (at // 120 + 1) * 120
        log.append([event])
    log.append([['end', 1, 3180]])
    log.close(3300)
    return path


@unittest.skipUnless(CHROMIUM and FFMPEG, 'needs local Chromium (MPM_TEST_CHROMIUM), Playwright and FFmpeg')
class BrowserReplayTest(unittest.IsolatedAsyncioTestCase):
    async def test_continuous_opus_has_no_clicks_at_batch_or_clock_adjustments(self):
        from playwright.async_api import async_playwright

        with tempfile.TemporaryDirectory() as tmp, ZipFile(PUBLISHED) as archive:
            assets = Path(tmp)
            archive.extractall(assets)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(executable_path=CHROMIUM, chromium_sandbox=True)
                try:
                    renderer = ArchiveRenderer(assets)
                    async with renderer.open(browser) as page:
                        packets = await page.evaluate(OPUS_FIXTURE)

                    async def pcm(skew, paced):
                        # Every 20 ms packet is present. A sender report can shift
                        # the estimated time while the RTP sample count is continuous.
                        updates = [['media', 1, ms + 40 + skew * min(35, max(0, (ms - 60) // 4)), 'audio/opus',
                                    (0xffffff60 + ms * 48) & 0xffffffff, True, raw] for ms, raw in packets]
                        async with renderer.open(browser) as page:
                            encoded = await page.evaluate(OPUS_REPLAY_PCM, {'packets': updates, 'paced': paced})
                        return [val for val, in struct.iter_unpack('<h', base64.b64decode(encoded))]

                    reference = await pcm(0, False)
                    self.assertTrue(await pcm(0, True) == reference, 'batching must not alter continuous Opus audio')

                    def pulse_peak(values):
                        # Second difference detects sharp edges but not the steady tone.
                        # Inspect only the middle; no leading/trailing pauses are involved.
                        return max(abs(values[i - 1] - 2 * values[i] + values[i + 1]) for i in range(24_000, 132_000))

                    def endpoint(values):
                        return next(i for i in range(len(values) - 1, -1, -1) if abs(values[i]) > 2000)

                    for skew in (-1, 1):
                        with self.subTest(skew=skew):
                            adjusted = await pcm(skew, True)
                            self.assertGreater((endpoint(adjusted) - endpoint(reference)) * skew, 160,
                                               'the soundtrack still follows the corrected clock')
                            self.assertLess(pulse_peak(adjusted), 5 * pulse_peak(reference),
                                            'a skew correction must not stamp a sharp peak over voiced samples')
                finally:
                    await browser.close()

    async def test_large_camera_obeys_the_recorded_publication_and_yields_on_mute(self):
        from playwright.async_api import async_playwright
        with tempfile.TemporaryDirectory() as tmp, ZipFile(PUBLISHED) as archive:
            archive.extractall(tmp)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(executable_path=CHROMIUM, chromium_sandbox=True)
                try:
                    async with ArchiveRenderer(Path(tmp)).open(browser) as page:
                        samples = await page.evaluate(CAMERA_REPLAY)
                finally:
                    await browser.close()
        # A pink camera replaces the right board, while green screen remains at left.
        first, muted, new_track, wrong_sid, restored, no_screen = samples
        pink = lambda rgb: rgb[0] > 170 and rgb[1] < 65 and rgb[2] > 130
        green = lambda rgb: rgb[1] > 170 and rgb[0] < 75 and rgb[2] < 75
        self.assertTrue(green(first[0]) and pink(first[1]), first)
        for pair in (muted, new_track, wrong_sid):
            self.assertTrue(green(pair[0]) and not pink(pair[1]), pair)
        self.assertEqual(muted[1], new_track[1], 'the same board returns without a focused camera')
        self.assertTrue(green(restored[0]) and pink(restored[1]), restored)
        self.assertTrue(no_screen[0][2] > 160 and pink(no_screen[1]), no_screen)

    async def test_archived_board_audio_and_full_persian_credit_in_mp4(self):
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
                        credit = await page.evaluate('''async () => {
                            const ctx = document.getElementById('film').getContext('2d');
                            let credit;
                            const fill = ctx.fillText.bind(ctx);
                            ctx.fillText = (text,x,y) => {
                                fill(text,x,y);
                                if (y !== 29 || !text.startsWith('طراحی و توسعه')) return;
                                const m = ctx.measureText(text);
                                const left = Math.floor(x-m.actualBoundingBoxLeft), width = Math.ceil(x+m.actualBoundingBoxRight)-left+1;
                                const box = [left,15,width,29];
                                credit = {text,rtl:ctx.direction === 'rtl',box,mask:[...ctx.getImageData(...box).data]
                                    .filter((v,i) => i%4 === 0).map(v => v > 140)};
                            };
                            await replay.start({lesson:'a'.repeat(32),epoch:'c'.repeat(24)});
                            await replay.frame(0,[]);
                            return credit;
                        }''')
                    results = []
                    for paced in (False, True):
                        async with ArchiveRenderer(assets).open(browser) as page:
                            results.append(base64.b64decode(await page.evaluate(OPUS_REPLAY_PCM,
                                {'packets': opus_with_gap(packets), 'paced': paced})))
                finally:
                    await browser.close()
            self.assertTrue(results[0] == results[1], 'Opus decode and placement must not depend on the compositor batch')
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

            def rms(start, end):
                window = pcm[start * 48:end * 48]
                return math.sqrt(sum(x * x for x in window) / len(window))

            # Allow AAC priming/window bleed at the edges, not a moved or lost gap.
            self.assertLess(rms(375, 420), 240, 'an actual RTP gap stays at the correct time')
            self.assertGreater(min(rms(t, t + 20) for t in range(480, 2900, 20)), 2400,
                               'later audio has no false dropouts between compositor batches')
            pixels = await asyncio.to_thread(subprocess.check_output, [FFMPEG, '-v', 'error', '-i', str(part), '-map', '0:v:0',
                '-frames:v', '1', '-pix_fmt', 'rgb24', '-threads:v', '1', '-f', 'rawvideo', '-'], timeout=8)
            self.assertEqual(len(pixels), WIDTH * HEIGHT * 3)
            start = (320 * WIDTH + 500) * 3
            board = pixels[start:start + 280 * 3:3]
            self.assertGreater(len(set(board)), 20)  # The noisy archived document reached the film.
            self.assertEqual(credit['text'], 'طراحی و توسعه : سید مهدی بیات')
            self.assertTrue(credit['rtl'])
            x, y, w, h = credit['box']
            self.assertGreaterEqual(x, 24)
            expected = {i for i, ink in enumerate(credit['mask']) if ink}
            decoded = {row * w + col for row in range(h) for col in range(w)
                       if pixels[((y + row) * WIDTH + x + col) * 3] > 140}
            self.assertGreater(len(expected), 120)
            self.assertGreater(len(expected & decoded) / len(expected | decoded), .83, 'credit glyphs in the MP4')


if __name__ == '__main__':
    unittest.main()
