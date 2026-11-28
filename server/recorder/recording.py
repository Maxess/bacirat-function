#!/usr/bin/env python3
"""Baci's classroom archive worker. Runs on the dedicated LiveKit VPS, not the web host."""
import argparse
import asyncio
from contextlib import suppress
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time

from archive import ArchiveRenderer, Budget, MIB, ROOM, SourceCapture, SpaceError, StoragePressure
from postprocess import WorkRevoked, film_lock, renderer_ready, request_render
import movie
from capture import CLIP, Capture, CaptureError, HEX, Renderer, check_clip, clips, lease_ms
from transport import ProtocolError, School, send_video

VERSION = '0.4.23'
TTL = 26 * 3600  # Hard local maximum, even if PHP/DB remains unavailable.
SPACE_LIMIT = 1800 * 1024 * 1024
ROOT = Path(__file__).resolve().parent


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic(path, obj):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as fp:
        json.dump(obj, fp)
        fp.flush()
        os.fsync(fp.fileno())
    tmp.replace(path)
    sync_dir(path.parent)


def read_obj(path):
    if not path.is_file():
        return None
    with path.open(encoding='utf-8') as fp:
        return json.loads(fp.read(8192))


class SpoolJob:
    def __init__(self, school, root, job=None):
        self.school, self.root, self.job = school, root, job

    async def rpc(self, action, **data):
        return await asyncio.to_thread(self.school, action, self.job, **data)

    def room_dir(self, expires):
        path = self.root / self.job
        if path.is_symlink() or not isinstance(expires, int) or not time.time() < expires <= time.time() + TTL:
            raise CaptureError('invalid recording directory or deadline')
        path.mkdir(mode=0o700, exist_ok=True)
        if not (path / 'meta.json').exists():
            atomic(path / 'meta.json', {'created': int(time.time()), 'expires': expires})
        return path

    @staticmethod
    def erase_media(path, keep_receipt=False):
        keep = {'meta.json', '.film.lock'}
        if keep_receipt:
            keep.add('receipt.json')
        for file in path.iterdir():
            if file.name not in keep:
                if file.is_dir() and not file.is_symlink():
                    shutil.rmtree(file)
                else:
                    file.unlink()
        (path / '.cleaned').touch(mode=0o600)
        sync_dir(path)

    @staticmethod
    def delivery_parts(path):
        index = path / 'delivery.json'
        saved = read_obj(index)
        if saved is None:
            parts = [file.name for file in clips(path)]
            if not parts:
                raise CaptureError('recording parts unavailable')
            # Written before requesting a permit. Accepted parts are unlinked without
            # renumbering later parts or changing the total after a worker restart.
            atomic(index, {'parts':parts})
        else:
            parts = saved.get('parts') if isinstance(saved, dict) else None
            if not isinstance(parts, list) or not 1 <= len(parts) <= 32 or any(
                    not isinstance(name, str) or not CLIP.fullmatch(name) for name in parts) or parts != sorted(set(parts)):
                raise CaptureError('invalid recording part index')
        return [path / name for name in parts]

    @staticmethod
    def remove_sent(parts, count):
        if type(count) is not int or not 0 <= count <= len(parts):
            raise CaptureError('invalid sent part count')
        if count:
            for file in parts[:count]:
                file.unlink(missing_ok=True)
            sync_dir(parts[0].parent)

    @classmethod
    def purge_media(cls, path, keep_receipt=False):
        if path.is_dir() and not path.is_symlink():
            with film_lock(path):
                if not (path / '.cleaned').exists():
                    cls.erase_media(path, keep_receipt=keep_receipt)

    @staticmethod
    def purge_dir(path):
        if path.is_dir() and not path.is_symlink():
            with film_lock(path):
                shutil.rmtree(path)

    async def reap(self, path):
        await asyncio.to_thread(self.purge_media, path)
        try:
            await self.rpc('cleaned')
        except ProtocolError as exc:
            if exc.status not in (404, 410):
                raise
        await asyncio.to_thread(self.purge_dir, path)
        self.job = None

    def receipt_files(self, path):
        reply = read_obj(path)
        if not reply:
            return None
        complete = reply.pop('complete', False) is True
        if reply['result'] == 'accepted':
            # Delete each confirmed part *before* waiting on PHP. On restart, replay
            # the durable receipt, never the upload. Final acceptance removes all media.
            if complete:
                self.purge_media(path.parent, keep_receipt=True)
            elif not (path.parent / '.cleaned').exists():
                self.remove_sent(self.delivery_parts(path.parent), reply['part'])
        return reply

    async def receipt(self, path):
        reply = await asyncio.to_thread(self.receipt_files, path)
        if not reply:
            return
        try:
            await self.rpc('result', **reply)
        except ProtocolError as error:
            if error.status != 410:
                raise
            # The server will not replay this in-flight part. A subsequent poll
            # determines whether it was already settled or is now uncertain.
        await asyncio.to_thread(path.unlink, missing_ok=True)

    def delivery_request(self, path, sent):
        parts = self.delivery_parts(path)
        self.remove_sent(parts, sent)  # Also handles any confirmed parts from the previous release.
        part = sent + 1
        if part > len(parts):
            raise CaptureError('recording parts unavailable')
        return check_clip(parts[sent]), part, len(parts)

    async def send_part(self, path, reply, guard=None, allowed=None):
        file, part, total = await asyncio.to_thread(self.delivery_request, path, reply['sent'])
        if guard:
            await guard()
        permit = await self.rpc('claim', part=part, total=total)
        if permit.get('command') != 'send':
            return 1
        if not HEX.fullmatch(permit.get('attempt', '')):
            raise ProtocolError()
        # Once a permit has been issued, any crash before recording a receipt leaves
        # the attempt uncertain. Never call a provider a second time for that permit.
        def deliver():
            if allowed:
                try:
                    if allowed() <= time.monotonic():
                        return 'retry', ''  # No provider request was attempted.
                except WorkRevoked:
                    return 'retry', ''
            return send_video(permit, file)

        result, message = await asyncio.to_thread(deliver)
        await asyncio.to_thread(atomic, path / 'receipt.json',
            {'attempt': permit['attempt'], 'part': part, 'result': result, 'message': message,
             'complete': result == 'accepted' and part == total})
        await self.receipt(path / 'receipt.json')
        return 0


