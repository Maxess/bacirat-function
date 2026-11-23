"""Native receiver IPC, long-running clock calibration and live-priority cleanup."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import tempfile
from threading import Event
import time
import types
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server/recorder'))
from archive import Budget, StoragePressure, Timeline, duration, events, sources
from capture import CaptureError
from clock import Clocks
from native_capture import NativeCapture
from postprocess import PostProcess, request_render, read_message, write_message
from recording import DeferredRunner, SpoolJob, WorkRevoked

JOB = '7'*32
ROOM = f'test-{"a"*20}-v4-c7-{"b"*24}'

SCRIPT = r'''
import json, struct, sys

def msg(code, body=b''):
    sys.stdout.buffer.write(struct.pack('>BI', code, len(body)) + body)
    sys.stdout.buffer.flush()

def record(header, binary=b''):
    header=json.dumps(header).encode()
    return struct.pack('>III', 0, len(header), len(binary)) + header + binary

initial=json.loads(sys.stdin.readline())
assert initial['room'].startswith('test-') and initial['token']=='pipe-only' and len(sys.argv)==1
assert initial['until_ns'] > initial['begin_ns']
msg(2)
msg(1, record(['open',1,2,'microphone','teacher-1','PA_a','TR_b',False])
      + record(['media',1,5,'audio/opus',150,True], b'\xf8\xff\xfe'))
for line in sys.stdin:
    cmd=json.loads(line)
    if cmd.get('stop'):
        msg(3, struct.pack('>I', 1000))
        break
    assert cmd['until_ns'] > initial['begin_ns']
'''


class NativeIPCTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.spool = Path(tmp.name)
        self.path = self.spool/JOB
        self.path.mkdir()
        self.app = self.spool/'receiver'

    def build_fixture(self, script):
        self.app.write_text(f'#!{sys.executable}\n' + script)
        self.app.chmod(0o700)

    def school(self, action, job, **data):
        assert action == 'board' and job == JOB and data['room'] == ROOM
        return {'board':{'rev':1,'cursor':0,'doc':None,'page':1,'reset':True,'ops':[],'docs':[]}, 'archive':{}}

    async def begin(self, **kwargs):
        cap = NativeCapture(self.app, self.school, self.path, ROOM, Budget(self.spool), **kwargs)
        self.addAsyncCleanup(cap.stop, False)
        self.assertTrue(await cap.start({'token':'pipe-only','lease':int(time.time())+7,'teacher':'teacher-1'}))
        return cap

    async def test_binary_capture_seals_without_allocating_a_browser(self):
        self.build_fixture(SCRIPT)
        cap = await self.begin()
        for _ in range(30):
            if cap.board_seen:
                break
            await asyncio.sleep(.02)
        await cap.renew(int(time.time())+7)
        await cap.stop(True)
        # Cancellation and repeated cleanup cannot discard a sealed archive.
        [sealed] = sources(self.path)
        with sealed.open('rb') as fp:
            self.assertEqual(duration(fp),1000)
            rows = [event for _, event in events(fp)]
        self.assertEqual(rows[0][3]['clock'],'rtcp-v1')
        self.assertEqual(next(row for row in rows if row[0]=='media')[-1],'+P/+')
        self.assertTrue(any(row[0] == 'board' for row in rows))
        self.assertIsNone(cap.browser)
        self.assertIsNone(cap.page)

    async def test_bad_batch_never_seals(self):
        self.build_fixture(SCRIPT.replace("msg(3, struct.pack('>I', 1000))", "msg(1, b'incomplete')"))
        cap = await self.begin()
        with self.assertRaises(CaptureError):
            await cap.stop(True)
        self.assertFalse(sources(self.path))
        self.assertFalse((self.path/'.capturing').exists())

    async def test_stop_without_consent_drops_native_source(self):
        self.build_fixture(SCRIPT)
        cap = await self.begin()
        await cap.stop(False)
        self.assertEqual(list(self.path.iterdir()),[])
        self.assertIsNotNone(cap.process.returncode)

    async def test_queued_board_document_never_fetches_after_close(self):
        self.build_fixture(SCRIPT)
        cap = await self.begin()
        keys, requests = ('a'*32,'b'*32), []
        started, release = Event(), Event()

        def download(job, room, key, byte_range):
            requests.append(key)
            started.set()
            release.wait(3)
            return 200,{'content-type':'application/pdf'},b'x'

        cap.school = types.SimpleNamespace(document=download)
        # Stop the normal board poll; test only the queued document copy lane.
        cap.board_task.cancel()
        await asyncio.gather(cap.board_task,return_exceptions=True)
        for key in keys:
            cap.documents[key] = asyncio.create_task(cap.copy_doc({'id':key,'bytes':1,'mime':'application/pdf'}))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait,.5))
            await asyncio.wait_for(cap.stop(False),1)
        finally:
            release.set()
        self.assertEqual(requests,[keys[0]])
        self.assertFalse(list(self.path.iterdir()))

    async def test_periodic_checkpoint_indexes_only_flushed_bytes(self):
        self.build_fixture(SCRIPT)
        with patch('disk_writer.CHECKPOINT',.15):
            cap = await self.begin()
            index = self.path/'.capturing.idx'
            for _ in range(35):
                if index.exists() and index.read_bytes():
                    break
                await asyncio.sleep(.03)
            self.assertFalse(sources(self.path), 'partial input is never visible to the renderer')
            [first] = index.read_text().splitlines()
            start, end, offset, committed = map(int,first.split())
            self.assertEqual((start, offset),(0,10))
            self.assertGreater(end,start)
            self.assertLessEqual(committed,(self.path/'.capturing').stat().st_size)
            await cap.stop(True)
        [sealed] = sources(self.path)
        self.assertFalse(index.exists())
        self.assertEqual(int(sealed.with_suffix('.idx').read_text().split()[-1]),sealed.stat().st_size)

    async def test_disk_stall_stops_only_affected_receiver_and_notifies_before_cleanup(self):
        self.build_fixture(SCRIPT)
        other_jobs = ('8'*32,'9'*32)
        for job in other_jobs:
            (self.spool/job).mkdir()
        original = Timeline.write
        entering, release = Event(), Event()
        reported = asyncio.Event()
        faults = []

        def stuck(timeline, data):
            if timeline.path == self.path:
                entering.set()
                if not release.wait(8):
                    raise RuntimeError('test left a disk syscall blocked')
            original(timeline, data)

        async def notify(error):
            faults.append(error)
            reported.set()

        def school(action, job, **fields):
            return self.school(action,JOB,**fields)

        caps = [NativeCapture(self.app, school, self.spool/j, ROOM, Budget(self.spool), notify=notify if j == JOB else None)
                for j in (JOB,*other_jobs)]
        reply = {'token':'pipe-only','lease':int(time.time())+7,'teacher':'teacher-1'}
        with patch.object(Timeline,'write',stuck), patch('disk_writer.STALL',.7), patch.object(NativeCapture,'disk_warning') as warn:
            try:
                self.assertTrue(all(await asyncio.gather(*(cap.start(reply) for cap in caps))))
                self.assertTrue(await asyncio.to_thread(entering.wait,.5))
                await asyncio.wait_for(reported.wait(), 2)
                await asyncio.wait_for(caps[0].process.wait(), 1)
                self.assertEqual(len(faults),1)
                self.assertIsInstance(faults[0],StoragePressure)
                warn.assert_called_once()
                self.assertTrue((self.path/'.capturing').exists(), 'writer still owns the partial file')
                self.assertFalse(release.is_set())
                for cap in caps[1:]:
                    self.assertTrue((await cap.status())['running'])
                    await cap.renew(int(time.time())+7)
                    await cap.stop(True)
                    self.assertTrue(sources(cap.path))
            finally:
                release.set()
                await asyncio.gather(*(cap.stop(False) for cap in caps))
        self.assertFalse(list(self.path.iterdir()))

    async def test_full_disk_queue_never_seals_or_waits_on_receiver(self):
        burst = "\nimport time\nfor _ in range(80):\n    time.sleep(.005)\n    msg(1,record(['media',1,7,'audio/opus',200,True],b'A'*8192))\n"
        self.build_fixture(SCRIPT.replace('for line in sys.stdin:',burst + '\nfor line in sys.stdin:'))
        entered, release = Event(), Event()
        original = Timeline.write
        def blocked(log, data):
            entered.set()
            release.wait(5)
            original(log, data)
        with patch.object(Timeline,'write',blocked), patch('disk_writer.QUEUE_BYTES',96*1024), \
                patch('disk_writer.COALESCE',.02), patch.object(NativeCapture,'disk_warning') as warn:
            cap = None
            try:
                cap = await self.begin()
                self.assertTrue(await asyncio.to_thread(entered.wait,.5))
                await asyncio.wait_for(cap.failed.wait(), 1)
                self.assertIsInstance(cap.fault,StoragePressure)
                self.assertLessEqual(cap.writer.pending_bytes,96*1024)
                await asyncio.wait_for(cap.process.wait(), 1)
                self.assertFalse(release.is_set())
                warn.assert_called_once()
            finally:
                release.set()
                if cap:
                    await cap.stop(False)
        self.assertFalse(sources(self.path))



class ClockTest(unittest.IsolatedAsyncioTestCase):
    async def test_independent_publisher_clocks_wrap_and_align_over_45_minutes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/JOB
            path.mkdir()
            timeline = Timeline(path,Budget(path.parent))
            length = 2_700_000
            # Use independent sender dates/counters and skew. At the receiver,
            # each camera arrives ~140 ms later than the corresponding mic.
            items = [(1,'PA_a',48000,0xfffffeaa,4_018_420_000_000,1.00047,26),
                     (2,'PA_a',90000,901,4_018_420_000_000,.99953,168),
                     (3,'PA_b',48000,400_000_000,4_018_898_000_000,1.0001,32),
                     (4,'PA_b',90000,0xfffffc98,4_018_898_000_000,1.00004,150)]
            def rtp(entry, at):
                return (round(at/1000*entry[2]*entry[5]) + entry[3]) & 0xffffffff
            def report(entry, at):
                return ['sr',entry[0],at+22+(entry[0]%2)*3,rtp(entry,at),entry[4]+at]
            tape = []
            for item in items:
                i, peer, rate, *_ = item
                tape.append(['open',i,0,'microphone' if rate==48000 else 'camera','teacher-1',peer,f'TR_{i}',False])
                tape.extend(report(item, time_ms) for time_ms in range(0,length,5000))
            timeline.append(tape)
            timeline.close(length)
            async def permitted(): pass
            [sealed] = sources(path)
            with sealed.open('rb') as fp:
                clocks = await Clocks.scan(fp,duration(fp),permitted)
            for cue in (500,32_000,899_950,1_800_000,length-300):
                aligned = []
                for item in items:
                    track = clocks.tracks[item[0]]
                    received = cue + item[-1]
                    aligned.append(track.align(received,rtp(item,cue),clocks.offsets))
                self.assertLess(max(aligned)-min(aligned),12, (cue,aligned))
                self.assertTrue(all(abs(x-cue) < 32 for x in aligned))


class PriorityTest(unittest.IsolatedAsyncioTestCase):
    async def test_socket_revocation_cancels_renderer_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = Path(directory)
            path = spool/JOB
            path.mkdir()
            saved = path/'source-000.baci'
            saved.write_bytes(b'original')
            endpoint = spool/'render.sock'
            expires = time.time()+60
            worker = DeferredRunner(None, spool, None, None, '/ffmpeg', native='/verified', post_socket=endpoint)
            worker.pending = {JOB}
            worker.permit, worker.until = JOB,time.monotonic()+5
            rendered, canceled = asyncio.Event(), asyncio.Event()
            browser = types.SimpleNamespace(close=AsyncMock())
            chromium = types.SimpleNamespace(launch=AsyncMock(return_value=browser))

            @asynccontextmanager
            async def playwright():
                yield types.SimpleNamespace(chromium=chromium)

            async def render(*args):
                rendered.set()
                try:
                    await asyncio.Future()  # IPC revokes even during a frame, not just at a guard.
                finally:
                    canceled.set()

            fake_api = types.SimpleNamespace(async_playwright=playwright, TimeoutError=TimeoutError)
            fake_renderer = types.SimpleNamespace(check_replay=AsyncMock())
            service = PostProcess(spool,'/ffmpeg')
            async with await asyncio.start_unix_server(service.handle,path=endpoint,limit=512):
                with patch.dict(sys.modules, {'playwright.async_api':fake_api}), \
                        patch('postprocess.ArchiveRenderer', return_value=fake_renderer), \
                        patch('postprocess.movie.render', side_effect=render):
                    task = asyncio.create_task(worker.offline(JOB,expires))
                    await asyncio.wait_for(rendered.wait(), 1)
                    with self.assertRaises(BlockingIOError):
                        SpoolJob.purge_media(path)  # Cannot erase under an active renderer.
                    with self.assertRaises(WorkRevoked):
                        await request_render(endpoint,'b'*32,lambda:worker.until)
                    worker.permit = None  # Even an open bell without recording revokes the window.
                    with self.assertRaises(WorkRevoked):
                        await asyncio.wait_for(task, 1)
                    self.assertTrue(canceled.is_set())
                    browser.close.assert_awaited_once()
                    self.assertEqual(saved.read_bytes(),b'original')
                    with self.assertRaises(WorkRevoked):
                        await worker.offline(JOB,expires)
                    chromium.launch.assert_awaited_once()
            self.assertIsNone(service.busy)

    async def test_server_revokes_on_heartbeat_loss_even_with_unexpired_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = Path(directory)
            endpoint = spool/'render.sock'
            service = PostProcess(spool,'/ffmpeg')
            left = asyncio.Event()

            async def stalled(*_):
                try:
                    await asyncio.Future()
                finally:
                    left.set()

            async with await asyncio.start_unix_server(service.handle,path=endpoint,limit=512):
                with patch.object(service,'render',side_effect=stalled), patch('postprocess.PULSE_TIMEOUT',.2):
                    stream, session = await asyncio.open_unix_connection(endpoint)
                    await write_message(session,job=JOB,until=time.monotonic_ns()+5_000_000_000)
                    self.assertEqual(await asyncio.wait_for(read_message(stream),1),{'result':'paused'})
                    self.assertTrue(left.is_set())
                    session.close()
                    await session.wait_closed()


if __name__ == '__main__':
    unittest.main()
