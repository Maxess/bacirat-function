"""Deferred archive policy, local bounds, and a browser-to-MP4 integration fixture."""
import array
import asyncio
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'server/recorder'
sys.path.insert(0, str(SRC))
from archive import ArchiveRenderer, Budget, SourceCapture, SpaceError, Timeline, duration, events, sources
from movie import render
from recording import DeferredRunner, PAGED_PLAN, PLAN_PAGE, SpoolJob, WorkRevoked, atomic
from test_recorder_browser import make_pdf
from transport import ProtocolError

A, B, C, D = (str(i)*32 for i in range(1,5))


class SchedulingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)

    def fixture(self, job):
        path = self.root/job
        path.mkdir()
        atomic(path/'meta.json', {'created':int(time.time()),'expires':int(time.time())+3600})
        return path

    async def test_three_captures_renew_while_the_previous_film_is_paused(self):
        captures, occupied, count = [], set(), 0
        active, pending, work = [], [D], D
        release = asyncio.Event()
        advanced = asyncio.Event()
        resumed = asyncio.Event()
        leases = set()

        def school(action, job, **args):
            if action == 'plan':
                return {'capture':list(active),'pending':list(pending),'settle':[], 'work':work, 'lease':int(time.time())+6}
            if action == 'poll':
                if job == D:
                    return {'command':'render','sent':0}
                n = int(job[0])
                return {'command':'record', 'id':job, 'room':f'test-{"a"*20}-v4-c{n}-{"d"*24}',
                        'lease':int(time.time())+8, 'expires':int(time.time())+3600, 'token':'fixture'}
            if action == 'rendered':
                return {'command':'deliver'}
            if action == 'claim':
                return {'command':'hold'}
            self.fail(action)

        class Ingest:
            def __init__(self, browser, renderer, school, directory, room, budget):
                self.room, self.job = room, directory.name
            async def start(self, reply):
                captures.append(self.job)
                occupied.add(self.job)
                return True
            async def status(self):
                return {'running':True}
            async def renew(self, lease):
                leases.add(self.job)
            async def stop(self, seal):
                occupied.remove(self.job)

        async def movie(browser, renderer, ffmpeg, path, wait, budget):
            nonlocal count
            count += 1
            await wait()
            advanced.set()
            await release.wait()
            await wait()
            (path/'clip-000-000.mp4').write_bytes(b'\0\0\0\x18ftypTEST')
            resumed.set()

        self.fixture(D)
        r = DeferredRunner(school,self.root,None,None,None)
        with patch('recording.SourceCapture', Ingest), patch('recording.movie.render', side_effect=movie):
            await r.tick()
            await asyncio.wait_for(advanced.wait(), 1)
            active[:] = [A,B,C]; pending[:] = [A,B,C,D]; work = None
            await r.tick()
            release.set()
            await asyncio.sleep(1.5)
            self.assertEqual(set(captures),{A,B,C})
            self.assertEqual(leases,{A,B,C})
            self.assertFalse(resumed.is_set(), 'no new frame after live priority revokes the render lease')
            r.until = time.monotonic()-1
            r.permit = D  # stale but otherwise favorable plan
            await asyncio.sleep(.25)
            self.assertFalse(resumed.is_set(), 'no heavy work using an expired authorization')
            active[:] = []; work = D
            await r.tick()
            await asyncio.wait_for(resumed.wait(), 1)
            await r.finish_task
            self.assertEqual(count,1,'resume the same encode after classes, not another parallel job')
            r.stopping = True
            await asyncio.gather(*r.captures.values())
            self.assertFalse(occupied)

    async def test_large_backlog_discovery_and_offpeak_permit_are_independent(self):
        jobs = [f'{i:032x}' for i in range(1, 1041)]
        active = set(jobs[-9:])
        work = jobs[-10]
        visited = []
        r = DeferredRunner(None, self.root, None, None, None)
        admitted, finished, settled = set(), [], set()
        all_started, waiting = asyncio.Event(), asyncio.Event()

        async def rpc(action, **data):
            cursor = data.get('after')
            block = jobs[(jobs.index(cursor)+1 if cursor else 0):][:PLAN_PAGE]
            out = {'plan_format':PAGED_PLAN, 'pending':block, 'capture':sorted(set(block) & active),
                   'settle':list(set(block) & {jobs[-11]}), 'next':block[-1] if len(block) == PLAN_PAGE else None}
            if action == 'plan':
                self.assertEqual(data['plan_format'], PAGED_PLAN)
                return out | {'work':None if active else work, 'current_ok':not active, 'lease':int(time.time())+6}
            self.assertEqual(action, 'plan-page')
            visited.append(cursor)
            return out

        async def capture(job):
            admitted.add(job)
            if admitted == active:
                all_started.set()
            await waiting.wait()

        async def finish(job):
            await asyncio.wait_for(r.eligible(job, time.time()+60), .5)
            finished.append(job)

        async def maintain(todo):
            settled.update(todo)

        r.control.rpc = rpc
        r.capture, r.finish, r.maintain = capture, finish, maintain
        await r.tick()
        await asyncio.wait_for(all_started.wait(), 1)
        self.assertEqual(admitted, active)
        self.assertEqual(visited, jobs[PLAN_PAGE-1::PLAN_PAGE])
        self.assertFalse(finished, 'live work preempts finishing')
        waiting.set()
        await asyncio.gather(*r.captures.values())
        active.clear()
        await r.tick()
        await asyncio.gather(r.finish_task, r.repair, r.discovery, r.scan_task)
        self.assertEqual(finished, [work], 'selected film can be outside the first page')
        self.assertIn(jobs[-11], settled, 'expired work is found on later pages too')

    async def test_paged_capture_leases_survive_failed_page_and_another_rooms_disk_fault(self):
        wanted = [f'{i:032x}' for i in range(1, 10)]
        broken = wanted[4]
        renewed, occupied, faults = set(), set(), []
        paged = True
        later_failed = False

        def school(action, job, **args):
            if action == 'plan':
                head = {'capture':wanted[:4], 'pending':wanted[:4], 'settle':[], 'work':None, 'lease':0}
                return head | ({'plan_format':PAGED_PLAN,'next':wanted[3],'current_ok':False} if paged else {})
            if action == 'plan-page':
                if later_failed:
                    raise ProtocolError(503)
                return {'plan_format':PAGED_PLAN, 'next':None, 'pending':wanted[4:], 'capture':wanted[4:], 'settle':[]}
            if action == 'fault':
                faults.append((job,args['reason']))
                return {}
            if action == 'poll':
                if any(job == key for key, _ in faults):
                    return {'command':'purge'}
                return {'command':'record','id':job,'token':'fixture','lease':int(time.time())+8,'expires':int(time.time())+600,
                        'room':f'test-{"a"*20}-v4-c{int(job,16)}-{"d"*24}'}
            self.fail(action)

        class Ingest:
            def __init__(self, browser, renderer, school, directory, room, budget):
                self.job, self.room = directory.name, room
            async def start(self, reply):
                if self.job == broken:
                    raise SpaceError('fixture-disk-pressure')
                occupied.add(self.job)
                return True
            async def status(self):
                return {'running':True}
            async def renew(self, lease):
                renewed.add(self.job)
            async def stop(self, seal):
                occupied.discard(self.job)

        runner = DeferredRunner(school, self.root, None, None, None)
        with patch('recording.SourceCapture', Ingest):
            try:
                await runner.tick()
                await runner.discovery
                self.assertEqual(set(runner.captures), set(wanted))
                later_failed = True
                await runner.tick()
                await runner.discovery
                self.assertEqual(runner.missing_hints, set(), 'an incomplete scan is not authoritative')
                paged = False
                with self.assertRaises(ProtocolError):
                    await runner.tick()
                await asyncio.sleep(1.45)
                survivors = set(wanted) - {broken}
                self.assertEqual(faults, [(broken,'space')])
                self.assertEqual(occupied, survivors)
                self.assertEqual(renewed, survivors, 'per-job renewals do not depend on the global plan or paging')
            finally:
                runner.stopping = True
                await asyncio.gather(*runner.captures.values(), *(t for t in (runner.discovery, runner.scan_task) if t))
        self.assertEqual(occupied, set())

    async def test_paged_fault_replay_and_expiry_release_capture_without_a_plan_removal(self):
        path = self.fixture(A)
        atomic(path/'.fault.json', {'reason':'space'})
        calls = []

        def school(action, job, **kwargs):
            calls.append((action, job, kwargs))
            if len(calls) > 1:
                raise ProtocolError(400)  # A terminal job no longer accepts a fault.
            return {}

        r = DeferredRunner(school, self.root, None, None, None)
        r.paged = True
        await asyncio.wait_for(r.capture(A), .5)
        await asyncio.wait_for(r.capture(A), .5)
        self.assertEqual(calls, [('fault',A,{'reason':'space'})]*2)
        path = self.fixture(B)
        atomic(path/'meta.json', {'created':int(time.time())-600,'expires':int(time.time())-1})
        await asyncio.wait_for(r.capture(B), .5)
        self.assertEqual(len(calls), 2, 'expired local work ends even if no new plan is available')

    async def test_expired_work_releases_finishing_slot_even_during_pause(self):
        path = self.fixture(A)
        r = DeferredRunner(None,self.root,None,None,None)
        r.pending = {A,B}
        r.due = {A}
        with self.assertRaises(WorkRevoked):
            await asyncio.wait_for(r.eligible(A,r.expiry(path)), .5)

    async def test_restart_replays_receipt_and_does_not_rerender_or_resend(self):
        path = self.fixture(A)
        full = path/'source-000.baci'; full.write_bytes(b'private classroom media')
        pdf = path/'doc-copy.pdf'; pdf.write_bytes(b'private board copy')
        first, second = (path/f'clip-000-{n:03d}.mp4' for n in range(2))
        for file in (first,second):
            file.write_bytes(b'\0\0\0\x18ftypTEST')
        SpoolJob.delivery_parts(path)
        sent, offline, terminal, attempt, claims = 0, True, False, None, []

        def school(action, job, **fields):
            nonlocal sent, terminal, attempt
            self.assertEqual(job,A)
            if action == 'poll':
                return {'command':'purge' if terminal else 'deliver','id':job,'sent':sent}
            if action == 'claim':
                self.assertIsNone(attempt)
                attempt = f'{fields["part"]:032x}'
                claims.append(fields['part'])
                return {'command':'send','attempt':attempt}
            if action == 'result':
                if offline:
                    raise ProtocolError(503)
                self.assertEqual(attempt,fields['attempt'])
                attempt = None
                sent = fields['part']
                terminal = sent == 2
                return {}
            if action == 'cleaned':
                self.assertTrue(terminal)
                return {}
            self.fail(action)

        def authorize():
            worker = DeferredRunner(school,self.root,None,None,None)
            worker.pending = {A}
            worker.permit, worker.until = A,time.monotonic()+10
            return worker

        with patch('recording.movie.render', side_effect=AssertionError('do not render committed parts')), \
                patch('recording.send_video', side_effect=[('accepted','901'),('accepted','902')]) as send:
            await authorize().finish(A)
            self.assertTrue((path/'receipt.json').exists())
            self.assertFalse(first.exists())
            self.assertTrue(second.exists() and full.exists() and pdf.exists())
            offline = False
            restarted = authorize()
            await restarted.maintain([A])
            self.assertEqual(sent,1)
            await restarted.finish(A)
            self.assertFalse(first.exists() or second.exists() or full.exists() or pdf.exists())
            await restarted.maintain([A])
        self.assertEqual(claims,[1,2])
        self.assertEqual(send.call_count,2)
        self.assertFalse(path.exists())

    async def test_does_not_start_provider_if_class_opens_during_claim(self):
        path = self.fixture(A)
        (path/'clip-000-000.mp4').write_bytes(b'\0\0\0\x18ftypTEST')
        SpoolJob.delivery_parts(path)
        issued = []
        runner = DeferredRunner(None,self.root,None,None,None)
        runner.pending = {A}
        runner.permit, runner.until = A,time.monotonic()+5

        def school(action,job,**fields):
            self.assertEqual(job,A)
            if action == 'poll':
                return {'command':'deliver','sent':0}
            if action == 'claim':
                runner.permit = None  # A concurrent plan supersedes this now-stale result.
                return {'command':'send','attempt':B}
            if action == 'result':
                issued.append(fields)
                return {}
            self.fail(action)

        runner.school = school
        with patch('recording.send_video') as send:
            await asyncio.wait_for(runner.finish(A),1)
            send.assert_not_called()
        self.assertEqual(issued,[{'attempt':B,'part':1,'result':'retry','message':''}])
        self.assertTrue((path/'clip-000-000.mp4').is_file())

    async def test_interrupted_source_and_retention_do_not_restart_a_partial_session(self):
        path = self.fixture(A)
        log = Timeline(path,Budget(self.root))
        log.append([['board',0,0,{}, {},0]])
        log.fp.close()  # Simulate a crash between writing packets and renaming a sealed file.
        calls = []
        def school(action, job, **args):
            calls.append((action,job,args))
            if action == 'poll':
                return {'command':'purge'}
            return {}
        worker = DeferredRunner(school,self.root,None,None,None)
        worker.wanted = {A}
        await worker.capture(A)
        self.assertEqual(calls,[('fault',A,{'reason':'capture'})])
        await worker.maintain([A])
        self.assertFalse(path.exists())
        old = self.fixture(B)
        (old/'source-000.baci').write_bytes(b'expired source')
        (old/'receipt.json').write_text('{}')
        atomic(old/'meta.json',{'created':int(time.time())-100000,'expires':int(time.time())-1})
        worker.sweep()
        self.assertTrue((old/'.cleaned').exists() and (old/'receipt.json').exists())
        self.assertFalse(sources(old))

    async def test_spool_inventory_counts_regular_sources_and_render_reserve_without_links(self):
        p, q = self.fixture(A),self.fixture(B)
        before = Budget(self.root)
        before.measure()
        totals = before.total, before.rendered, before.per_job.copy()
        (p/'source-000.baci').write_bytes(b'm' * 200)
        (p/'.render').mkdir()
        (p/'.render'/'frame.tmp').write_bytes(b'f' * 77)
        (q/'docs').mkdir()
        (q/'docs'/'board.pdf').write_bytes(b'b' * 52)
        (p/'bogus-source').symlink_to(p/'source-000.baci')
        (q/'bogus-dir').symlink_to(p, target_is_directory=True)
        (self.root/('e'*32)).symlink_to(q, target_is_directory=True)
        (self.root/'ignored').write_bytes(b'x'*500)
        after = Budget(self.root)
        after.measure()
        self.assertEqual(after.total - totals[0],252)
        self.assertEqual(after.rendered - totals[1],77)
        self.assertEqual(after.per_job,{p:totals[2][p]+277,q:totals[2][q]+52})
        (q/'docs'/'board.pdf').unlink()
        after.measure()
        self.assertEqual(after.per_job[q],totals[2][q]+52)
        after.measured = -float('inf')
        after.measure()
        self.assertEqual(after.per_job[q],totals[2][q])

    async def test_spool_limit_is_shared_and_reserves_room_to_encode(self):
        p, q = self.fixture(A),self.fixture(B)
        budget = Budget(self.root)
        # Use sparse bytes and reduced limits so the test is deterministic on small CI disks.
        with patch('archive.SPACE_LIMIT',3000), patch('archive.RENDER_RESERVE',1000), \
                patch('archive.SOURCE_LIMIT',1200), patch('archive.MIN_FREE',0):
            budget.measure()
            budget.add(p,1000)
            with self.assertRaises(SpaceError): budget.add(p,400)
            budget.add(q,800)
            with self.assertRaises(SpaceError): budget.add(q,400)
            self.assertTrue(budget.can_render())