class Runner(SpoolJob):
    """Reference protocol-1 pipeline retained for the published film regressions, not used by service()."""
    def __init__(self, school, root, browser, renderer, ffmpeg):
        super().__init__(school, root)
        self.browser, self.renderer, self.ffmpeg = browser, renderer, ffmpeg
        self.capture = None
        self.faulted = set()
        self.swept = self.connect_failures = 0

    def dirs(self):
        return sorted(p for p in self.root.iterdir() if HEX.fullmatch(p.name) and p.is_dir() and not p.is_symlink())

    def sweep(self):
        if time.monotonic() - self.swept < 30:
            return
        self.swept = time.monotonic()
        now = time.time()
        for path in self.dirs():
            if self.capture and path.name == self.capture.job:
                continue
            end = read_obj(path / 'meta.json') or {}
            if min(end.get('expires', now + TTL), end.get('created', path.stat().st_ctime) + TTL) <= now:
                # Leave only the acknowledgment marker. While the site is down this still
                # removes the private video on schedule; metadata is removed separately.
                if not (path / '.cleaned').exists():
                    self.erase_media(path)
                elif now - (path / '.cleaned').stat().st_mtime > TTL:
                    shutil.rmtree(path)

    async def stop(self, verify=True):
        if self.capture:
            cap, self.capture = self.capture, None
            try:
                await cap.stop(verify)
            except Exception:
                self.faulted.add(cap.job)
                raise CaptureError('capture not finalized') from None

    async def tick(self):
        self.sweep()
        if self.job is None:
            self.job = next((p.name for p in self.dirs() if not (p / '.retained').exists() or (p / '.cleaned').exists()), None)
        if self.capture and not await self.capture.running():
            await self.stop()
        running = self.capture.room if self.capture else None
        if self.job:
            path = self.root / self.job
            await self.receipt(path / 'receipt.json')
        available = shutil.disk_usage(self.root).free >= 256 * 1024 * 1024 and (
            sum(p.stat().st_size for d in self.dirs() for p in d.glob('clip-*.mp4')) < SPACE_LIMIT)
        reply = await self.rpc('poll', running=running, available=available, archiveLayout=True)
        op, job = reply.get('command'), reply.get('id')
        if op == 'idle' and not self.job:
            return 4
        if not isinstance(job, str) or not HEX.fullmatch(job) or self.job not in (job, None):
            raise ProtocolError()
        self.job = job
        path = self.root / job
        if self.capture and not available:
            raise CaptureError('spool limit reached')
        if self.capture and (op != 'record' or self.capture.room != reply.get('room')):
            await self.stop()
        if op == 'purge':
            self.faulted.discard(self.job)
            self.connect_failures = 0
            await self.reap(path)
        elif op == 'retain':
            if path.exists():
                (path / '.retained').touch(mode=0o600)
            self.faulted.discard(job)
            self.job = None
            self.connect_failures = 0
        elif job in self.faulted or (self.capture is None and (path / '.capturing').exists()):
            await self.rpc('fault')
            self.faulted.discard(job)
        elif op == 'record':
            lease = reply.get('lease')
            if type(lease) is not int or lease_ms(lease) < 1000:
                await self.stop()
                return 1
            if self.capture:
                await self.capture.renew(lease)
            else:
                if not available:
                    raise CaptureError('spool limit reached')
                room, token = reply.get('room'), reply.get('token')
                if not isinstance(room, str) or not re.fullmatch(r'test-[a-f0-9]{20}-v[34]-c[1-9][0-9]{0,9}-[a-f0-9]{24}', room) or not isinstance(token, str):
                    raise ProtocolError()
                self.capture = Capture(self.browser, self.renderer, self.school, room, job, self.room_dir(reply['expires']), self.ffmpeg)
                if await self.capture.start(token, lease, reply.get('title'), reply.get('teacher'), reply.get('archive')):
                    self.connect_failures = 0
                else:
                    await self.stop()
                    self.connect_failures += 1
                    if self.connect_failures >= 3:
                        raise CaptureError('media connection failed')
        elif op == 'deliver':
            return await self.send_part(path, reply)
        elif op != 'hold':
            raise ProtocolError()
        return 1.4 if op in ('record', 'deliver') else 2

    async def run(self, stopped):
        noted = None
        while not stopped.is_set():
            if not self.browser.is_connected():
                # Let systemd restart the compositor instead of advertising a dead browser.
                raise RuntimeError('recording browser disconnected')
            try:
                delay = await self.tick()
                noted = None
            except ProtocolError as exc:
                delay = 3  # Page's independent lease stops capture if renewals cease.
                if noted != ('site', exc.status):
                    print('recorder: school connection or lease not confirmed', flush=True)
                    noted = ('site', exc.status)
            except (CaptureError, TimeoutError, OSError, ValueError, KeyError) as exc:
                delay = 2
                if self.job and not (self.root / self.job / 'receipt.json').exists():
                    self.faulted.add(self.job)
                # An unlink/settlement error with a receipt is retried at the start
                # of the next tick; do not turn that known attempt into a capture fault.
                try:
                    await self.stop(False)
                except CaptureError:
                    pass
                if noted != type(exc):
                    print('recorder: local capture or spool problem', flush=True)
                    noted = type(exc)
            if delay > 0:
                try:
                    await asyncio.wait_for(stopped.wait(), delay)
                except TimeoutError:
                    pass
        await self.stop()


