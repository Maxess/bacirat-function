"""Real Chromium/MediaRecorder + FFmpeg with synthetic tracks and a private PDF fixture."""
import asyncio
import array
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import time
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server/recorder'))
from capture import CSP, FPS, HEIGHT, WIDTH, Capture, Renderer, clips, encoder, view
from recording import Runner

ARTIFACT = ROOT / 'artifacts/baci-classroom-recorder-v0.4.20.zip'
PREVIOUS = ROOT / 'artifacts/baci-classroom-recorder-v0.4.18.zip'
SCHOOL_SITE = ROOT / 'artifacts/school-classroom-plugin-v0.4.20.zip'
CREDIT = 'طراحی و توسعه : س. مهدی بیات'
# Stub signaling only. Use the pinned SDK attach/detach methods and real inbound RTP audio
# so this test detects the old silent graph (local oscillators alone masked it).
FAKE_ROOM = b'''
(() => {
    const lk = window.LivekitClient, {RoomEvent:E,Track:{Source:S}} = lk;
    window.LivekitClient = {...lk,Room:class {
        constructor() { this.events=new Map(); this.stop=[]; this.videos=new Map(); }
        on(event,fn) { this.events.set(event,fn); return this; }
        async connect() {
            this.state='connected';
            this.video('teacher-1',S.Camera,'#ef301c');
            this.video('student-1',S.Camera,'#19d822');
            window.fixture = {
                audio:() => this.mics(),
                screen:on => on ? this.video('student-1',S.ScreenShare,'#164cfa') : this.unpublish('student-1',S.ScreenShare),
                camera:on => on ? this.video('teacher-1',S.Camera,'#ef301c') : this.unpublish('teacher-1',S.Camera),
            };
        }
        video(identity,source,color) {
            const c=document.createElement('canvas'); c.width=320; c.height=180;
            const ctx=c.getContext('2d'), paint=()=>{ctx.fillStyle=color;ctx.fillRect(0,0,320,180);};
            paint(); const media=c.captureStream(12); paint();
            const timer=setInterval(paint,80), trackSid=identity+source;
            const stop=()=>{clearInterval(timer);media.getVideoTracks()[0].stop();};
            this.stop.push(stop);
            const track=new lk.RemoteVideoTrack(media.getVideoTracks()[0],trackSid,undefined);
            const pub={trackSid,source,isMuted:false};
            this.videos.set(trackSid,{track,pub,stop});
            this.events.get(E.TrackSubscribed)(track,pub,{identity});
        }
        unpublish(identity,source) {
            const key=identity+source, {track,pub,stop}=this.videos.get(key);
            this.events.get(E.TrackUnsubscribed)(track,pub,{identity});
            stop(); this.videos.delete(key);
        }
        async mics() {
            this.audio=new AudioContext({sampleRate:48000,sinkId:{type:'none'}}); await this.audio.resume();
            const sender=new RTCPeerConnection(), receiver=new RTCPeerConnection(), ids=new Map();
            this.stop.push(()=>sender.close(),()=>receiver.close());
            receiver.ontrack=({track,receiver,streams:[stream]}) => {
                const spec=ids.get(stream.id);
                const remote=new lk.RemoteAudioTrack(track,spec.identity,receiver);
                remote.setMediaStream(stream);
                // Keep the third track transmitting so the recorder's mute filter is exercised.
                this.events.get(E.TrackSubscribed)(remote,{trackSid:spec.identity,source:S.Microphone,isMuted:spec.muted},spec);
            };
            for (const [identity,hz,muted] of [['teacher-1',392,false],['student-1',658,false],['student-2',976,true]]) {
                const tone=this.audio.createOscillator(), dest=this.audio.createMediaStreamDestination(), gain=this.audio.createGain();
                gain.gain.value=.10; tone.frequency.value=hz; tone.connect(gain).connect(dest); tone.start();
                this.stop.push(()=>tone.stop()); ids.set(dest.stream.id,{identity,muted});
                sender.addTrack(dest.stream.getAudioTracks()[0],dest.stream);
            }
            async function offer(pc,desc) {
                await pc.setLocalDescription(desc);
                if (pc.iceGatheringState!=='complete') await new Promise(resolve => {
                    pc.addEventListener('icegatheringstatechange',() => { if(pc.iceGatheringState==='complete') resolve(); });
                });
            }
            await offer(sender,await sender.createOffer()); await receiver.setRemoteDescription(sender.localDescription);
            await offer(receiver,await receiver.createAnswer()); await sender.setRemoteDescription(receiver.localDescription);
        }
        async disconnect() { this.state='disconnected'; for(const fn of this.stop)fn(); await this.audio?.close(); }
    }};
})();
'''