# Use real encoded RTP from Chromium, including Opus (not Web Audio on the receiver).
# Only service discovery/subscription is faked. Sender code is test-only.
RTC_FIXTURE = b'''
(() => {
    const lk=window.LivekitClient, E=lk.RoomEvent;
    window.LivekitClient={...lk,Room:class {
        constructor() {
            this.events=new Map(); this.remoteParticipants=new Map(); this.timers=[];
            for (const identity of ['teacher-7','student-1']) this.remoteParticipants.set(identity,{identity,sid:'PA_'+identity,trackPublications:new Map()});
        }
        on(e,cb){this.events.set(e,cb); return this;}
        async connect(){
            const send=this.send=new RTCPeerConnection(), recv=this.recv=new RTCPeerConnection(), meta=new Map();
            send.onicecandidate=e=>e.candidate && recv.addIceCandidate(e.candidate).catch(()=>{});
            recv.onicecandidate=e=>e.candidate && send.addIceCandidate(e.candidate).catch(()=>{});
            const publish=(track,identity,source,id)=>{send.addTransceiver(track,{direction:'sendonly'});meta.set(id,{identity,source,kind:track.kind});};
            for (const [source,identity,id,color] of [['camera','teacher-7','TR_teacher','#ef301c'],['camera','student-1','TR_student','#19d822'],
                ['screen_share','student-1','TR_screen','#164cfa']]) {
                const c=document.createElement('canvas');c.width=320;c.height=180;
                const ctx=c.getContext('2d'); ctx.fillStyle=color;
                const draw=()=>ctx.fillRect(0,0,320,180);draw();this.timers.push(setInterval(draw,95));
                publish(c.captureStream(8).getVideoTracks()[0],identity,source,id);
            }
            const ac=this.audio=new AudioContext({sampleRate:48000});
            await ac.resume();
            for (const [identity,id,hz] of [['teacher-7','TR_audioTeacher',392],['student-1','TR_audioStudent',658],['student-1','TR_muted',976]]) {
                const osc=ac.createOscillator(),gain=ac.createGain(),target=ac.createMediaStreamDestination();
                osc.frequency.value=hz;gain.gain.value=.12;osc.connect(gain).connect(target);osc.start();
                publish(target.stream.getAudioTracks()[0],identity,'microphone',id);
            }
            const metadata=[...meta];
            recv.ontrack=e=>{
                const [trackSid,{identity,source,kind}]=metadata[Number(e.transceiver.mid)];
                const p=this.remoteParticipants.get(identity), track={kind,receiver:e.receiver};
                const pub={trackSid,source,isMuted:trackSid==='TR_muted',mimeType:kind==='audio'?'audio/opus':'video/VP8',
                    setSubscribed:on=>{if(on)this.events.get(E.TrackSubscribed)?.(track,pub,p);}};
                p.trackPublications.set(trackSid,pub);this.events.get(E.TrackPublished)?.(pub,p);
            };
            await send.setLocalDescription(await send.createOffer());await recv.setRemoteDescription(send.localDescription);
            await recv.setLocalDescription(await recv.createAnswer());await send.setRemoteDescription(recv.localDescription);
            this.state='connected';
            window.controls={mute:(person,sid)=>{ const pub=this.remoteParticipants.get(person).trackPublications.get(sid);
                pub.isMuted = true; this.events.get(E.TrackMuted)?.(pub); }};
        }
        async disconnect(){this.timers.forEach(clearInterval);this.recv?.close();this.send?.close();await this.audio?.close();}
    }};
})();
'''


