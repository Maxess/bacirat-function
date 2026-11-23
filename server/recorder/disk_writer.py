"""One bounded journal queue per bell. File writes/checkpoints never run on the ingest loop."""
import asyncio
from collections import deque
import time

from archive import MIB, StoragePressure

QUEUE_BYTES = 8 * MIB  # includes the batch still in the writer
QUEUE_BLOCKS = 128
COALESCE = .10
BATCH = 512 * 1024
CHECKPOINT = 15.0
STALL = 4.0


class DiskWriter:
    def __init__(self, timeline, on_fault, warn):
        self.timeline, self.on_fault, self.warn = timeline, on_fault, warn
        self.blocks = deque()
        self.wake = asyncio.Event()
        self.pending_bytes = self.pending_blocks = 0
        self.active_since = None
        self.closing = self.discard = self.noted = False
        self.fault = None
        self.task = asyncio.create_task(self.run())
        self.monitor = asyncio.create_task(self.watch_pressure())

    def fail(self, error):
        if not self.fault:
            self.fault = error
            self.discard = True
            while self.blocks:
                blob, _ = self.blocks.popleft()
                self.pending_bytes -= len(blob)
                self.pending_blocks -= 1
            self.wake.set()
            self.on_fault(error)

    def append(self, blob):
        if self.fault:
            raise self.fault
        if self.closing:
            raise StoragePressure('source writer is closing')
        if self.pending_bytes + len(blob) > QUEUE_BYTES or self.pending_blocks >= QUEUE_BLOCKS:
            self.note(True)
            error = StoragePressure('capture disk queue is full')
            self.fail(error)
            raise error
        self.blocks.append((blob, time.monotonic()))
        self.pending_bytes += len(blob)
        self.pending_blocks += 1
        self.wake.set()
        self.note(self.pending_bytes >= QUEUE_BYTES//2 or self.pending_blocks >= QUEUE_BLOCKS//2)

    def note(self, pressure):
        if pressure and not self.noted:
            self.noted = True
            self.warn()

    async def watch_pressure(self):
        try:
            while not self.task.done() and not self.fault:
                await asyncio.sleep(.25)
                started = self.active_since
                if started is None and self.blocks:
                    started = self.blocks[0][1]
                age = 0 if started is None else time.monotonic() - started
                self.note(age >= STALL/2)
                if age >= STALL:
                    self.fail(StoragePressure('capture disk writer stalled'))
                if age < STALL/4 and self.pending_bytes < QUEUE_BYTES//4 and self.pending_blocks < QUEUE_BLOCKS//4:
                    self.noted = False
        except asyncio.CancelledError:
            pass

    async def run(self):
        checkpoint = time.monotonic() + CHECKPOINT
        dirty = False
        try:
            while not self.discard:
                if self.blocks:
                    if not self.closing:
                        await asyncio.sleep(COALESCE)
                    if self.discard:
                        break
                    group, size = [], 0
                    self.active_since = self.blocks[0][1]
                    while self.blocks and (not group or size + len(self.blocks[0][0]) <= BATCH):
                        block, _ = self.blocks.popleft()
                        group.append(block)
                        size += len(block)
                    await asyncio.to_thread(self.write_group, group)
                    self.pending_bytes -= size
                    self.pending_blocks -= len(group)
                    self.active_since = None
                    dirty = True
                if self.discard:
                    break
                if self.closing and not self.blocks:
                    break  # Seal already syncs the final group; don't fsync twice.
                if time.monotonic() >= checkpoint:
                    if dirty:
                        self.active_since = time.monotonic()
                        await asyncio.to_thread(self.timeline.checkpoint)
                        self.active_since = None
                        dirty = False
                    checkpoint = time.monotonic() + CHECKPOINT
                if not self.blocks and not self.closing:
                    self.wake.clear()
                    try:
                        await asyncio.wait_for(self.wake.wait(), max(.01, checkpoint-time.monotonic()))
                    except TimeoutError:
                        pass
        except Exception as error:
            self.fail(error)
        finally:
            self.blocks.clear()
            self.pending_bytes = self.pending_blocks = 0
            self.active_since = None

    def write_group(self, group):
        self.timeline.write(group[0] if len(group) == 1 else b''.join(group))

    async def finish(self, seal):
        self.closing, self.discard = True, self.discard or not seal
        self.wake.set()
        try:
            # Never cancel a thread owning an active FD or rename while it is writing.
            await asyncio.shield(self.task)
        except asyncio.CancelledError:
            self.discard = True
            self.wake.set()
            await asyncio.shield(self.task)
            raise
        finally:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        if seal and self.fault:
            raise self.fault
