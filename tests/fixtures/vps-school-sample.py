"""QA guest sampler. No service calls, CPU stress, or private payloads."""
import ctypes
import json
import os
from pathlib import Path
import struct
import sys
import time


PHASE = Path(sys.argv[1])
CGROUP = Path('/sys/fs/cgroup/system.slice')
NET = Path('/sys/class/net')
SPOOL = Path('/var/lib/school-classroom-recorder')
UNITS = ('sfu', 'admission', 'https', 'recorder', 'postprocess')
LINK_KEYS = ('rx_bytes', 'tx_bytes', 'rx_packets', 'tx_packets', 'rx_dropped', 'tx_dropped', 'rx_errors', 'tx_errors')
NICS = [path for path in NET.iterdir() if (path/'address').read_text().strip() == '52:54:00:12:34:57']
assert len(NICS) == 1, 'isolated benchmark NIC required'
assert Path('/etc/school-classroom/offline-vps.json').is_file(), 'offline QA installation required'


def read_pairs(path):
    return {key.rstrip(':'): int(value) for key, value, *_ in (line.split() for line in path.read_text().splitlines())}


def udp():
    keys, data = (line.split()[1:] for line in Path('/proc/net/snmp').read_text().splitlines() if line.startswith('Udp:'))
    return {k: int(v) for k, v in zip(keys, data, strict=True)}


def open_syscall_counters():
    # perf_event_open with PID_CGROUP counts every thread, including threads
    # born after sampling. It does not log packets, addresses or credentials.
    if '--sfu-syscalls' not in sys.argv[2:] or os.uname().machine != 'x86_64':
        return {}
    open_events = {}
    sfu = os.open(CGROUP/'school-classroom-sfu.service', os.O_RDONLY | os.O_CLOEXEC)
    try:
        perf = ctypes.CDLL(None, use_errno=True).syscall
        for name in ('sendmmsg','recvmmsg','sendmsg','recvmsg','sendto','recvfrom'):
            event = int(Path(f'/sys/kernel/tracing/events/syscalls/sys_enter_{name}/id').read_text())
            attr = ctypes.create_string_buffer(struct.pack('=IIQ', 2, 64, event), 64)  # TRACEPOINT, VER0
            record = open_events[name] = []
            for cpu in sorted(os.sched_getaffinity(0)):
                fd = perf(298, ctypes.byref(attr), sfu, cpu, -1, (1 << 2) | (1 << 3))
                if fd < 0:
                    raise OSError(ctypes.get_errno(), 'SFU perf cgroup tracepoint unavailable')
                record.append(fd)
    except OSError as error:
        for fds in open_events.values():
            for fd in fds:
                os.close(fd)
        print(f'QA_SCHOOL perf tracepoints unavailable (errno {error.errno})', file=sys.stderr)
        return {}
    finally:
        os.close(sfu)
    return open_events


COUNTERS = open_syscall_counters()


def syscall_counts():
    return {name:sum(struct.unpack('=Q', os.read(fd,8))[0] for fd in fds) for name,fds in COUNTERS.items()}


def link(path):
    return {key: int((path/'statistics'/key).read_text()) for key in LINK_KEYS}


def sample(phase):
    mem = read_pairs(Path('/proc/meminfo'))
    raw = {key: [int(v) for v in row[:8]] for key, *row in (
        line.split() for line in Path('/proc/stat').read_text().splitlines() if line.startswith('cpu'))}
    units = {}
    for unit in UNITS:
        name = CGROUP/f'school-classroom-{unit}.service'
        units[unit] = {
            'cpu_us': read_pairs(name/'cpu.stat')['usage_usec'],
            'mem': int((name/'memory.current').read_text()),
            'pids': int((name/'pids.current').read_text()),
            'oom_kill': read_pairs(name/'memory.events')['oom_kill'],
        }
    # Live Timeline writes .capturing (and its index), only naming the sealed
    # archive source-*.baci when the bell ends. Size all sources, and log an
    # independent byte counter for each live bell (digits from fake bell IDs).
    open_sources, source_bytes, spool_bytes = {}, 0, 0
    for job in SPOOL.iterdir():
        if not job.is_dir():
            continue
        for path in job.rglob('*'):
            try:
                if not path.is_file():
                    continue
                size = path.stat().st_size
            except FileNotFoundError:
                continue  # A receipt may have cleaned it between glob and stat.
            spool_bytes += size
            if path.name == '.capturing' or path.name.startswith('source-') or path.name == '.capturing.idx':
                source_bytes += size
            if path.name == '.capturing':
                open_sources[str(int(job.name, 16))] = size
    usage = os.statvfs(SPOOL)
    return dict(disk_free_bytes=usage.f_bavail * usage.f_frsize, t=time.monotonic(), phase=phase, hz=os.sysconf('SC_CLK_TCK'), cpu=raw, units=units, network=link(NICS[0]),
                loopback=link(NET/'lo'), udp=udp(), sfu_syscalls=syscall_counts(), mem_available=mem['MemAvailable'], mem_total=mem['MemTotal'],
                swap_kib=mem['SwapTotal']-mem['SwapFree'], source_bytes=source_bytes, spool_bytes=spool_bytes,
                open_sources=open_sources, oom=read_pairs(Path('/proc/vmstat'))['oom_kill'])


start = time.monotonic()
while time.monotonic() - start < 1500:
    phase = PHASE.read_text().strip()
    if phase == 'halt':
        break
    assert phase in ('idle','join','warmup','live','reconnect','profile','closing','wait','render'), phase
    print(json.dumps(sample(phase), separators=(',', ':')), flush=True)
    time.sleep(.25 if phase == 'join' else 1)
else:
    raise SystemExit('benchmark sampler reached timeout')