def make_pdf(persian=False):
    stream = b'1 0.9 0.2 rg 0 0 480 300 re f 0.05 0.85 0.75 rg 0 0 120 300 re f\n'
    def obj_stream(data):
        return b'<</Length ' + str(len(data)).encode() + b'>>\nstream\n' + data + b'endstream'
    resources, fonts = b'', []
    if persian:
        # Visual-order presentation forms for «پژوهش» with Vazirmatn advances.
        # No embedded font: desktop and the VPS must agree on the fallback before rasterizing.
        stream = b'0 g BT /F 48 Tf 144 124 Td <00010002000300040005> Tj ET\n1 0 0.7 rg 470 288 10 12 re f\n'
        resources = b'/Font <</F 5 0 R>>'
        cmap = b'''/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo <</Registry (Adobe)/Ordering (UCS)/Supplement 0>> def
/CMapName /PersianFixture def
/CMapType 2 def
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
5 beginbfchar
<0001> <FEB6>
<0002> <FEEB>
<0003> <FEED>
<0004> <FB8B>
<0005> <FB58>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end
'''
        fonts = [b'<</Type /Font /Subtype /Type0 /BaseFont /PersianFixtureMissing /Encoding /Identity-H /DescendantFonts [6 0 R] /ToUnicode 8 0 R>>',
                 b'<</Type /Font /Subtype /CIDFontType2 /BaseFont /PersianFixtureMissing /FontDescriptor 7 0 R '
                 b'/CIDSystemInfo <</Registry (Adobe)/Ordering (Identity)/Supplement 0>> /W [1 [1237 695 444 386 312]]>>',
                 b'<</Type /FontDescriptor /FontName /PersianFixtureMissing /Flags 4 /FontBBox [0 -250 1800 1000] '
                 b'/ItalicAngle 0 /Ascent 900 /Descent -200 /CapHeight 800 /StemV 100>>', obj_stream(cmap)]
    content = [b'<</Type /Catalog /Pages 2 0 R>>', b'<</Type /Pages /Count 1 /Kids [3 0 R]>>',
               b'<</Type /Page /Parent 2 0 R /MediaBox [0 0 480 300] /Resources <<' + resources + b'>> /Contents 4 0 R>>',
               obj_stream(stream), *fonts]
    out = b'%PDF-1.4\n'
    offsets = []
    for i, obj in enumerate(content, 1):
        offsets.append(len(out))
        out += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    size = len(content) + 1
    return (out + f'xref\n0 {size}\n0000000000 65535 f \n'.encode() + b''.join(f'{x:010d} 00000 n \n'.encode() for x in offsets)
            + f'trailer\n<</Size {size} /Root 1 0 R>>\nstartxref\n{len(out)}\n%%EOF\n'.encode())


class RecorderPackageTest(unittest.TestCase):
    def test_standalone_payload_matches_sources_and_keeps_protocol_assets(self):
        with ZipFile(ARTIFACT) as new, ZipFile(PREVIOUS) as old:
            fonts = {f'fonts/{name}' for name in ('OFL.txt','README.md','Vazirmatn-Regular.woff2','Vazirmatn-Bold.woff2')}
            self.assertEqual(set(new.namelist()), set(old.namelist()) | fonts)
            changed = {'frame.js','view.js','view.css','capture.py','recording.py','README-fa.md'}
            for name in set(old.namelist()) - changed:
                self.assertEqual(new.read(name), old.read(name))
            for name in changed | fonts:
                self.assertEqual(new.read(name), (ROOT / 'server/recorder' / name).read_bytes())
            self.assertEqual(new.read('board.js'), (ROOT / 'school-addon/upload/online-classroom/assets/board.js').read_bytes())
            if site := os.environ.get('MPM_TEST_BACI'):
                for name in fonts:
                    if name.endswith('.woff2'):
                        self.assertEqual(new.read(name), (Path(site) / 'assets/fonts/screen' / Path(name).name).read_bytes())