PAGED_PLAN = 3
PLAN_PAGE = 128  # Bound each reply, never the number of jobs or the number of pages.


class DeferredRunner:
    """Independent source leases, paged job discovery, and one finishing task.

    A slow upload/PDF/rpc cannot delay another room's lease. Nothing can render on
    cached consent alone: the short plan lease is checked before each output frame.
    """
    def __init__(self, school, root, browser, renderer, ffmpeg, *, native=None, post_socket=None):
        self.school, self.root, self.browser, self.renderer, self.ffmpeg = school, root, browser, renderer, ffmpeg
        self.native, self.post_socket = native, post_socket
        self.budget = Budget(root)
        self.captures, self.wanted, self.pending, self.due = {}, set(), set(), set()
        self.control = SpoolJob(school, root)
        self.work = self.finish_task = self.repair = self.repairing = None
        self.scan_task = self.discovery = None
        self.paged, self.current_ok = False, False
        self.settle_hints, self.missing_hints = set(), set()
        self.available, self.on_disk, self.receipts, self.unsettled = False, set(), set(), set()
        self.sampled = -float('inf')
        self.permit, self.until = None, 0
        self.stopping = False

    def dirs(self):
        return {p.name for p in self.root.iterdir() if HEX.fullmatch(p.name) and not p.is_symlink() and p.is_dir()}

    def local(self, job):
        # Callers only use checked ids under the private service directory.
        return self.root / job, SpoolJob(self.school, self.root, job)

    @staticmethod
    def expiry(path):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise CaptureError('recording path is not private')
        created = info.st_ctime
        try:
            meta = read_obj(path / 'meta.json') or {}
            return min(meta.get('expires', created + TTL), meta.get('created', created) + TTL)
        except (TypeError, ValueError, AttributeError):
            return time.time()

    def sweep(self, busy=None):
        # FS expiry also runs when the site's plan endpoint is offline. A small receipt
        # may outlive the private inputs; expired inputs can never be rendered/resumed.
        if busy is None:
            busy = set(self.captures) | {self.work, self.repairing}
        now = time.time()
        for job in self.dirs() - busy:
            path = self.root / job
            with suppress(FileNotFoundError, BlockingIOError):
                if self.expiry(path) <= now:
                    marker = path / '.cleaned'
                    if not marker.exists():
                        SpoolJob.purge_media(path, keep_receipt=True)
                    elif now - marker.stat().st_mtime > TTL:
                        SpoolJob.purge_dir(path)

    def inspect_storage(self, busy):
        self.sweep(busy)
        paths = self.dirs()
        receipts = {job for job in paths if (self.root / job / 'receipt.json').is_file()}
        unsettled = {job for job in paths if not (self.root / job / '.retained').exists()
                     or (self.root / job / '.cleaned').exists()}
        return self.budget.room(4 * MIB), paths, receipts, unsettled

    @staticmethod
    def reason(error):
        full = isinstance(error, (SpaceError, StoragePressure)) or (isinstance(error, OSError) and error.errno in (errno.ENOSPC, errno.EDQUOT))
        return 'space' if full else 'capture'

    async def report_fault(self, site, error):
        with suppress(ProtocolError):
            await site.rpc('fault', reason=self.reason(error))

    async def fail(self, job, error):
        path, site = self.local(job)
        reason = self.reason(error)

        def remember():
            if path.is_symlink() or (path / 'receipt.json').is_file():
                return False  # Preserve known provider outcomes, including unlink failures.
            path.mkdir(mode=0o700, exist_ok=True)
            atomic(path / '.fault.json', {'reason':reason})
            return True

        print(f'recorder: archive stopped ({reason})', flush=True)
        try:
            if not await asyncio.to_thread(remember):
                return
        except OSError:
            pass  # Disk full: still notify the worker API; won't seal partial media.
        await self.report_fault(site, error)

    async def capture(self, job):
        path, site = self.local(job)
        cap, failures = None, 0
        try:
            if await asyncio.to_thread(lambda: path.is_symlink() or (path / '.capturing').exists() or (path / '.cleaned').exists()):
                raise CaptureError('interrupted or expired source capture')
            while not self.stopping and (self.paged or job in self.wanted):
                if not cap and (fault := await asyncio.to_thread(read_obj, path / '.fault.json')):
                    with suppress(ProtocolError):
                        await site.rpc('fault', reason=fault.get('reason'))
                    await asyncio.sleep(2)
                    continue
                if cap and not (await cap.status())['running']:
                    closing, cap = cap, None
                    await closing.stop(True)
                try:
                    # The optional flag belongs to the matched native capture/renderer pack.
                    reply = await site.rpc('poll', running=cap.room if cap else None,
                                           **({'archiveCamera': True} if self.native else {}))
                except ProtocolError:
                    await asyncio.sleep(2)  # The receiver's lease is independent of TLS.
                    continue
                op, room = reply.get('command'), reply.get('room')
                if cap and (op != 'record' or cap.room != room):
                    closing, cap = cap, None
                    await closing.stop(op != 'purge')
                if op in ('purge', 'retain', 'render', 'deliver') or (op == 'hold' and reply.get('state') != 'recording'):
                    return
                if op == 'hold':
                    await asyncio.sleep(1.4)
                    continue
                if op != 'record' or reply.get('id') != job or not isinstance(room, str) or not ROOM.fullmatch(room):
                    raise ProtocolError()
                lease = reply.get('lease')
                if type(lease) is not int or lease_ms(lease) < 1000:
                    await asyncio.sleep(.5)
                    continue
                if cap:
                    await cap.renew(lease)
                elif not self.stopping and (self.paged or job in self.wanted):
                    if not await asyncio.to_thread(self.budget.room, MIB):
                        raise SpaceError('no room in source spool')
                    if not isinstance(reply.get('token'), str):
                        raise ProtocolError()
                    directory = await asyncio.to_thread(site.room_dir, reply['expires'])
                    if self.native:
                        from native_capture import NativeCapture
                        cap = NativeCapture(self.native, self.school, directory, room, self.budget,
                            notify=lambda fault: self.report_fault(site, fault))
                    else:
                        cap = SourceCapture(self.browser, self.renderer, self.school, directory, room, self.budget)
                    if await cap.start(reply):
                        failures = 0
                    else:
                        closing, cap = cap, None
                        await closing.stop(False)
                        failures += 1
                        if failures >= 3:
                            raise CaptureError('media connection unavailable')
                await asyncio.sleep(1.25)
            if cap:
                closing, cap = cap, None
                await closing.stop(self.paged or job in self.pending)
        except Exception as error:
            if cap:
                with suppress(Exception):
                    await cap.stop(False)
            await self.fail(job, error)

    def authorized(self, job, expires):
        still_pending = self.current_ok if self.paged else job in self.pending and job not in self.due
        if self.stopping or not still_pending or time.time() >= expires:
            raise WorkRevoked()
        return self.until if self.permit == job else 0

    async def eligible(self, job, expires):
        while self.authorized(job, expires) <= time.monotonic():
            await asyncio.sleep(.20)

    async def offline(self, job, expires):
        await request_render(self.post_socket, job, lambda: self.authorized(job, expires))

    async def finish(self, job):
        path, site = self.local(job)
        try:
            expires = await asyncio.to_thread(self.expiry, path)
            wait = lambda: self.eligible(job, expires)
            await wait()
            if fault := await asyncio.to_thread(read_obj, path / '.fault.json'):
                await site.rpc('fault', reason=fault.get('reason'))
                return
            if await asyncio.to_thread((path / 'receipt.json').is_file):
                await site.receipt(path / 'receipt.json')
                return  # Re-poll after committing; never send using a stale `sent` cursor.
            reply = await site.rpc('poll')
            if reply.get('command') == 'render':
                if not await asyncio.to_thread((path / 'delivery.json').is_file):
                    if self.native:
                        await self.offline(job, expires)
                    else:
                        await movie.render(self.browser, self.renderer, self.ffmpeg, path, wait, self.budget)
                # Commit a durable part index before any credentials or delivery attempt.
                parts = await asyncio.to_thread(site.delivery_parts, path)
                await wait()
                if (await site.rpc('rendered', total=len(parts))).get('command') != 'deliver':
                    return
            elif reply.get('command') != 'deliver':
                return
            if not await asyncio.to_thread((path / 'delivery.json').is_file):
                raise CaptureError('committed film index disappeared')
            await wait()
            await site.send_part(path, reply, guard=wait, allowed=lambda: self.authorized(job, expires))
        except (WorkRevoked, ProtocolError):
            pass
        except Exception as error:
            await self.fail(job, error)

    async def maintain(self, jobs):
        for job in jobs:
            if job in self.captures or job == self.work or self.stopping:
                continue
            self.repairing = job
            try:
                path, site = self.local(job)
                await site.receipt(path / 'receipt.json')
                result = await site.rpc('poll')
                if result.get('command') == 'purge':
                    await site.reap(path)
                elif result.get('command') == 'retain':
                    await asyncio.to_thread((path / '.retained').touch, mode=0o600)
            except (ProtocolError, OSError, ValueError, CaptureError, KeyError):
                pass  # Durable receipts and cleanup markers are retried at the next plan.
            finally:
                self.repairing = None

    @staticmethod
    def ids(reply, name, limit):
        values = reply.get(name)
        if not isinstance(values, list) or len(values) > limit or any(not isinstance(v, str) or not HEX.fullmatch(v) for v in values):
            raise ProtocolError()
        ids = set(values)
        if len(ids) != len(values):
            raise ProtocolError()
        return ids

    @classmethod
    def read_page(cls, reply, after=''):
        if reply.get('plan_format') != PAGED_PLAN or 'next' not in reply:
            raise ProtocolError()
        wanted, pending, due = (cls.ids(reply, name, PLAN_PAGE) for name in ('capture', 'pending', 'settle'))
        cursor = reply['next']
        if (not wanted <= pending or not due <= pending or wanted & due or (pending and min(pending) <= after)
                or (cursor is not None and (not isinstance(cursor, str) or not HEX.fullmatch(cursor)
                                            or cursor <= after or (pending and cursor < max(pending))))):
            raise ProtocolError()
        return wanted, pending, due, cursor

    def start_captures(self, wanted):
        if self.stopping:
            return
        for job in sorted(wanted - self.captures.keys()):
            self.captures[job] = asyncio.create_task(self.capture(job))

    async def discover(self, cursor, unchecked):
        try:
            while cursor is not None and not self.stopping:
                reply = await self.control.rpc('plan-page', after=cursor)
                wanted, pending, due, cursor = self.read_page(reply, after=cursor)
                self.start_captures(wanted)
                self.settle_hints.update(due)
                unchecked.difference_update(pending)
        except ProtocolError:
            return  # Retry from a fresh head. An incomplete traversal says nothing about missing jobs.
        if cursor is None:
            self.missing_hints.update(unchecked)

    async def tick(self):
        for job, task in list(self.captures.items()):
            if task.done():
                del self.captures[job]
                task.result()
        if self.finish_task and self.finish_task.done():
            task, self.finish_task, self.work = self.finish_task, None, None
            task.result()
        if self.repair and self.repair.done():
            task, self.repair = self.repair, None
            task.result()
        if self.discovery and self.discovery.done():
            task, self.discovery = self.discovery, None
            task.result()
        if self.scan_task and self.scan_task.done():
            task, self.scan_task = self.scan_task, None
            try:
                self.available, self.on_disk, self.receipts, self.unsettled = task.result()
                self.sampled = time.monotonic()
            except OSError:
                self.available, self.sampled = False, -float('inf')
        if self.scan_task is None:
            busy = set(self.captures) | {self.work, self.repairing}
            self.scan_task = asyncio.create_task(asyncio.to_thread(self.inspect_storage, busy))
        sampled = time.monotonic() - self.sampled < 5
        available = self.available and sampled
        if self.native:
            available = available and await renderer_ready(self.post_socket)
        reply = await self.control.rpc('plan', available=available, current=self.work, plan_format=PAGED_PLAN,
                                       capture_space=self.available if sampled else None,
                                       **({'native_release': VERSION} if self.native else {}))
        work, lease = reply.get('work'), reply.get('lease')
        if type(lease) is not int or (work is not None and (not isinstance(work, str) or not HEX.fullmatch(work))):
            raise ProtocolError()
        if reply.get('plan_format') == PAGED_PLAN:
            wanted, pending, due, cursor = self.read_page(reply)
            if type(reply.get('current_ok')) is not bool or (work is not None and work in wanted | due):
                raise ProtocolError()
            self.paged = True
            self.current_ok = reply['current_ok'] and work in (self.work, None)
            # The head and continuation pages discover jobs; only each room's own
            # poll renews or ends its capture. No partial list revokes another room.
            self.start_captures(wanted)
            self.settle_hints.update(due)
            if self.discovery is None:
                unchecked = (self.on_disk & self.unsettled) - pending
                if cursor is None:
                    self.missing_hints.update(unchecked)
                else:
                    self.discovery = asyncio.create_task(self.discover(cursor, unchecked))
        else:
            if self.paged or 'plan_format' in reply:
                raise ProtocolError()
            # Old sites can still be paired during rollout; their own admission limits apply.
            wanted, pending, due = (self.ids(reply, name, 24) for name in ('capture', 'pending', 'settle'))
            if (not wanted <= pending or not due <= pending or wanted & due
                    or (work is not None and (work not in pending or work in wanted | due))):
                raise ProtocolError()
            self.wanted, self.pending, self.due = wanted, pending, due
            self.start_captures(wanted)
        self.permit = work
        self.until = time.monotonic() + min(6, max(0, lease - time.time()))
        if work and not self.finish_task and work not in self.captures and work != self.repairing and time.monotonic() < self.until:
            self.work, self.current_ok = work, True
            self.finish_task = asyncio.create_task(self.finish(work))
        if not self.repair:
            if self.paged:
                candidates = self.settle_hints | self.missing_hints
                self.settle_hints.clear()
                self.missing_hints.clear()
            else:
                candidates = self.due | ((self.on_disk - self.pending) & self.unsettled)
            jobs = sorted(self.receipts) + sorted(candidates - self.receipts)
            if jobs:
                self.repair = asyncio.create_task(self.maintain(jobs))

    async def run(self, stopped):
        noted = False
        try:
            while not stopped.is_set():
                if self.browser is not None and not self.browser.is_connected():
                    raise RuntimeError('archive browser disconnected')
                try:
                    await self.tick()
                    noted = False
                except ProtocolError:
                    self.permit, self.until = None, 0
                    if not noted:
                        print('recorder: school plan not confirmed', flush=True)
                        noted = True
                with suppress(TimeoutError):
                    await asyncio.wait_for(stopped.wait(), 2 if noted else 1.25)
        finally:
            self.stopping = True
            self.permit, self.until = None, 0
            await asyncio.gather(*self.captures.values(), *(task for task in (self.finish_task,self.repair,self.scan_task,self.discovery) if task),
                                 return_exceptions=True)


