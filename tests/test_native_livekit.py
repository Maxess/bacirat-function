"""Optional isolated SFU -> native capture -> deferred MP4 acceptance test.

Requires Linux, the pinned LiveKit server and a built receiver. Never uses the
site or its credentials. It binds the recorder's standard loopback test port.
"""
import array
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from zipfile import ZipFile

from test_deferred_recording import A, B, C, ROOT, two_page_pdf
from archive import Budget, events, duration, sources
from capture import clips
from postprocess import WorkRevoked, request_render
from native_capture import NativeCapture

ENV = ('MPM_TEST_LIVEKIT', 'MPM_TEST_NATIVE_RECEIVER', 'MPM_TEST_SFU_IP', 'MPM_TEST_CHROMIUM', 'MPM_REC_FFMPEG')
ADDRESS = 'http://127.0.0.1:7880'
KEY = 'baci_native_fixture'
SECRET = 'isolated-fixture-do-not-use-outside-test'

PUBLISHER = r'''
async ({token,palette,codec,mic}) => {
    const lk = window.LivekitClient;
    const room = new lk.Room({adaptiveStream:false, dynacast:false});
    await room.connect('ws://127.0.0.1:7880', token,{autoSubscribe:false, websocketTimeout:4500, peerConnectionTimeout:4500});
    const audio = new AudioContext({sampleRate:48000}); await audio.resume();
    const osc = audio.createOscillator(); osc.frequency.value=910;
    const gain = audio.createGain(); gain.gain.value=0;
    const sink = audio.createMediaStreamDestination(); osc.connect(gain).connect(sink); osc.start();
    if (mic) await room.localParticipant.publishTrack(sink.stream.getAudioTracks()[0], {source:'microphone',red:false,dtx:false});
    function scene(color) {
        const canvas=document.createElement('canvas'); canvas.width=320; canvas.height=180;
        const ctx=canvas.getContext('2d');
        const track=canvas.captureStream(0).getVideoTracks()[0];
        let actions=[];
        const draw=()=>{
            const now=performance.now();
            while(actions.length && actions[0][0]<=now) actions.shift()[1]();
            ctx.fillStyle=color;ctx.fillRect(0,0,320,180);track.requestFrame();
        };
        draw();
        const timer=setInterval(draw,75);
        return {track,cue(queue){actions=queue;},paint(value){color=value;},close(){clearInterval(timer);track.stop();}};
    }
    const cam=scene(palette);
    const capture={videoCodec:codec,simulcast:false,backupCodec:false,videoEncoding:{maxBitrate:230000,maxFramerate:15}};
    const camera = await room.localParticipant.publishTrack(cam.track, {source:'camera',...capture});
    let screen, published;
    window.pilot = {
        who:room.localParticipant.sid,
        cues() {
            const start=performance.now();
            cam.cue([[0,true],[530,false],[1350,true],[1880,false],[2700,true],[3230,false]].map(([at,on])=>
                [start+at,()=>{cam.paint(on?'#1cde3b':palette);gain.gain.setValueAtTime(on?.42:0,audio.currentTime);} ]));
        },
        async share(color) {
            if (published) {
                await room.localParticipant.unpublishTrack(published.track);
                screen.close();
            }
            screen=scene(color);
            published=await room.localParticipant.publishTrack(screen.track,{...capture,source:'screen_share'});
            return published.trackSid;
        },
        async muteShare() { await published.mute(); },
        async muteCamera() { await camera.mute(); },
        async close() { cam.close(); screen?.close(); await room.disconnect(); await audio.close(); },
    };
    return window.pilot.who;
}
'''


def token(room, who, recorder=False, *, listener=False):
    def enc(value):
        return base64.urlsafe_b64encode(json.dumps(value,separators=(',', ':')).encode()).rstrip(b'=')
    body = b'.'.join([enc({'alg':'HS256','typ':'JWT'}), enc({'iss':KEY,'sub':who,'nbf':int(time.time())-5,'exp':int(time.time())+180,
        'video':{'roomJoin':True,'room':room,'hidden':recorder,'recorder':recorder,'canSubscribe':recorder or listener,'canPublish':not (recorder or listener),
                 'canPublishData':False,'canUpdateOwnMetadata':False}})])
    return (body + b'.' + base64.urlsafe_b64encode(hmac.digest(SECRET.encode(),body,hashlib.sha256)).rstrip(b'=')).decode()


