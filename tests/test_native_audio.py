"""Optional wire check: LiveKit + production Chrome room defaults, DTX and Opus LBRR under uplink loss."""
import asyncio
import base64
from contextlib import asynccontextmanager
import ctypes
import os
from pathlib import Path
import re
import socket
import tempfile
import time
import unittest
from zipfile import ZipFile

from test_native_livekit import ADDRESS, ENV, ROOT, Budget, NativeCapture, events, duration, sources, local_sfu, token


class LocalUplink(asyncio.DatagramProtocol):
    """Pass ICE/DTLS/RTCP through unchanged; discard scoped RTP from test publishers only."""
    def __init__(self, server):
        self.server = server
        self.sockets = {}
        self.sent = self.dropped = self.drop_every = 0

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, packet, client):
        if client not in self.sockets:
            if len(self.sockets) >= 8:
                return
            flow = self.sockets[client] = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            flow.setblocking(False)
            flow.connect(self.server)
            def back():
                try:
                    self.transport.sendto(flow.recv(4096), client)
                except OSError:
                    pass
            asyncio.get_running_loop().add_reader(flow.fileno(),back)
        flow = self.sockets[client]
        if len(packet) >= 12 and 128 <= packet[0] < 192 and not 192 <= packet[1] <= 223:
            self.sent += 1
            if self.drop_every and self.sent % self.drop_every == 0:
                self.dropped += 1
                return
        try:
            flow.send(packet)
        except OSError:
            pass

    def connection_lost(self, _):
        for flow in self.sockets.values():
            asyncio.get_running_loop().remove_reader(flow.fileno())
            flow.close()


@asynccontextmanager
async def local_uplink():
    ip = '127.0.0.1'
    proxy = LocalUplink((ip,7882))
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda: proxy, local_addr=(ip,0))
    try:
        yield proxy, transport.get_extra_info('sockname')[1]
    finally:
        transport.close()


AUDIO = r'''
async ({port,token,makeRoom}) => {
    const pcs = [];
    const PC = window.RTCPeerConnection;
    const replace = s => s.replace(/ 7882 typ host/g, ` ${port} typ host`);
    window.RTCPeerConnection = class extends PC {
        constructor(...args) { super(...args); pcs.push(this); }
        addIceCandidate(candidate) {
            return super.addIceCandidate(candidate?.candidate ? {
                candidate:replace(candidate.candidate),sdpMid:candidate.sdpMid,sdpMLineIndex:candidate.sdpMLineIndex,
                usernameFragment:candidate.usernameFragment,
            } : candidate);
        }
        setRemoteDescription(desc) {
            return super.setRemoteDescription({type:desc.type,sdp:replace(desc.sdp)});
        }
    };
    const lk = window.LivekitClient;
    const room = new Function('lk', makeRoom+'; return makeRoom();')(lk);
    await room.connect('ws://127.0.0.1:7880', token,{autoSubscribe:false,websocketTimeout:4500,peerConnectionTimeout:4500});
    const audio = new AudioContext({sampleRate:48000}); await audio.resume();
    const input = audio.createBuffer(1, 144000, 48000);
    const pcm = input.getChannelData(0);
    let pitch = 0;
    for (let i=0; i<pcm.length; i++) {
        const t=i/48000;
        pitch += 2*Math.PI*(150+30*Math.sin(2*Math.PI*.7*t))/48000;
        const amp = .13+.09*Math.sin(2*Math.PI*3.5*t);
        pcm[i] = amp*(Math.sin(pitch)+.45*Math.sin(pitch*3)+.26*Math.sin(pitch*7)+.26*Math.sin(pitch*11));
    }
    const voice = audio.createBufferSource(); voice.buffer=input; voice.loop=true;
    const gain = audio.createGain(); gain.gain.value=1;
    const sink = audio.createMediaStreamDestination(); sink.channelCount=1;
    voice.connect(gain).connect(sink); voice.start();
    await room.localParticipant.publishTrack(sink.stream.getAudioTracks()[0], {source:lk.Track.Source.Microphone});
    window.probe = {
        sound(on){gain.gain.value = +on;},
        fmtp(){return pcs.filter(pc=>pc.getSenders().some(s=>s.track?.kind==='audio')).map(pc=>pc.remoteDescription?.sdp).join('\n');},
        async close(){await room.disconnect(); await audio.close();},
    };
}
'''


