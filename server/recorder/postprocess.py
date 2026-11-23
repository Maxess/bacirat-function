"""Low-priority, local-only film renderer. It has no site credentials or upload API."""
import argparse
import asyncio
from contextlib import contextmanager, suppress
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import subprocess
import time

from archive import ArchiveRenderer, Budget, SpaceError
from capture import CaptureError, HEX
import movie

ROOT = Path(__file__).resolve().parent
SPOOL = Path('/var/lib/school-classroom-recorder')
SOCKET = Path('/run/school-classroom-postprocess/render.sock')
MAX_LEASE = 6.05
PULSE_TIMEOUT = 1.25
CLEANUP_TIMEOUT = 2.5
RENDER_PROTOCOL = 'baci-render/1'


class WorkRevoked(Exception):
    pass


@contextmanager
def film_lock(path):
    # The name must remain in place until the private job directory is removed.
    # This fence also protects expiry/purge if the control socket is lost mid-frame.
    with (path / '.film.lock').open('a') as fp:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def private_dir(path):
    st = path.lstat()
    if path.resolve() != path or not stat.S_ISDIR(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.geteuid():
        raise ValueError('private service directory required')


async def read_message(reader):
    line = await reader.readline()
    if not line or not line.endswith(b'\n'):
        raise WorkRevoked()
    message = json.loads(line)
    if not isinstance(message, dict):
        raise WorkRevoked()
    return message


async def write_message(writer, **fields):
    writer.write(json.dumps(fields, separators=(',', ':')).encode('ascii') + b'\n')
    await asyncio.wait_for(writer.drain(), .5)


async def renderer_ready(socket_path):
    """A cheap local socket handshake; never starts Chromium or handles a job."""
    writer = None
    try:
        async with asyncio.timeout(.5):
            reader, writer = await asyncio.open_unix_connection(str(socket_path), limit=512)
            await write_message(writer, probe='ready')
            return (await read_message(reader)).get('result') == RENDER_PROTOCOL
    except (ValueError, OSError, WorkRevoked):
        return False
    finally:
        if writer is not None:
            writer.close()


async def request_render(socket_path, job, authorized_until):
    """Forward only the *remaining* school permit; never invent or extend a lease."""
    writer = answer = None
    try:
        if authorized_until() <= time.monotonic() or socket_path is None:
            raise WorkRevoked()
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(socket_path), limit=512), .5)
        until = authorized_until()
        if until <= time.monotonic():
            raise WorkRevoked()
        await write_message(writer, job=job, until=int(until * 1e9))
        answer = asyncio.create_task(read_message(reader))
        while not answer.done():
            await asyncio.wait({answer}, timeout=.25)
            until = authorized_until()
            if until <= time.monotonic():
                raise WorkRevoked()
            if not answer.done():
                # Heartbeats prove that the one site worker is still monitoring
                # the room plan. Repeating a permit never slides its deadline.
                await write_message(writer, until=int(until * 1e9))
        result = (await answer).get('result')
        if authorized_until() <= time.monotonic():
            raise WorkRevoked()
        if result == 'space':
            raise SpaceError('insufficient post-processing space')
        if result == 'failed':
            raise CaptureError('offline renderer failed')
        if result != 'done':
            raise WorkRevoked()
    except (ValueError, OSError, TimeoutError) as error:
        # Renderer restarts and read failures are retryable from the sealed source.
        raise WorkRevoked() from error
    finally:
        if writer is not None:
            if answer and not answer.done():
                # EOF revokes even an otherwise valid permit. Leave the other
                # direction open briefly for the renderer's cleanup acknowledgement.
                with suppress(OSError):
                    writer.write_eof()
                await asyncio.wait({answer}, timeout=CLEANUP_TIMEOUT + .25)
            writer.close()
            with suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), .5)
        if answer:
            answer.cancel()
            await asyncio.gather(answer, return_exceptions=True)


class Lease:
    def __init__(self, until, stopping):
        self.stopping = stopping
        self.until = self.pulse = 0
        self.set(until)

    def set(self, until):
        now = time.monotonic()
        if type(until) is not int or not 0 < until <= int((now + MAX_LEASE) * 1e9):
            raise WorkRevoked()
        self.until = until / 1e9
        self.pulse = now + PULSE_TIMEOUT
        self.check()

    def check(self):
        if self.stopping.is_set() or time.monotonic() >= min(self.until, self.pulse):
            raise WorkRevoked()

    async def guard(self):
        self.check()

    async def follow(self, reader):
        while True:
            self.check()
            delay = max(0, min(self.until, self.pulse) - time.monotonic())
            message = await asyncio.wait_for(read_message(reader), delay)
            self.check()  # A delayed renewal must not revive a revoked job.
            if message.keys() != {'until'}:
                raise WorkRevoked()
            self.set(message['until'])