@asynccontextmanager
async def local_sfu(directory, *, udp_only=False, loopback=False):
    address = ipaddress.IPv4Address('127.0.0.1' if loopback or udp_only else os.environ['MPM_TEST_SFU_IP'])
    path = directory/'livekit.yaml'
    # Isolated signaling, no TURN, cloud connectivity, webhook, or real secrets.
    path.write_text(f'''port: 7880
bind_addresses: [127.0.0.1]
rtc:
  node_ip: {address}
  use_external_ip: false
  udp_port: 7882
  tcp_port: {0 if udp_only else 7881}
  use_ice_lite: {str(udp_only).lower()}
  enable_loopback_candidate: {str(loopback or udp_only).lower()}
room:
  auto_create: true
  enabled_codecs:
    - mime: audio/opus
    - mime: video/VP8
    - mime: video/H264
keys:
  {KEY}: "{SECRET}"
logging:
  level: warn
''')
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)  # The previous fixture may leave TIME_WAIT.
        sock.bind(('127.0.0.1',7880))  # Do not use or stop any existing SFU.
    with (directory/'sfu.log').open('wb') as out:
        proc = await asyncio.create_subprocess_exec(os.environ['MPM_TEST_LIVEKIT'],'--config',str(path),
                    stdout=out, stderr=out)
        try:
            for _ in range(70):
                if proc.returncode is not None:
                    raise RuntimeError((directory/'sfu.log').read_text())
                try:
                    _,writer = await asyncio.open_connection('127.0.0.1',7880)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(.05)
            else:
                raise RuntimeError('fixture SFU did not start')
            yield
        finally:
            if proc.returncode is None:
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 4)
            except TimeoutError:
                proc.kill()
                await proc.wait()



@asynccontextmanager
async def local_postprocess(directory, spool, assets):
    run = directory/'renderer'; run.mkdir(mode=0o700)
    endpoint = run/'render.sock'
    # A separate real Python process, like the systemd unit. Only the fixture
    # asset paths/executables differ; there is no site or school token here.
    script = """
import asyncio, os, sys
from pathlib import Path
from types import SimpleNamespace
from postprocess import serve
spool, endpoint, assets = map(Path,sys.argv[1:])
launch = {'executable_path':os.environ['MPM_TEST_CHROMIUM'],'chromium_sandbox':True,
          'args':['--autoplay-policy=no-user-gesture-required']}
asyncio.run(serve(SimpleNamespace(spool=spool, socket=endpoint, ffmpeg=os.environ['MPM_REC_FFMPEG']),assets=assets,launch=launch))
"""
    env = os.environ.copy()
    env['PYTHONPATH'] = str(assets) + ':' + env.get('PYTHONPATH','')
    proc = await asyncio.create_subprocess_exec(sys.executable,'-c',script,str(spool),str(endpoint),str(assets),env=env)
    try:
        for _ in range(80):
            if endpoint.is_socket():
                break
            if proc.returncode is not None:
                raise RuntimeError('postprocess fixture did not start')
            await asyncio.sleep(.05)
        else:
            raise RuntimeError('postprocess socket unavailable')
        yield endpoint
    finally:
        if proc.returncode is None:
            proc.terminate()
        try:
            if await asyncio.wait_for(proc.wait(), 4):
                raise RuntimeError('postprocess fixture did not shut down cleanly')
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise

def decode(path, args):
    return subprocess.check_output([os.environ['MPM_REC_FFMPEG'],'-hide_banner','-v','error','-i',str(path),*args],timeout=25)