@unittest.skipUnless(all(os.environ.get(x) for x in (*ENV,'MPM_TEST_OPUS')), 'needs isolated SFU and libopus >= 1.5 with LBRR probe')
class NativeAudioTest(unittest.IsolatedAsyncioTestCase):
    async def test_mono_dtx_and_fec_on_livekit(self):
        from playwright.async_api import async_playwright
        opus = ctypes.CDLL(os.environ['MPM_TEST_OPUS'])
        opus.opus_packet_get_nb_channels.argtypes = [ctypes.c_char_p]
        opus.opus_packet_has_lbrr.argtypes = [ctypes.c_char_p, ctypes.c_int32]
        job, room = '5'*32, f'test-{"a"*20}-v4-c5-{"c"*24}'
        def school(action, _job, **_):
            assert action == 'board'
            return {'board':{'rev':1,'cursor':0},'archive':{}}
        source = (ROOT/'school-addon/upload/online-classroom/assets/classroom.js').read_text()
        opts = re.search(r'^    function makeRoom\(\) \{.*?^    \}',source,re.M|re.S)[0]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory/job).mkdir()
            async with local_sfu(directory, udp_only=True), local_uplink() as (uplink, port), async_playwright() as p:
                browser = await p.chromium.launch(executable_path=os.environ['MPM_TEST_CHROMIUM'],chromium_sandbox=True,
                                                  args=['--autoplay-policy=no-user-gesture-required'])
                page = await browser.new_page()
                await page.goto(ADDRESS,timeout=4000)
                with ZipFile(ROOT/'artifacts/baci-classroom-recorder-v0.4.22.zip') as frozen:
                    await page.add_script_tag(content=frozen.read('livekit-client.umd.js').decode())
                await asyncio.wait_for(page.evaluate(AUDIO,{'port':port,'token':token(room,'teacher-7'),'makeRoom':opts}),12)
                fmtp = await page.evaluate('probe.fmtp()')
                self.assertIn('useinbandfec=1',fmtp)
                self.assertIn('usedtx=1',fmtp)
                cap = NativeCapture(os.environ['MPM_TEST_NATIVE_RECEIVER'],school,directory/job,room,Budget(directory))
                self.assertTrue(await cap.start({'token':token(room,'recorder-'+job,True),'lease':int(time.time())+8,'mediaMode':'audio'}))
                async def renew():
                    await cap.renew(int(time.time())+8)
                try:
                    await asyncio.sleep(1.25)
                    start = uplink.sent
                    await asyncio.sleep(3)
                    speech = (uplink.sent-start)/3
                    await page.evaluate('probe.sound(false)')
                    await renew()
                    await asyncio.sleep(1.8)
                    start = uplink.sent
                    await asyncio.sleep(3)
                    silence = (uplink.sent-start)/3
                    await page.evaluate('probe.sound(true)')
                    uplink.drop_every = 10
                    lossy = cap.log.elapsed()
                    for _ in range(4):
                        await renew()
                        await asyncio.sleep(3)
                finally:
                    await cap.stop(True)
                    await page.evaluate('probe.close()')
                    await browser.close()
                self.assertGreater(speech,40, 'the test uplink must carry the publisher RTP')
                self.assertLess(silence,speech/4)
                self.assertGreater(uplink.dropped,45)
                [tape] = sources(directory/job)
                with tape.open('rb') as fp:
                    duration(fp)
                    packets = [((e[4]),base64.b64decode(e[-1])) for _, e in events(fp) if e[0]=='media' and e[2]>lossy+4000]
                self.assertGreater(len(packets),240)
                self.assertTrue(all(opus.opus_packet_get_nb_channels(payload)==1 for _,payload in packets))
                gaps = [now[1] for prev,now in zip(packets,packets[1:]) if (now[0]-prev[0]) & 0xffffffff == 1920]
                self.assertGreater(len(gaps),20, 'observed one-packet RTP gaps at the receiver')
                protected = sum(opus.opus_packet_has_lbrr(packet,len(packet)) == 1 for packet in gaps)
                print(f'Opus wire diagnostic: {speech:.1f} speech vs {silence:.1f} silence pkt/s; '
                      f'{uplink.dropped} RTP packets dropped; {protected}/{len(gaps)} single-packet gaps with in-band LBRR',flush=True)
