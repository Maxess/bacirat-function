#!/usr/bin/env python3
"""Baci's single-slot recording pilot. Runs on the dedicated LiveKit VPS, not the web host."""
import argparse
import asyncio
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

from capture import CLIP, Capture, CaptureError, HEX, Renderer, check_clip, clips, lease_ms
from transport import ProtocolError, School, send_video

VERSION = '0.4.17'
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


class Runner:
    def __init__(self, school, root, browser, renderer, ffmpeg):
        self.school, self.root, self.browser, self.renderer, self.ffmpeg = school, root, browser, renderer, ffmpeg
        self.job = self.capture = None
        self.faulted = set()
        self.swept = self.connect_failures = 0

    async def rpc(self, action, **data):
        return await asyncio.to_thread(self.school, action, self.job, **data)

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

    def room_dir(self, expires):
        path = self.root / self.job
        if path.is_symlink() or not isinstance(expires, int) or not time.time() < expires <= time.time() + TTL:
            raise CaptureError('invalid recording directory or deadline')
        path.mkdir(mode=0o700, exist_ok=True)
        if not (path / 'meta.json').exists():
            atomic(path / 'meta.json', {'created': int(time.time()), 'expires': expires})
        return path

    async def stop(self, verify=True):
        if self.capture:
            cap, self.capture = self.capture, None
            try:
                await cap.stop(verify)
            except Exception:
                self.faulted.add(cap.job)
                raise CaptureError('capture not finalized') from None

    @staticmethod
    def erase_media(path, keep_receipt=False):
        keep = {'meta.json', 'receipt.json'} if keep_receipt else {'meta.json'}
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

    async def reap(self, path):
        if path.is_dir() and not path.is_symlink() and not (path / '.cleaned').exists():
            self.erase_media(path)
        try:
            await self.rpc('cleaned')
        except ProtocolError as exc:
            if exc.status not in (404, 410):
                raise
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        self.faulted.discard(self.job)
        self.job = None
        self.connect_failures = 0

    async def receipt(self, path):
        reply = read_obj(path)
        if not reply:
            return
        complete = reply.pop('complete', False) is True
        if reply['result'] == 'accepted':
            # Delete each confirmed part *before* waiting on PHP. On restart, replay
            # the durable receipt, never the upload. Final acceptance removes all media.
            if complete:
                self.erase_media(path.parent, keep_receipt=True)
            else:
                self.remove_sent(self.delivery_parts(path.parent), reply['part'])
        try:
            await self.rpc('result', **reply)
        except ProtocolError as error:
            if error.status != 410:
                raise
            # The server will not replay this in-flight part. A subsequent poll
            # determines whether it was already settled or is now uncertain.
        path.unlink(missing_ok=True)

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
        reply = await self.rpc('poll', running=running, available=available)
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
                if await self.capture.start(token, lease):
                    self.connect_failures = 0
                else:
                    await self.stop()
                    self.connect_failures += 1
                    if self.connect_failures >= 3:
                        raise CaptureError('media connection failed')
        elif op == 'deliver':
            parts = self.delivery_parts(path)
            sent = reply['sent']
            self.remove_sent(parts, sent)  # Also handles any confirmed parts from the previous release.
            part = sent + 1
            if part > len(parts):
                raise CaptureError('recording parts unavailable')
            file = check_clip(parts[sent])
            permit = await self.rpc('claim', part=part, total=len(parts))
            if permit.get('command') != 'send':
                return 1
            if not HEX.fullmatch(permit.get('attempt', '')):
                raise ProtocolError()
            # Once a permit has been issued, any crash before recording a receipt leaves
            # the attempt uncertain. Never call a provider a second time for that permit.
            result, message = await asyncio.to_thread(send_video, permit, file)
            atomic(path / 'receipt.json', {'attempt': permit['attempt'], 'part': part, 'result': result, 'message': message,
                                           'complete': result == 'accepted' and part == len(parts)})
            await self.receipt(path / 'receipt.json')
            return 0
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


async def service(args, config):
    from playwright.async_api import async_playwright

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
        runner = Runner(school, root, None, None, args.ffmpeg)
        runner.sweep()  # Expire old private media even if Chromium or FFmpeg is no longer installed.
        encoders = subprocess.check_output([args.ffmpeg, '-hide_banner', '-encoders'], timeout=8, stderr=subprocess.DEVNULL)
        if b' libx264 ' not in encoders or b' aac ' not in encoders:
            raise ValueError('FFmpeg H.264/AAC encoders needed')
        renderer = Renderer(ROOT)
        stopped = asyncio.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(signum, stopped.set)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True,
                args=['--autoplay-policy=no-user-gesture-required'])
            await renderer.check(browser)
            runner.browser, runner.renderer = browser, renderer
            print(f'recorder: compositor and encoder available ({VERSION})', flush=True)
            await runner.run(stopped)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--spool', type=Path, default=Path('/var/lib/school-classroom-recorder'))
    parser.add_argument('--ffmpeg', default='/usr/bin/ffmpeg')
    args = parser.parse_args()
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