def two_page_pdf():
    # First page includes joined Persian glyphs; second has a distinct gold paper for a page-turn proof.
    pdf = make_pdf(persian=True).split(b'\nxref\n',1)[0]+b'\n'
    pdf = pdf.replace(b'/Count 1 /Kids [3 0 R]', b'/Count 2 /Kids [3 0 R 9 0 R]')
    paint = b'0.98 0.81 0.15 rg 0 0 480 300 re f\n'
    pdf += b'9 0 obj\n<</Type /Page /Parent 2 0 R /MediaBox [0 0 480 300] /Resources <<>> /Contents 10 0 R>>\nendobj\n'
    pdf += b'10 0 obj\n<</Length ' + str(len(paint)).encode() + b'>>\nstream\n' + paint + b'endstream\nendobj\n'
    xref = b''.join(f'{pdf.index(f"{n} 0 obj".encode()):010d} 00000 n \n'.encode() for n in range(1,11))
    return pdf + b'xref\n0 11\n0000000000 65535 f \n' + xref + b'trailer\n<</Size 11 /Root 1 0 R>>\nstartxref\n' + str(len(pdf)).encode() + b'\n%%EOF\n'


@unittest.skipUnless(os.environ.get('MPM_TEST_CHROMIUM') and os.environ.get('MPM_REC_FFMPEG'),
                     'set MPM_TEST_CHROMIUM and MPM_REC_FFMPEG for real RTP/MP4 archive test')