class PostProcess:
    def __init__(self, spool, ffmpeg, *, assets=ROOT, launch=None):
        self.spool, self.ffmpeg, self.assets = spool, ffmpeg, assets
        self.launch = launch or {'headless':True, 'chromium_sandbox':True, 'args':['--autoplay-policy=no-user-gesture-required']}
        self.stopping = asyncio.Event()
        self.busy = None

    async def self_test(self):
        # Only a root maintenance probe runs this. No job, school RPC, or file output.
        from playwright.async_api import async_playwright

        encoders = await asyncio.to_thread(subprocess.check_output,
            [self.ffmpeg, '-hide_banner', '-encoders'], timeout=6, stderr=subprocess.DEVNULL)
        if not all(tag in encoders for tag in (b' libx264 ', b' aac ')):
            raise CaptureError('FFmpeg H.264/AAC encoders needed')
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(**self.launch, timeout=7000)
            try:
                await ArchiveRenderer(self.assets).check_replay(browser)
            finally:
                await browser.close()

    async def render(self, job, lease):
        from playwright.async_api import async_playwright

        path = self.spool / job
        lease.check()
        if path.is_symlink() or not path.is_dir():
            raise CaptureError('sealed input is missing')
        with film_lock(path):
            if any((path / flag).exists() for flag in ('.cleaned', '.capturing', '.fault.json', 'delivery.json', 'receipt.json')):
                raise WorkRevoked()
            renderer = ArchiveRenderer(self.assets)
            async with async_playwright() as playwright:
                lease.check()
                browser = await playwright.chromium.launch(**self.launch)
                try:
                    lease.check()
                    await renderer.check_replay(browser)
                    await movie.render(browser, renderer, self.ffmpeg, path, lease.guard, Budget(self.spool))
                finally:
                    await browser.close()

    async def handle(self, reader, writer):
        answer, tasks = 'paused', []
        current = asyncio.current_task()
        try:
            raw = writer.get_extra_info('socket').getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
            uid = struct.unpack('3i', raw)[1]
            if uid not in (0, os.geteuid()) or self.stopping.is_set():
                return
            start = await asyncio.wait_for(read_message(reader), .5)
            if start == {'probe':'ready'}:
                answer = RENDER_PROTOCOL  # including while a film is running
                return
            if self.busy:
                answer = 'busy'
                return
            self.busy = current
            if start == {'probe':'self-test'} and uid == 0:
                try:
                    await asyncio.wait_for(self.self_test(), 18)
                    answer = RENDER_PROTOCOL + ' tested'
                except Exception as error:
                    print(f'postprocess: startup self-test failed ({type(error).__name__})', flush=True)
                    answer = 'failed'
                return
            if uid != os.geteuid():
                return  # Root probe cannot submit a job; the original lease fence is unchanged.
            job = start.get('job')
            if start.keys() != {'job','until'} or not isinstance(job, str) or not HEX.fullmatch(job):
                raise WorkRevoked()
            lease = Lease(start['until'], self.stopping)
            work = asyncio.create_task(self.render(job, lease))
            watch = asyncio.create_task(lease.follow(reader))
            shutdown = asyncio.create_task(self.stopping.wait())
            tasks = [work, watch, shutdown]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if not work.done() or watch.done() or shutdown.done():
                raise WorkRevoked()
            lease.check()
            try:
                await work
                answer = 'done'
            except (WorkRevoked, BlockingIOError):
                pass
            except SpaceError:
                answer = 'space'
            except Exception as error:
                print(f'postprocess: film failed ({type(error).__name__})', flush=True)
                answer = 'failed'
        except (WorkRevoked, ValueError, OSError):
            pass
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                _, alive = await asyncio.wait(tasks, timeout=CLEANUP_TIMEOUT)
                if alive:
                    # systemd reaps this service's entire cgroup (including the
                    # browser and FFmpeg) and starts a clean listener. No send permit.
                    print('postprocess: render cancellation timed out; recycling the service', flush=True)
                    os._exit(1)
                await asyncio.gather(*tasks, return_exceptions=True)
            if self.busy is current:
                self.busy = None
            with suppress(OSError, TimeoutError):
                await write_message(writer, result=answer)
            writer.close()
            with suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), .5)


def notify(message):
    endpoint = os.environ.get('NOTIFY_SOCKET')
    if endpoint:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_NONBLOCK) as sock:
            with suppress(OSError):
                sock.sendto(message, '\0' + endpoint[1:] if endpoint.startswith('@') else endpoint)


async def watchdog(stopping):
    endpoint = os.environ.get('NOTIFY_SOCKET')
    period = int(os.environ.get('WATCHDOG_USEC', '0')) / 1e6
    if not endpoint or period <= 0:
        return
    while not stopping.is_set():
        notify(b'WATCHDOG=1')
        await asyncio.sleep(min(1, period / 3))


async def serve(args, *, assets=ROOT, launch=None):
    private_dir(args.spool)
    private_dir(args.socket.parent)
    if not args.socket.is_absolute():
        raise ValueError('absolute socket path required')
    worker = PostProcess(args.spool, args.ffmpeg, assets=assets, launch=launch)
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, worker.stopping.set)
    with args.socket.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.socket.exists() or args.socket.is_symlink():
            if not stat.S_ISSOCK(args.socket.lstat().st_mode):
                raise ValueError('invalid renderer socket')
            args.socket.unlink()
        server = await asyncio.start_unix_server(worker.handle, path=args.socket, limit=512)
        async with server:
            notify(b'READY=1')
            ping = asyncio.create_task(watchdog(worker.stopping))
            try:
                await worker.stopping.wait()
                server.close()
                if worker.busy:
                    await worker.busy
            finally:
                ping.cancel()
                await asyncio.gather(ping, return_exceptions=True)
                args.socket.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spool', type=Path, default=SPOOL)
    parser.add_argument('--socket', type=Path, default=SOCKET)
    parser.add_argument('--ffmpeg', default='/usr/bin/ffmpeg')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if os.geteuid() == 0:
            raise ValueError('require an unprivileged renderer')
        asyncio.run(serve(args))
    except Exception as error:
        print(f'postprocess: unavailable ({type(error).__name__})', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
