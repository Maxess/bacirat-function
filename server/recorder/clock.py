"""Offline synchronization for native capture. Nothing here runs during a bell."""
import asyncio
from bisect import bisect_left
from collections import defaultdict
from statistics import median

from archive import MAX_DURATION, events
from capture import FPS, CaptureError

CLOCK = 'rtcp-v1'
MAX_SHIFT = 320  # bound against receive time before trusting a sender report
WINDOW = 10_000
REPORT_LIMIT = 150_000
TRACK_LIMIT = 4096


def delta(ts, earlier):
    """Signed RTP delta, including the 32-bit wrap (one bell is < half a wrap)."""
    return ((ts - earlier + 0x80000000) & 0xffffffff) - 0x80000000


class TrackClock:
    def __init__(self, opened, publisher, rate, duration):
        self.opened, self.closed, self.publisher, self.rate = opened, duration, publisher, rate
        self.reports = []
        self.last = None

    def align(self, received, ts, offsets):
        # RTP extrapolation also keeps packets smooth before a sender report is
        # available (e.g. a very short screen share), without comparing RTP
        # counters of different sources.
        baseline = received
        if self.last is not None:
            old, last_ts = self.last
            predicted = old + delta(ts, last_ts)*1000/self.rate
            if abs(predicted-received) <= MAX_SHIFT:
                baseline = predicted
        options = self.reports
        if options:
            i = bisect_left(options, (received,))
            nearby = options[max(0, i-1):i+1]
            report = min(nearby, key=lambda row: abs(row[0]-received))
            at, rtp, ntp = report
            offset = offsets.get((self.publisher, at//WINDOW))
            if offset is not None and abs(at-received) <= 2*WINDOW:
                period = 1000/self.rate
                if len(nearby) == 2:
                    a, b = nearby
                    ticks, ms = delta(b[1], a[1]), b[2] - a[2]
                    if ticks > 0 and abs(ms-ticks*period) <= 4 + ticks*period*.002:
                        period = ms/ticks  # slow skew, but not a wall-clock jump
                estimate = ntp + delta(ts, rtp)*period + offset
                if abs(estimate-received) <= MAX_SHIFT:
                    baseline = estimate
        at = max(self.opened, min(self.closed, baseline))
        if self.last is not None:
            at = max(self.last[0], at)
        self.last = (at, ts)
        return round(at)


class Clocks:
    @classmethod
    async def scan(cls, fp, length, wait):
        self = cls()
        self.tracks, bins = {}, defaultdict(list)
        reports = 0
        for n, (_, event) in enumerate(events(fp, headers_only=True)):
            if n % 512 == 0:
                await asyncio.sleep(0)
                await wait()
            kind, track_id, at = event[:3]
            if type(at) is not int or not 0 <= at <= MAX_DURATION or type(track_id) is not int:
                raise CaptureError('invalid native event time')
            if kind == 'open':
                if track_id in self.tracks or len(self.tracks) >= TRACK_LIMIT or len(event) != 8 or not isinstance(event[5], str):
                    raise CaptureError('invalid native track')
                self.tracks[track_id] = TrackClock(at, event[5], 48000 if event[3] == 'microphone' else 90000, length)
            elif kind == 'end':
                track = self.tracks[track_id]
                track.closed = min(length, at)
            elif kind == 'sr':
                track = self.tracks[track_id]
                _, _, _, rtp, ntp = event
                if (type(ntp) is not int or not 0 < ntp <= (1<<32)*1000 or type(rtp) is not int or not 0 <= rtp < (1<<32)):
                    raise CaptureError('invalid sender report')
                if not track.reports or at-track.reports[-1][0] >= WINDOW//2:
                    reports += 1
                    if reports > REPORT_LIMIT:
                        raise CaptureError('too many sender reports')
                    track.reports.append((at, rtp, ntp))
                    bins[(track.publisher, at//WINDOW)].append(at - ntp)
            elif kind not in ('board', 'media'):
                raise CaptureError('invalid native source event')
        # One common offset per publisher (not one per track). Independent
        # clocks on different student devices need not have matching NTP dates.
        self.offsets = {key: median(v) for key, v in bins.items()}
        return self

    def apply(self, tape):
        for received, event in tape:
            if event[0] == 'sr':
                continue
            if event[0] == 'media':
                track = self.tracks[event[1]]
                at = track.align(event[2], event[4], self.offsets)
                # Select the closest output picture, not always the next one.
                # At 12 fps this removes an additional one-sided frame delay.
                event[2] = max(track.opened, at - 500//FPS) if track.rate == 90000 else at
            yield received, event
