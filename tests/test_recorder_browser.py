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
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server/recorder'))
from capture import CSP, Capture, Renderer, clips, encoder, view

FAKE_LK = b'''
window.LivekitClient = {
    Track:{Source:{Camera:'camera',ScreenShare:'screen_share',Microphone:'microphone'}},
    RoomEvent:{TrackSubscribed:'track',TrackUnsubscribed:'untrack',Disconnected:'gone'},
    Room:class {
        constructor(){ this.events=new Map(); this.stop=[]; }
        on(event,fn){ this.events.set(event,fn); return this; }
        async connect(){
            this.state='connected';
            for (const [id,source,color] of [['teacher-1','camera','#ef301c'],['student-1','camera','#19d822'],['student-1','screen_share','#164cfa']]) {
                const c=document.createElement('canvas'); c.width=320; c.height=180;
                const ctx=c.getContext('2d');
                const paint=()=>{ctx.fillStyle=color;ctx.fillRect(0,0,320,180);};
                paint(); const media=c.captureStream(10); paint();
                const timer=setInterval(paint,100); this.stop.push(()=>{clearInterval(timer);media.getTracks()[0].stop();});
                this.events.get('track')({kind:'video',attach:(v)=>v.srcObject=media,detach:(v)=>v.srcObject=null},
                    {trackSid:id+source,source,isMuted:false},{identity:id});
            }
            this.audio = new AudioContext({sampleRate:48000}); await this.audio.resume();
            for (const [id,hz] of [['teacher-1',392],['student-1',658]]) {
                const tone=this.audio.createOscillator(), dest=this.audio.createMediaStreamDestination(), gain=this.audio.createGain();
                gain.gain.value=.08; tone.frequency.value=hz; tone.connect(gain).connect(dest); tone.start();
                this.stop.push(()=>tone.stop());
                this.events.get('track')({kind:'audio',mediaStreamTrack:dest.stream.getAudioTracks()[0]},
                    {trackSid:id,source:'microphone',isMuted:false},{identity:id});
            }
        }
        async disconnect(){ this.state='disconnected'; for(const fn of this.stop)fn(); await this.audio?.close(); }
    }
};
'''


def make_pdf():
    stream = b'1 0.9 0.2 rg 0 0 480 300 re f\n'
    content = [b'<</Type /Catalog /Pages 2 0 R>>', b'<</Type /Pages /Count 1 /Kids [3 0 R]>>',
               b'<</Type /Page /Parent 2 0 R /MediaBox [0 0 480 300] /Resources <<>> /Contents 4 0 R>>',
               b'<</Length ' + str(len(stream)).encode() + b'>>\nstream\n' + stream + b'endstream']
    out = b'%PDF-1.4\n'
    offsets = []
    for i, obj in enumerate(content, 1):
        offsets.append(len(out))
        out += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    return (out + b'xref\n0 5\n0000000000 65535 f \n' + b''.join(f'{x:010d} 00000 n \n'.encode() for x in offsets)
            + f'trailer\n<</Size 5 /Root 1 0 R>>\nstartxref\n{len(out)}\n%%EOF\n'.encode())


@unittest.skipUnless(os.environ.get('MPM_TEST_CHROMIUM') and os.environ.get('MPM_REC_FFMPEG'),
                     'set MPM_TEST_CHROMIUM, MPM_REC_FFMPEG and install Playwright for real compositor tests')