@unittest.skipUnless(os.environ.get('MPM_TEST_CHROMIUM') and os.environ.get('MPM_REC_FFMPEG'),
                     'set MPM_TEST_CHROMIUM, MPM_REC_FFMPEG and install Playwright for real compositor tests')
class CompositorTest(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_webcam_band_pdf_screen_and_real_remote_audio_in_mp4(self):
        from playwright.async_api import async_playwright
        ffmpeg = os.environ['MPM_REC_FFMPEG']
        pdf, doc, job = make_pdf(), 'd' * 32, '1' * 32
        room = f'test-{"a" * 20}-v4-c42-{"c" * 24}'
        requests, show_doc, framing = [], False, None

        class School:
            def __call__(self, action, given, **data):
                requests.append((action, given, data['room']))
                rev = 2 if show_doc else 1
                base = {'rev': rev, 'cursor': int(show_doc), 'write': False}
                if framing:
                    base |= {'view': framing, 'viewMoving': True}
                if data['board'] == rev:
                    return {'board': base}
                document = {'id': doc, 'mime': 'application/pdf', 'name': 'fixture.pdf', 'bytes': len(pdf), 'pages': 1}
                stroke = {'tool': 'pen', 'id': 'stroke', 'size': 24, 'color': '#ff00e8', 'points': [[2000, 4500], [8000, 4500]]}
                return {'board': base | {'reset': True, 'page': 1, 'docs': [], 'doc': document if show_doc else None,
                                        'ops': [stroke] if show_doc else []}}

            def document(self, given, epoch, key, byte_range):
                requests.append(('document', given, epoch))
                assert epoch == room and key == doc
                return 200, {'content-type': 'application/pdf'}, pdf

        with tempfile.TemporaryDirectory() as tmp, ZipFile(ARTIFACT) as zipped:
            work = Path(tmp)
            zipped.extractall(work)
            out = work / 'spool'
            out.mkdir()
            renderer = Renderer(work)
            renderer.static['/livekit-client.umd.js'] = ('text/javascript', zipped.read('livekit-client.umd.js') + FAKE_ROOM)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(executable_path=os.environ['MPM_TEST_CHROMIUM'],
                    args=['--autoplay-policy=no-user-gesture-required'], headless=True, chromium_sandbox=True)
                try:
                    await renderer.check(browser)
                    cap = Capture(browser, renderer, School(), room, job, out, ffmpeg)
                    try:
                        self.assertTrue(await cap.start('local-fixture', time.time()+8))
                        p = cap.page
                        pix = '''(x,y) => Array.from(document.getElementById('film').getContext('2d')
                                                          .getImageData(x,y,1,1).data).slice(0,3)'''
                        async def wait(test):
                            await p.wait_for_function(f'() => {{const pix=({pix}); return {test};}}', timeout=4000)

                        cameras = 'pix(695,602)[0]>180 && pix(430,602)[1]>160'
                        await wait(cameras)
                        gallery = "document.getElementById('film').getContext('2d').getImageData(28,538,1215,148).data"
                        before = await p.evaluate(f'() => Array.from(({gallery}).filter((_,i)=>i%137===0))')
                        self.assertLess((await p.evaluate(f'p => ({pix})(...p)', [515,190]))[0], 140)
                        # The first speakers begin *after* the capture starts, through real RTCPeerConnections.
                        await asyncio.wait_for(p.evaluate('fixture.audio()'), 4)
                        await p.wait_for_function("() => {const a=[...document.querySelectorAll('#media-sinks audio')]; return a.length===3 && a.every(e=>e.muted && !e.paused);}", timeout=2800)
                        await cap.renew(time.time()+8)
                        show_doc = True
                        await wait(f'pix(515,190)[0]>200 && pix(515,190)[1]>180 && {cameras}')
                        await p.evaluate('fixture.screen(true)')
                        await wait(f'pix(329,312)[2]>180 && pix(1030,294)[0]>190 && pix(1030,294)[1]<100 && pix(1030,294)[2]>160 && {cameras}')
                        self.assertEqual(before, await p.evaluate(f'() => Array.from(({gallery}).filter((_,i)=>i%137===0))'),
                                         'starting PDF/ink and screen presentation must not move any webcams')
                        await wait('pix(736,202)[0]<80 && pix(736,202)[1]>175') # Full PDF: left teal stripe is visible.
                        await asyncio.sleep(.4)
                        # The next delta has the same document/ink revision: pan/zoom only.
                        framing = {'zoom': 200, 'crop': [5000,0,10000,10000]}
                        await wait(f'pix(736,202)[1]<70 && pix(830,202)[0]>200 && pix(850,292)[2]>160 && pix(850,292)[1]<100 && {cameras}')
                        self.assertEqual(before, await p.evaluate(f'() => Array.from(({gallery}).filter((_,i)=>i%137===0))'))
                        await asyncio.sleep(.55)
                        await p.evaluate('fixture.camera(false)')
                        await wait('pix(695,602)[0]<130 && pix(430,602)[1]>160')
                        await p.evaluate('fixture.camera(true)')
                        await wait(cameras)
                        self.assertEqual(before, await p.evaluate(f'() => Array.from(({gallery}).filter((_,i)=>i%137===0))'))
                        await p.evaluate('fixture.screen(false)')
                        await wait(f'pix(515,190)[0]>200 && pix(515,190)[1]>180 && {cameras}')
                        self.assertTrue(await cap.running())
                        await cap.renew(time.time()+1.5)
                        # Stop on the short independent lease even without a close call from PHP.
                        await p.wait_for_function('() => !recorder.state().running', timeout=3000)
                        self.assertFalse(await cap.running())
                    finally:
                        await cap.stop()
                finally:
                    await browser.close()
            self.assertIn(('document', job, room), requests)
            self.assertIn(('board', job, room), requests)
            [part] = clips(out)
            data = part.read_bytes()
            self.assertLess(data.find(b'moov'), data.find(b'mdat'))
            self.assertIn(b'avc1', data)
            self.assertIn(b'mp4a', data)
            report = subprocess.run([ffmpeg, '-hide_banner', '-i', str(part)], capture_output=True, text=True, timeout=10).stderr
            self.assertIn(f'{WIDTH}x{HEIGHT}', report)
            self.assertIn(f'{FPS} fps', report)
            self.assertIn('48000 Hz, mono', report)
            small_w, small_h = WIDTH//4, HEIGHT//4
            raw = subprocess.check_output([ffmpeg, '-v', 'error', '-i', str(part), '-an', '-vf', f'fps=4,scale={small_w}:{small_h}',
                                            '-pix_fmt', 'rgb24', '-threads', '1', '-f', 'rawvideo', '-'], timeout=10)
            stride = small_w * small_h * 3
            def pixel(frame_at, x, y):
                start = frame_at + ((y//4)*small_w + x//4)*3
                return raw[start:start+3]
            self.assertTrue(any(pixel(i,329,312)[2]>155 and pixel(i,1030,294)[2]>135 and pixel(i,1030,294)[1]<135
                                and pixel(i,695,602)[0]>150 and pixel(i,430,602)[1]>135
                                for i in range(0,len(raw),stride)),
                            'an encoded frame must contain the board, screen and both cameras in their own areas')
            self.assertTrue(any(pixel(i,736,202)[1]<85 and pixel(i,830,202)[0]>180 and pixel(i,850,292)[2]>123
                                and pixel(i,850,292)[1]<125 and pixel(i,329,312)[2]>155 and pixel(i,695,602)[0]>150
                                and pixel(i,430,602)[1]>135 for i in range(0,len(raw),stride)),
                            'decoded MP4 must crop out the teal stripe and retain aligned ink, screen and webcam positions')
            sample = subprocess.check_output([ffmpeg, '-v', 'error', '-i', str(part), '-vn', '-ar', '8000', '-f', 's16le', '-'], timeout=10)
            pcm = array.array('h', sample)
            def power(hz, start):
                return abs(sum(x*complex(math.cos(2*math.pi*hz*n/8000), math.sin(2*math.pi*hz*n/8000))
                               for n,x in enumerate(pcm[start:start+4000]))) / 4000
            windows = range(0,len(pcm)-4000,4000)
            self.assertTrue(any(min(power(392,i),power(658,i))>180 for i in windows),
                            'both real WebRTC microphones must be audible in the same window of the emitted AAC/MP4')
            self.assertLess(max(power(976,i) for i in windows), 35, 'muted microphone must not leak into the MP4')

    async def test_persian_pdf_raster_and_complete_header_are_preserved_in_mp4(self):
        from playwright.async_api import async_playwright
        ffmpeg = os.environ['MPM_REC_FFMPEG']
        pdf, doc, job = make_pdf(persian=True), 'f'*32, '2'*32
        room = f'test-{"a"*20}-v4-c42-{"d"*24}'
        title = {'school': 'مدرسهٔ پژوهش Baci', 'subject': 'فیزیک ۲'}
        state = {'rev': 1, 'cursor': 0, 'reset': True, 'page': 1, 'ops': [], 'write': False,
                 'doc': {'id': doc, 'mime': 'application/pdf', 'name': 'پژوهش.pdf', 'bytes': len(pdf), 'pages': 1}}

        class School:
            def __call__(self, action, given, **data):
                if action == 'poll':
                    now = int(time.time())
                    return {'command': 'record', 'id': job, 'room': room, 'token': 'local-fixture',
                            'lease': now+8, 'expires': now+600, 'title': title}
                assert action == 'board' and given == job and data['room'] == room
                return {'board': state if not data['board'] else {'rev': 1, 'cursor': 0, 'write': False}}

            def document(self, given, epoch, key, byte_range):
                assert given == job and epoch == room and key == doc
                return 200, {'content-type': 'application/pdf'}, pdf

        async def raster(page):
            await page.wait_for_function('''() => {
                const p = document.getElementById('board-paper');
                const [r,g,b] = p.getContext('2d').getImageData(p.width-2,2,1,1).data;
                return r > 220 && g < 25 && b > 150; // last PDF op: magenta registration mark
            }''', timeout=4500)
            return await page.locator('#board-paper').evaluate('canvas => canvas.toDataURL()')

        school, fonts = School(), []

        class RecorderRenderer(Renderer):
            async def route(self, route, capture=None):
                path = urlsplit(route.request.url).path
                if path.startswith('/fonts/'):
                    fonts.append(path)
                await super().route(route, capture)

        with (tempfile.TemporaryDirectory() as tmp, ZipFile(ARTIFACT) as recorder, ZipFile(SCHOOL_SITE) as site):
            work = Path(tmp)
            recorder.extractall(work)
            out = work / 'spool'
            out.mkdir()
            renderer, live = RecorderRenderer(work), Renderer(work)
            renderer.static['/livekit-client.umd.js'] = ('text/javascript', recorder.read('livekit-client.umd.js') + FAKE_ROOM)
            # Independent reference: the live plugin's own board code and CSS, running on the same PDF and browser.
            live.static['/board.js'] = ('text/javascript', site.read('assets/board.js'))
            live.static['/view.css'] = ('text/css', site.read('assets/classroom.css'))
            live.static['/view.js'] = ('text/javascript', b'')
            for weight in ('Regular','Bold'):
                name = f'Vazirmatn-{weight}.woff2'
                live.static[f'/assets/fonts/screen/{name}'] = ('font/woff2', recorder.read(f'fonts/{name}'))
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(executable_path=os.environ['MPM_TEST_CHROMIUM'],
                    args=['--autoplay-policy=no-user-gesture-required'], headless=True, chromium_sandbox=True)
                runner = Runner(school, out, browser, renderer, ffmpeg)
                try:
                    live_board = Capture(browser, live, school, room, job, out, ffmpeg)
                    async with live_board.view as page:
                        await page.evaluate('''({state, scope}) => {
                            // The room template uses .board-scroll (its physical direction is LTR even in an RTL page).
                            document.getElementById('board-scroll').classList.add('board-scroll');
                            ClassroomBoard(document.getElementById('capture-board'), {
                                rasterOnly:true, canEdit:()=>false, notice:()=>{}
                            }).sync(state,scope);
                        }''', {'state': state, 'scope': {'lesson': job, 'epoch': room[-24:]}})
                        expected = await raster(page)
                    await renderer.check(browser)
                    fonts.clear()
                    await runner.tick()
                    p = runner.capture.page
                    self.assertTrue((await raster(p)) == expected, 'archive PDF pixels differ from the live board raster')
                    self.assertEqual(set(fonts), {f'/fonts/Vazirmatn-{weight}.woff2' for weight in ('Regular','Bold')})
                    art = await p.evaluate('''title => {
                        const probe = document.createElement('canvas');
                        const film = document.getElementById('film');
                        probe.width = film.width; probe.height = film.height;
                        const ctx = probe.getContext('2d',{alpha:false}), draw = ArchiveFrame(probe), rows = [];
                        const fill = ctx.fillText.bind(ctx);
                        ctx.fillText = (text,x,y) => {
                            if (y === 29) {
                                const m = ctx.measureText(text);
                                rows.push({text, rtl:ctx.direction === 'rtl', l:Math.floor(x-m.actualBoundingBoxLeft), r:Math.ceil(x+m.actualBoundingBoxRight)});
                            }
                            fill(text,x,y);
                        };
                        function layout(heading) {
                            rows.length = 0;
                            draw({heading,cameras:[]});
                            return [...rows];
                        }
                        const long = layout({school:'مدرسه 12 مدرسهٔ پژوهش '.repeat(7),subject:'Advanced فیزیک ۲ '.repeat(7)});
                        const missing = layout({});
                        const normal = layout(title);
                        const samples = normal.map(({l,r}) => [l,10,r-l+1,39]);
                        const red = (from,rect) => [...from.getImageData(...rect).data.filter((_,i)=>i%4===0)];
                        return {layouts:[normal,long,missing], probes:samples.map(rect=>[rect,red(ctx,rect)])};
                    }''', title)
                    normal, long, missing = art['layouts']
                    self.assertEqual([r['text'] for r in normal], [CREDIT, title['school'], 'کلاس مجازی', title['subject']])
                    self.assertEqual([r['text'] for r in missing], [CREDIT, 'کلاس مجازی'])
                    self.assertEqual([long[0]['text'], long[2]['text']], [CREDIT, 'کلاس مجازی'])
                    self.assertTrue(all(long[i]['text'].endswith('…') for i in (1,3)))
                    for labels in art['layouts']:
                        self.assertTrue(all(r['rtl'] and 24 <= r['l'] < r['r'] <= WIDTH-58 for r in labels))
                        order = [labels[0], *reversed(labels[1:])]
                        self.assertTrue(all(a['r'] < b['l'] for a,b in zip(order,order[1:])), 'RTL titles must not overlap or reorder')
                    # Allow several stable board frames to reach the encoder before finalizing.
                    await asyncio.sleep(1)
                    region = [465,270,405,135]
                    board_pixels = await p.locator('#film').evaluate('''(c, rect) => [...c.getContext('2d')
                        .getImageData(...rect).data.filter((_,i)=>i%4===0)]''', region)
                    await runner.stop()
                finally:
                    await runner.stop(False)
                    await browser.close()
            [mp4] = clips(out / job)
            image = subprocess.check_output([ffmpeg, '-v', 'error', '-sseof', '-0.4', '-i', str(mp4), '-an',
                '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', '-'], timeout=10)
            self.assertEqual(len(image), WIDTH*HEIGHT*3)

            def compare_mask(rect, reference, dark=False):
                x,y,w,h = rect
                found = [image[(row*WIDTH+col)*3] for row in range(y,y+h) for col in range(x,x+w)]
                glyph = (lambda v: v<120) if dark else (lambda v: v>145)
                want, got = ({i for i,v in enumerate(values) if glyph(v)} for values in (reference,found))
                self.assertGreater(len(want), 60, 'expected visible glyphs in this region')
                self.assertGreater(len(want & got) / len(want | got), .83, 'decoded MP4 lost or misplaced text pixels')

            for rect, reference in art['probes']:
                compare_mask(rect, reference)
            compare_mask(region, board_pixels, dark=True)

    async def test_gallery_keeps_all_60_camera_tiles_inside_its_band(self):
        from playwright.async_api import async_playwright
        with tempfile.TemporaryDirectory() as tmp, ZipFile(ARTIFACT) as zipped:
            zipped.extractall(tmp)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(executable_path=os.environ['MPM_TEST_CHROMIUM'], chromium_sandbox=True)
                try:
                    async with view(browser, Renderer(tmp)) as page:
                        seen = await page.evaluate('''() => {
                            const film=document.getElementById('film');
                            const cameras=Array.from({length:60},(_,i)=>{
                                const video=Object.assign(document.createElement('canvas'),{width:64,height:48,videoWidth:64,videoHeight:48});
                                const c=video.getContext('2d'); c.fillStyle=`rgb(251,${110+i},55)`; c.fillRect(0,0,64,48);
                                return {role:i===0?'teacher':'student',video};
                            });
                            ArchiveFrame(film)({cameras});
                            const ctx=film.getContext('2d'), ids=new Set(); let outside=0;
                            const {data}=ctx.getImageData(0,0,film.width,film.height);
                            for(let i=0;i<data.length;i+=4) if(data[i]===251 && data[i+2]===55 && data[i+1]>=110 && data[i+1]<170) {
                                ids.add(data[i+1]);
                                if(Math.floor(i/(4*film.width))<532) outside++;
                            }
                            return [ids.size,outside];
                        }''')
                finally:
                    await browser.close()
            self.assertEqual(seen, [60,0])

    async def test_real_browser_can_open_loopback_signaling_without_disabling_sandbox(self):
        from playwright.async_api import async_playwright
        requests = []

        class SignalingFixture(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get('Origin')))
                key = self.headers['Sec-WebSocket-Key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
                self.send_response_only(101)
                self.send_header('Upgrade', 'websocket')
                self.send_header('Connection', 'Upgrade')
                self.send_header('Sec-WebSocket-Accept', base64.b64encode(hashlib.sha1(key.encode()).digest()).decode())
                self.end_headers()
                self.wfile.write(b'\x81\x07fixture')

        with HTTPServer(('127.0.0.1', 0), SignalingFixture) as server, tempfile.TemporaryDirectory() as tmp:
            signal = '127.0.0.1:' + str(server.server_port)
            thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
            thread.start()
            try:
                with ZipFile(ARTIFACT) as zipped:
                    zipped.extractall(tmp)
                with patch('capture.CSP', CSP.replace('127.0.0.1:7880', signal)):
                    async with async_playwright() as playwright:
                        browser = await playwright.chromium.launch(executable_path=os.environ['MPM_TEST_CHROMIUM'], chromium_sandbox=True)
                        try:
                            async with view(browser, Renderer(tmp)) as page:
                                message = await asyncio.wait_for(page.evaluate('''url => new Promise((resolve, reject) => {
                                    const ws = new WebSocket(url);
                                    ws.onerror = () => reject(new Error('Loopback signaling blocked'));
                                    ws.onmessage = ev => { resolve(ev.data); ws.close(); };
                                })''', 'ws://' + signal + '/rtc'), timeout=5)
                                self.assertEqual(message, 'fixture')
                        finally:
                            await browser.close()
            finally:
                server.shutdown()
                thread.join(timeout=2)
        self.assertEqual(requests, [('/rtc', 'http://recorder.localhost')])

    async def test_encoder_splits_playable_fast_start_parts(self):
        ffmpeg = os.environ['MPM_REC_FFMPEG']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = subprocess.check_output([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=1280x720:rate=12,noise=alls=15:allf=t+u',
                '-f', 'lavfi', '-i', 'sine=sample_rate=48000:frequency=553', '-t', '16.5', '-c:v', 'libvpx',
                '-deadline', 'realtime', '-cpu-used', '8', '-threads', '2', '-b:v', '1250k',
                '-c:a', 'libopus', '-b:a', '64k', '-f', 'webm', '-'], timeout=16)
            subprocess.run(encoder(ffmpeg, root / 'clip-000-%03d.mp4', seconds=8), input=source, check=True, timeout=15)
            parts = clips(root)
            self.assertEqual(len(parts), 3, 'never skip the first keyframe boundary and create a double-length part')
            for part in parts:
                raw = part.read_bytes()
                self.assertLess(raw.find(b'moov'), raw.find(b'mdat'))
                subprocess.run([ffmpeg, '-v', 'error', '-i', str(part), '-f', 'null', '-'], check=True, timeout=5)
                info = subprocess.run([ffmpeg, '-hide_banner', '-i', str(part)], capture_output=True, text=True, timeout=5).stderr
                duration = float(info.split('Duration: 00:00:', 1)[1].split(',', 1)[0])
                self.assertLessEqual(duration, 8.20)
