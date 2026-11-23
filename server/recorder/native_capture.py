"""Private, lease-bound IPC to the native read-only LiveKit receiver."""
import asyncio
from contextlib import suppress
import json
import struct
import time

from archive import DURATION, MAX_DURATION, MIB, SourceCapture, StoragePressure, Timeline
from disk_writer import DiskWriter
from capture import CaptureError, lease_ms

PROTOCOL = b'baci-rtp/2\n'
MESSAGE = struct.Struct('>BI')
DATA, READY, DONE = 1, 2, 3


def deadline(until):
    return time.monotonic_ns() + lease_ms(until) * 1_000_000


class NativeCapture(SourceCapture):
    def __init__(self, binary, school, directory, room, budget, *, notify=None):
        super().__init__(None, None, school, directory, room, budget)
        self.binary, self.notify = binary, notify
        self.process = self.reader = self.writer = None
        self.boot, self.failed = asyncio.Event(), asyncio.Event()
        self.connected, self.length = False, 0
        self.discarding = self.warned = False
        self.notice = None
        self.screen = None

    def set_fault(self, error):
        if self.fault is None and self.notify and not self.discarding:
            # Site learns the bell has failed even if cleanup waits for a blocked FD.
            self.notice = asyncio.create_task(self.notify(error))
        super().set_fault(error)
        self.failed.set()
        self.kill()  # Revoke RTC immediately, even while a disk syscall is uninterruptible.

    def disk_warning(self):
        if not self.warned:
            self.warned = True
            asyncio.create_task(asyncio.to_thread(print, f'recorder: capture storage pressure ({self.job})', flush=True))

    def record(self, rows):
        self.writer.append(self.log.pack(rows))

    async def start(self, reply):
        self.log = await asyncio.to_thread(Timeline, self.path, self.budget, indexed=True)
        self.writer = DiskWriter(self.log, self.set_fault, self.disk_warning)
        self.record([self.header(reply, native=True)])
        self.screen = (reply.get('archive') or {}).get('screenOwner')
        self.process = await asyncio.create_subprocess_exec(str(self.binary), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, env={'GOMEMLIMIT':'64MiB', 'GOMAXPROCS':'2'})
        self.reader = asyncio.create_task(self.read())
        if not await self.command(token=reply['token'], room=self.room, begin_ns=round(self.log.begin*1e9),
                                  until_ns=deadline(reply['lease']), media=reply.get('mediaMode', 'standard'),
                                  teacher=reply.get('teacher') or '', screen_owner=self.screen):
            return False
        await asyncio.wait_for(self.boot.wait(), 8.5)
        if self.fault:
            raise self.fault
        if not self.connected:
            return False
        self.board_task = asyncio.create_task(self.watch_board())
        return True

    async def command(self, **values):
        try:
            self.process.stdin.write((json.dumps(values, separators=(',', ':')) + '\n').encode())
            await asyncio.wait_for(self.process.stdin.drain(), .75)
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False  # May have reached its independent deadline; inspect the terminal record.

    async def read(self):
        try:
            stream = self.process.stdout
            # Handle a complete frame header or genuine EOF, never silently ignore a partial frame.
            while first := await stream.read(1):
                code, size = MESSAGE.unpack(first + await stream.readexactly(MESSAGE.size-1))
                if self.length:
                    raise CaptureError('native receiver sent data after closure')
                if code == DATA and 0 < size <= 2 * MIB:
                    self.writer.append(self.log.stamp(bytearray(await stream.readexactly(size))))
                    await asyncio.sleep(0)  # Give lease/board timers a turn if the pipe is already full.
                elif code == READY and not size and not self.connected:
                    self.connected = True
                    self.boot.set()
                elif code == DONE and self.connected and size == DURATION.size:
                    self.length, = DURATION.unpack(await stream.readexactly(size))
                    if not 0 < self.length <= MAX_DURATION:
                        raise CaptureError('invalid native source duration')
                else:
                    raise CaptureError('invalid native receiver response')
            rc = await self.process.wait()
            if rc == 3:
                self.disk_warning()
                raise StoragePressure('native receiver queue reached its limit')
            if self.connected and (rc or not self.length):
                raise CaptureError('native source did not finalize')
        except (CaptureError, OSError, asyncio.IncompleteReadError) as error:
            if not self.discarding:
                self.set_fault(error)
        finally:
            self.documents_open = False
            self.boot.set()

    async def status(self):
        if self.fault:
            raise self.fault
        return {'running': self.connected and not self.reader.done(), 'ms':self.length}

    async def layout_changed(self, archive):
        screen = archive.get('screenOwner')
        if screen != self.screen:
            if not self.reader.done():
                await self.command(select_screen=True, screen_owner=screen)
            self.screen = screen

    async def renew(self, until):
        if self.reader and not self.reader.done():
            await self.command(until_ns=deadline(until))

    async def finish_receiver(self, seal):
        if not self.reader or not seal:
            return 0
        if not self.reader.done():
            await self.command(stop=True)
        await asyncio.wait_for(asyncio.shield(self.reader), 2)
        return (await self.status())['ms']

    def kill(self):
        if self.process and self.process.returncode is None:
            with suppress(ProcessLookupError):
                self.process.kill()

    async def stop(self, seal):
        self.discarding = not seal
        if self.discarding:
            self.kill()
        await super().stop(seal)

    async def close_receiver(self):
        if self.process:
            self.kill()
            # Keep draining while stopping: waiting for a child with a full pipe can deadlock.
            if self.reader:
                await asyncio.gather(self.reader, return_exceptions=True)
            await self.process.stdout.read()
            self.process.stdin.close()
            await self.process.wait()

    async def close_log(self, ms):
        if self.log:
            log = self.log
            try:
                if self.writer:
                    await self.writer.finish(bool(ms))
                if self.fault:
                    ms = 0
            except BaseException:
                ms = 0
                raise
            finally:
                await asyncio.to_thread(log.close, ms)
                self.log = None