@unittest.skipUnless(all(os.environ.get(x) for x in ENV), 'needs isolated LiveKit, native receiver, Chromium and FFmpeg')
class NativeLiveKitTest(unittest.IsolatedAsyncioTestCase):
    async def test_three_read_only_receivers_and_same_deferred_movie(self):
        from playwright.async_api import async_playwright
        rooms = {job:f'test-{"a"*20}-v4-c{i}-{"b"*24}' for i,job in enumerate((A,B,C),1)}
        pdf, doc = two_page_pdf(), '3'*32
        copies, layouts = [], {job:{'board':False,'screen':None,'screenOwner':None} for job in rooms}
        phase, laser, history = 0, 0, 0
        modes = {A:'standard', B:'audio', C:'teacher-video'}

        class School:
            def __call__(self, action, job, **fields):
                assert action == 'board' and fields['room'] == rooms[job]
                rev = 2 if phase == 2 else 1
                cursor = 1+history if phase == 1 else 0
                assert fields['wire'] == 1
                state = {'rev':rev,'cursor':cursor,'head':cursor,'write':False}
                if fields['board'] != rev:
                    state |= {'doc':{'id':doc,'mime':'application/pdf','bytes':len(pdf),'name':'Persian.pdf','pages':2},
                              'page':rev,'reset':True,'ops':[],'docs':[]}
                if phase == 1:
                    state['view'] = {'zoom':150, 'crop':[4000,1000,9500,9000]}
                    timeline = [
                        {'tool':'pen','id':'native','size':40,'color':'#f200e8','points':[[5100,3000],[8500,3000]]},
                        {'tool':'undo','targets':['native']}, {'tool':'redo','targets':['native']},
                        {'tool':'erase','size':80,'color':'#000000','points':[[5100,3000],[8500,3000]]},
                    ]
                    if fields['ink'] < cursor:
                        state['ops'] = [op | {'from':i,'seq':i+1} for i,op in enumerate(timeline[:cursor]) if i >= fields['ink']]
                    remain = round((laser-time.monotonic())*1000)
                    if remain > 0:
                        state['laser'] = {'points':[[6000,5700,remain],[8000,5200,remain]]}
                return {'board':state,'archive':dict(layouts[job])}

            def document(self, job, room, key, byte_range):
                assert room == rooms[job] and key == doc and byte_range is None
                copies.append(job)
                return 200,{'content-type':'application/pdf'},pdf

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            assets = directory/'assets'; assets.mkdir()
            with ZipFile(ROOT/'artifacts/baci-classroom-recorder-v0.4.23.zip') as zipped:
                zipped.extractall(assets)
            (assets/'baci-native-receiver').chmod(0o755)
            spool = directory/'data'; spool.mkdir(mode=0o700)
            for job in rooms:
                (spool/job).mkdir()
            budget = Budget(spool)
            async with local_sfu(directory), async_playwright() as playwright:
                launch = {'executable_path':os.environ['MPM_TEST_CHROMIUM'],'chromium_sandbox':True,
                          'args':['--autoplay-policy=no-user-gesture-required']}
                senders = await playwright.chromium.launch(**launch)
                contexts = [await senders.new_context() for _ in rooms]
                pages = dict(zip(rooms,[await context.new_page() for context in contexts]))

                async def publish(job, color, codec='vp8', *, page=None, who='teacher-7', mic=True):
                    page = pages[job] if page is None else page
                    await page.goto(ADDRESS,timeout=4000)
                    await page.add_script_tag(path=assets/'livekit-client.umd.js')
                    return await page.evaluate(PUBLISHER,{'token':token(rooms[job],who),'palette':color,'codec':codec,'mic':mic})

                h264 = os.environ.get('MPM_TEST_NATIVE_H264') == '1'
                teacher, _, _ = await asyncio.wait_for(asyncio.gather(publish(A,'#ea362d'),publish(B,'#7644df'),
                                                      publish(C,'#c540a8','h264' if h264 else 'vp8')), 12)
                pupils = [await senders.new_page(), await senders.new_page()]
                await asyncio.gather(publish(A,'#20d2b9',page=pupils[0],who='student-1',mic=False),
                                     publish(C,'#03bbc9',page=pupils[1],who='student-1',mic=False))
                tasks = [NativeCapture(assets/'baci-native-receiver',School(),spool/job,room,budget) for job,room in rooms.items()]
                async def renew():
                    await asyncio.gather(*(cap.renew(int(time.time())+8) for cap in tasks))
                try:
                    self.assertTrue(all(await asyncio.gather(*(cap.start({'token':token(cap.room,'recorder-'+cap.job,True),
                        'lease':int(time.time())+8,'teacher':'teacher-7','mediaMode':modes[cap.job],'title':{'school':'مدرسهٔ پژوهش','subject':'ریاضی'},
                        'archive':dict(layouts[cap.job])}) for cap in tasks))))
                    self.assertTrue(all(cap.page is None for cap in tasks))
                    await asyncio.sleep(3.8)  # SRs and stable red (camera remains the primary picture).
                    await renew()
                    await pupils[0].evaluate('pilot.muteCamera()')
                    await pages[A].evaluate('pilot.cues()')
                    await asyncio.sleep(3.65)
                    await renew()
                    first = await pages[A].evaluate('pilot.share("#2148ef")')
                    layouts[A]['screenOwner'] = {'identity':'teacher-7','sid':teacher}
                    await asyncio.sleep(1.1)
                    layouts[A].update(board=True,screen=first)
                    phase, laser = 1,time.monotonic()+1.7
                    await asyncio.sleep(2.4)
                    await renew()
                    history = 1
                    await asyncio.sleep(1.1)
                    history = 2
                    await asyncio.sleep(1.1)
                    history = 3
                    await asyncio.sleep(1.0)
                    second = await pages[A].evaluate('pilot.share("#f48613")')
                    self.assertNotEqual(first,second)
                    await asyncio.sleep(1.25)  # old screen preference must not promote a new SID
                    layouts[A]['screen'] = second
                    await asyncio.sleep(1.1)
                    await pages[A].evaluate('pilot.muteShare()')
                    phase = 2
                    # Deliberately stop renewing one lease. The helper must leave
                    # independently of the Python/site timers or a renderer.
                    await renew()  # The extra board-history steps must not age out the other two leases.
                    await tasks[1].renew(int(time.time())+2)
                    await asyncio.sleep(2.2)
                    self.assertFalse((await tasks[1].status())['running'])
                    # Actual publisher restart: new PA/TR and a fresh sender clock,
                    # while the recorder stays connected and the bell is still open.
                    await renew()
                    layouts[A].update(board=False,screen=None,screenOwner=None)
                    await pages[A].evaluate('pilot.close()')
                    teacher_again = await publish(A,'#ea362d')
                    self.assertNotEqual(teacher,teacher_again)
                    await asyncio.sleep(2.1)
                    await pages[A].evaluate('pilot.cues()')
                    await asyncio.sleep(3.7)
                    self.assertFalse(list(spool.rglob('*.mp4')))
                finally:
                    await asyncio.gather(*(cap.stop(cap.connected) for cap in tasks))
                    await senders.close()
                self.assertCountEqual(copies,rooms.keys())
                for job in rooms:
                    [source] = sources(spool/job)
                    with source.open('rb') as f:
                        self.assertGreater(duration(f),10_000)
                        tape = [e for _,e in events(f)]
                    codecs = {e[3] for e in tape if e[0]=='media'}
                    expected = {'audio/opus'} if job == B else {'audio/opus','video/h264' if h264 and job == C else 'video/vp8'}
                    self.assertEqual(codecs,expected)
                    self.assertGreaterEqual(len({e[1] for e in tape if e[0]=='sr'}), 1 if job == B else 2)
                    cams = [e for e in tape if e[0]=='open' and e[3]=='camera']
                    if job == A:
                        self.assertEqual({e[5] for e in cams if e[4]=='teacher-7'},{teacher,teacher_again})
                        self.assertTrue(any(e[4]=='student-1' for e in cams))
                        self.assertGreaterEqual(len(source.with_suffix('.idx').read_text().splitlines()),2)
                    if job == C:
                        self.assertTrue(cams and {e[4] for e in cams} == {'teacher-7'})
                async with local_postprocess(directory,spool,assets) as socket_path:
                    window = True
                    def lease(): return time.monotonic()+5 if window else 0
                    pending = asyncio.create_task(request_render(socket_path,A,lease))
                    try:
                        for _ in range(150):
                            if (spool/A/'.render').exists() or pending.done():
                                break
                            await asyncio.sleep(.04)
                        self.assertTrue((spool/A/'.render').is_dir())
                    finally:
                        window = False
                        with self.assertRaises(WorkRevoked):
                            await asyncio.wait_for(pending,4)
                    self.assertFalse((spool/A/'.render').exists())
                    self.assertTrue(sources(spool/A))
                    window = True
                    await asyncio.wait_for(request_render(socket_path,A,lease),80)
                    [clip] = clips(spool/A)
                    if h264:
                        await asyncio.wait_for(request_render(socket_path,C,lease),80)
                        [avc_clip] = clips(spool/C)
                        self.assertIn(b'avc1',avc_clip.read_bytes())
                self.assertCountEqual(copies,rooms.keys(), 'offline process did not re-fetch board PDFs')
            width,height = 320,180
            pixels = decode(clip,['-an','-vf',f'scale={width}:{height}','-threads','1','-pix_fmt','rgb24','-f','rawvideo','-'])
            def get(n,x,y):
                p = (n*width*height + y//4*width + x//4)*3
                return pixels[p:p+3]
            frames = len(pixels)//(width*height*3)
            red = lambda p: p[0]>160 and p[1]<106
            blue = lambda p: p[2]>160 and p[0]<95 and p[1]<125
            orange = lambda p: p[0]>160 and 106<p[1]<169 and p[2]<100
            pink = lambda p:p[2]>175 and p[0]>155 and p[1]<90
            gold = lambda p:p[0]>200 and p[1]>155 and p[2]<110
            green = lambda p:p[1]>150 and p[0]<100 and p[2]<110
            cues = [1000*n/12 for n in range(1,frames) if green(get(n,640,280)) and not green(get(n-1,640,280))]
            raw = array.array('h',decode(clip,['-vn','-ar','8000','-ac','1','-f','s16le','-']))
            rms = [math.sqrt(sum(float(x)*x for x in raw[i:i+80])/80) for i in range(0,len(raw)-79,80)]
            beeps = []
            for i in range(len(rms)-3):
                if min(rms[i:i+3])>1000 and (not beeps or i*10-beeps[-1]>700):
                    beeps.append(i*10)
            self.assertEqual(len(cues),6,(cues,beeps))
            self.assertEqual(len(beeps),6,(cues,beeps))
            bias = [round(v-a) for v,a in zip(cues,beeps)]
            print(f'LiveKit native MP4 camera minus mic, ms: {bias}',flush=True)
            self.assertLessEqual(max(map(abs,bias)),150)
            self.assertTrue(red(get(12,640,280)), 'camera initially primary')
            teal = lambda p: p[0]<95 and p[1]>135 and p[2]>130
            self.assertTrue(teal(get(18,640,593)), 'active pupil is in the separate gallery')
            self.assertFalse(teal(get(59,640,593)), 'muted pupil leaves the gallery')
            active = [n for n in range(frames) if blue(get(n,325,280)) and pink(get(n,960,215))]
            self.assertTrue(active, 'selected share and cropped PDF ink replay together')
            self.assertTrue(any(b-a >= 5 and all(blue(get(n,325,280)) for n in range(a,b+1))
                                for a,b in zip(active,active[1:])), 'undo hides ink and redo restores it in the MP4 on the same PDF page')
            def lit(n):
                return sum(r>g+65 and r>b+65 for r,g,b in (get(n,x,y) for x in range(824,1152,4) for y in range(270,405,4)))
            self.assertTrue(any(lit(n)>2 for n in active), 'board laser retained')
            self.assertEqual(lit(active[-1]),0, 'board laser fades without erasing ink')
            self.assertTrue(any(blue(get(n,325,280)) and not pink(get(n,960,215)) for n in range(active[-1]+1,frames)),
                            'the eraser removes ink without replacing the promoted screen')
            promoted = [n for n in range(active[-1]+1,frames) if orange(get(n,325,280))]
            self.assertTrue(promoted, 'new screen can be selected')
            self.assertTrue(any(red(get(n,325,280)) for n in range(active[-1]+1,promoted[0])), 'new SID does not inherit selection')
            self.assertTrue(any(red(get(n,325,280)) and gold(get(n,960,270)) for n in range(promoted[-1]+1,frames)),
                            'camera and later PDF page return after screen mute')