class CompositorTest(unittest.IsolatedAsyncioTestCase):
    async def test_records_pdf_ink_screen_teacher_and_student_video_and_audio(self):
        from playwright.async_api import async_playwright
        ffmpeg = os.environ['MPM_REC_FFMPEG']
        artifact = ROOT / 'artifacts/baci-classroom-recorder-v0.4.16.zip'
        pdf, doc, job = make_pdf(), 'd' * 32, '1' * 32
        room = f'test-{"a" * 20}-v4-c42-{"c" * 24}'
        requests = []

        class School:
            def __call__(self, action, given, **data):
                requests.append((action, given, data['room']))
                if data['board'] == 2:
                    return {'board': {'rev': 2, 'cursor': 1, 'write': False}}
                return {'board': {'rev': 2, 'cursor': 1, 'write': False, 'more': False, 'reset': True, 'page': 1, 'docs': [],
                        'doc': {'id': doc, 'mime': 'application/pdf', 'name': 'fixture.pdf', 'bytes': len(pdf), 'pages': 1},
                        'ops': [{'tool': 'pen', 'id': 'stroke', 'size': 24, 'color': '#ff00e8', 'points': [[2000, 4500], [8000, 4500]]}]}}

            def document(self, given, epoch, key, byte_range):
                requests.append(('document', given, epoch))
                assert epoch == room and key == doc
                return 200, {'content-type': 'application/pdf'}, pdf

        with tempfile.TemporaryDirectory() as tmp, ZipFile(artifact) as zipped:
            work = Path(tmp)
            zipped.extractall(work)
            out = work / 'spool'
            out.mkdir()
            renderer = Renderer(work)
            renderer.static['/livekit-client.umd.js'] = ('text/javascript', FAKE_LK)
            async with async_playwright() as playwright:
                # Keep the real browser sandbox on; only signaling and source tracks are synthetic.
                browser = await playwright.chromium.launch(executable_path=os.environ['MPM_TEST_CHROMIUM'],
                    args=['--autoplay-policy=no-user-gesture-required'], headless=True, chromium_sandbox=True)
                try:
                    await renderer.check(browser)
                    cap = Capture(browser, renderer, School(), room, job, out, ffmpeg)
                    try:
                        await cap.start('local-fixture', time.time()+8)
                        # Wait for the actual PDF worker to paint and all visible tracks to load.
                        colors = '''() => {
                            const c=document.getElementById('film').getContext('2d');
                            const pix=(x,y)=>Array.from(c.getImageData(x,y,1,1).data).slice(0,3);
                            return [[150,130],[200,211],[600,150],[210,495],[700,495]].map(p=>pix(...p));
                        }'''
                        await cap.page.wait_for_function(f'''() => {{
                            const [paper,ink,screen,teacher,student]=({colors})();
                            return paper[0]>200 && paper[1]>180 && ink[0]>200 && ink[2]>180 && ink[1]<100 &&
                                screen[2]>160 && teacher[0]>180 && student[1]>160;
                        }}''', timeout=5000)
                        self.assertTrue(await cap.running())
                        await cap.renew(time.time()+2)
                        # No further school renewal: the browser stops the recording on its own.
                        await cap.page.wait_for_function('() => !recorder.state().running', timeout=3500)
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
            sample = subprocess.check_output([ffmpeg, '-v', 'error', '-i', str(part), '-vn', '-ar', '8000', '-f', 's16le', '-t', '1.2', '-'])
            pcm = array.array('h', sample)[2000:8000]
            for hz in (392, 658):
                energy = abs(sum(x*complex(math.cos(2*math.pi*hz*n/8000), math.sin(2*math.pi*hz*n/8000))
                                 for n, x in enumerate(pcm))) / len(pcm)
                self.assertGreater(energy, 100, 'both microphones must be audible in the actual MP4')

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
                with ZipFile(ROOT / 'artifacts/baci-classroom-recorder-v0.4.16.zip') as zipped:
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
            source = subprocess.check_output([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=10',
                '-f', 'lavfi', '-i', 'sine=frequency=680', '-t', '3.1', '-c:v', 'libvpx', '-pix_fmt', 'yuv420p',
                '-c:a', 'libopus', '-f', 'webm', '-'], timeout=12)
            subprocess.run(encoder(ffmpeg, root / 'clip-000-%03d.mp4', seconds=1), input=source, check=True, timeout=15)
            parts = clips(root)
            self.assertGreaterEqual(len(parts), 3)
            for part in parts:
                raw = part.read_bytes()
                self.assertLess(raw.find(b'moov'), raw.find(b'mdat'))
                subprocess.run([ffmpeg, '-v', 'error', '-i', str(part), '-f', 'null', '-'], check=True, timeout=5)