async def service(args, config):
    root = args.spool
    root.mkdir(mode=0o700, exist_ok=True, parents=False)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid() or root.resolve() != root:
        raise ValueError('spool must be a private directory owned by the recorder')
    with (root / '.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        worker = read_obj(root / 'worker.json')
        if worker is None:
            worker = {'id': secrets.token_hex(16)}
            atomic(root / 'worker.json', worker)
        if not HEX.fullmatch(worker['id']):
            raise ValueError('worker identity invalid')
        school = School(config['site'], config['secret'], worker['id'])
        native = args.native_receiver
        runner = DeferredRunner(school, root, None, None, args.ffmpeg, native=native, post_socket=args.postprocess_socket)
        # The first control tick starts bounded, off-loop expiry/storage sampling.
        stopped = asyncio.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(signum, stopped.set)
        if native is not None:
            from native_capture import PROTOCOL
            if not native.is_absolute() or not native.is_file() or subprocess.check_output(
                    [native, '--check'], timeout=2, stderr=subprocess.DEVNULL, env={}) != PROTOCOL:
                raise ValueError('compatible native receiver required')
            print(f'recorder: native live capture available ({VERSION}); film renderer starts only off peak', flush=True)
            await runner.run(stopped)
            return
        from playwright.async_api import async_playwright

        encoders = subprocess.check_output([args.ffmpeg, '-hide_banner', '-encoders'], timeout=8, stderr=subprocess.DEVNULL)
        if b' libx264 ' not in encoders or b' aac ' not in encoders:
            raise ValueError('FFmpeg H.264/AAC encoders needed')
        renderer = ArchiveRenderer(ROOT)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True,
                args=['--autoplay-policy=no-user-gesture-required'])
            await renderer.check(browser)
            runner.browser, runner.renderer = browser, renderer
            print(f'recorder: deferred capture and encoder available ({VERSION})', flush=True)
            await runner.run(stopped)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--spool', type=Path, default=Path('/var/lib/school-classroom-recorder'))
    parser.add_argument('--ffmpeg', default='/usr/bin/ffmpeg')
    parser.add_argument('--native-receiver', type=Path, help='absolute path to the verified native receiver')
    parser.add_argument('--postprocess-socket', type=Path, help='private Unix socket of the separate finishing service')
    args = parser.parse_args()
    if bool(args.native_receiver) != bool(args.postprocess_socket) or (args.postprocess_socket and not args.postprocess_socket.is_absolute()):
        parser.error('native capture and the private absolute post-process socket must be supplied together')
    os.umask(0o077)
    try:
        if os.geteuid() == 0 or args.config.lstat().st_mode & 0o077:
            raise ValueError('require non-root and private credentials')
        config = read_obj(args.config)
        if set(config) != {'site', 'secret'} or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', config['secret']):
            raise ValueError('configuration invalid')
        asyncio.run(service(args, config))
        return 0
    except Exception as exc:
        # Playwright and HTTP exceptions may contain private request details.
        print(f'recorder: unavailable ({type(exc).__name__})', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