class MediaTest(unittest.IsolatedAsyncioTestCase):
    async def test_three_light_sources_and_later_complete_movie(self):
        from playwright.async_api import async_playwright
        ffmpeg = os.environ['MPM_REC_FFMPEG']
        pdf, doc = two_page_pdf(), 'd'*32
        layout = {'board':False,'screen':None,'screenOwner':{'identity':'student-1','sid':'PA_student-1'}}
        downloaded = []
        phase, lit = 0, 0

        class School:
            def __call__(self, action, job, **data):
                assert action == 'board' and data['room'] == rooms[job]
                rev = 2 if phase == 2 else 1
                view = {'rev':rev,'cursor':int(phase == 1),'write':False}
                if data['board'] != rev:
                    view |= {'doc':{'id':doc,'mime':'application/pdf','bytes':len(pdf),'name':'Persian.pdf','pages':2},
                             'page':rev,'reset':True,'ops':[],'docs':[]}
                if phase == 1:
                    view['view'] = {'zoom':150,'crop':[4000,1000,9500,9000]}
                    if not data['ink']:
                        view['ops'] = [{'tool':'pen','id':'fixture','size':40,'color':'#f200e8','points':[[5100,3000],[8500,3000]]}]
                    left = round(1000*(lit-time.monotonic()))
                    if left > 0:
                        view['laser'] = {'points':[[6000,5700,left],[8000,5200,left]]}
                return {'board':view,'archive':dict(layout)}

            def document(self, job, room, key, byte_range):
                assert room == rooms[job] and key == doc and byte_range is None
                downloaded.append(job)
                return 200,{'content-type':'application/pdf'},pdf

        rooms = {job:f'test-{"a"*20}-v4-c{i}-{"c"*24}' for i,job in enumerate((A,B,C),1)}
        with tempfile.TemporaryDirectory() as tmp, ZipFile(ROOT/'artifacts/baci-classroom-recorder-v0.4.22.zip') as zipped:
            temp = Path(tmp)
            zipped.extractall(temp)
            # Keep the recorded source and fixtures compatible with old archives,
            # but exercise the current renderer with real encoded WebRTC inputs.
            (temp / 'replay.js').write_bytes((ROOT / 'server/recorder/replay.js').read_bytes())
            spool = temp/'spool'; spool.mkdir()
            for job in rooms: (spool/job).mkdir()
            renderer = ArchiveRenderer(temp)
            renderer.static['/livekit-client.umd.js'] = ('text/javascript',zipped.read('livekit-client.umd.js') + RTC_FIXTURE)
            budget = Budget(spool)
            async with async_playwright() as ply:
                browser = await ply.chromium.launch(executable_path=os.environ['MPM_TEST_CHROMIUM'],
                    args=['--autoplay-policy=no-user-gesture-required'], chromium_sandbox=True)
                await renderer.check(browser)
                jobs = [SourceCapture(browser,renderer,School(),spool/job,room,budget) for job,room in rooms.items()]
                with patch('asyncio.create_subprocess_exec', side_effect=AssertionError('no FFmpeg during class')):
                    try:
                        self.assertTrue(all(await asyncio.gather(*(cap.start({'token':'fixture','lease':int(time.time())+8,
                            'teacher':'teacher-7','title':{'school':'مدرسهٔ پژوهش','subject':'ریاضی'},'archive':dict(layout)}) for cap in jobs))))
                        self.assertFalse(any(await asyncio.gather(*(cap.page.evaluate('!!document.getElementById("film")') for cap in jobs))))
                        await asyncio.sleep(1.35)
                        layout['screen'] = 'TR_screen'
                        await asyncio.sleep(1.2)
                        layout['board'] = True; phase = 1; lit = time.monotonic()+1.8
                        await asyncio.gather(*(cap.renew(int(time.time())+8) for cap in jobs))
                        await asyncio.sleep(2.2)
                        await asyncio.gather(*(cap.page.evaluate("controls.mute('student-1','TR_screen')") for cap in jobs))
                        phase = 2
                        await asyncio.sleep(1.25)
                        self.assertFalse(list(spool.rglob('*.mp4')))
                    finally:
                        await asyncio.gather(*(cap.stop(True) for cap in jobs))
                self.assertCountEqual(downloaded,rooms.keys())
                for job in rooms:
                    [sealed] = sources(spool/job)
                    with sealed.open('rb') as fp:
                        self.assertGreater(duration(fp),4000)
                        tape = [event for _,event in events(fp)]
                    by_id = {e[1]:e[6] for e in tape if e[0]=='open'}
                    self.assertEqual({e[3] for e in tape if e[0]=='media'},{'audio/opus','video/vp8'})
                    self.assertFalse(any(by_id[e[1]]=='TR_muted' for e in tape if e[0]=='media'))
                async def permitted(): pass
                [part] = await render(browser,renderer,ffmpeg,spool/A,permitted,budget)
                await browser.close()
            self.assertFalse(list((spool/B).glob('clip-*')))
            data = part.read_bytes()
            self.assertIn(b'avc1',data); self.assertIn(b'mp4a',data)
            width,height=320,180
            rgb = subprocess.check_output([ffmpeg,'-v','error','-i',str(part),'-an','-vf',f'fps=4,scale={width}:{height}',
                '-threads','1','-pix_fmt','rgb24','-f','rawvideo','-'],timeout=15)
            step = width*height*3
            def pix(frame,x,y):
                p=frame+((y//4)*width+x//4)*3
                return rgb[p:p+3]
            red=lambda p:p[0]>170 and p[1]<100
            blue=lambda p:p[2]>170 and p[0]<90
            pink=lambda p:p[2]>170 and p[0]>155 and p[1]<90
            seq = range(0,len(rgb),step)
            self.assertTrue(any(red(pix(n,640,280)) for n in seq), 'teacher camera is the initial main source')
            self.assertTrue(any(blue(pix(n,640,280)) for n in seq), 'screen publication is promoted on the recorded timeline')
            blended = [n for n in seq if blue(pix(n,325,280)) and pink(pix(n,960,215))]
            self.assertTrue(blended, 'the off-peak film has picture, PDF ink and crop together')
            def laser(n):
                return sum(r > g+65 and r > b+65 for r,g,b in (pix(n,x,y) for x in range(824,1152,4) for y in range(270,405,4)))
            self.assertTrue(any(laser(n)>2 for n in blended), 'the laser is visible on the archived virtual clock')
            self.assertEqual(laser(blended[-1]),0, 'the laser fades while ink remains')
            gold = lambda p: p[0] > 200 and p[1] > 155 and p[2] < 110
            self.assertTrue(any(red(pix(n,325,280)) and gold(pix(n,960,270)) for n in seq), 'mute and PDF page turn replay without another PDF download')
            wav = subprocess.check_output([ffmpeg,'-v','error','-i',str(part),'-vn','-ar','8000','-f','s16le','-'],timeout=15)
            pcm = array.array('h',wav)
            def power(hz,offset):
                return abs(sum(sample*(1-math.cos(j*math.tau/1600))*complex(math.cos(j*math.tau*hz/8000),math.sin(j*math.tau*hz/8000))
                               for j,sample in enumerate(pcm[offset:offset+1600])))/800
            windows = range(1600,len(pcm)-1600,1600)
            self.assertTrue(any(min(power(392,i),power(658,i))>150 for i in windows), 'two Opus speakers are mixed in sync')
            self.assertLess(max(power(976,i) for i in windows),90,'a muted publication is never stored or played')
