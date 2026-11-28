"""QA guest sampler. No service calls, CPU stress, or private payloads."""
import json
import os
from pathlib import Path
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
    objects = [file for job in SPOOL.iterdir() if job.is_dir() for file in job.glob('source-*.baci')]
    sizes = []
    for path in objects:
        try:
            sizes.append(path.stat().st_size)
        except FileNotFoundError:
            pass  # A receipt may have cleaned it between glob and stat.
    return dict(t=time.monotonic(), phase=phase, hz=os.sysconf('SC_CLK_TCK'), cpu=raw, units=units, network=link(NICS[0]),
                loopback=link(NET/'lo'), udp=udp(), mem_available=mem['MemAvailable'], mem_total=mem['MemTotal'],
                swap_kib=mem['SwapTotal']-mem['SwapFree'], spool_bytes=sum(sizes), oom=read_pairs(Path('/proc/vmstat'))['oom_kill'])


start = time.monotonic()
while time.monotonic() - start < 1500:
    phase = PHASE.read_text().strip()
    if phase == 'halt':
        break
    assert phase in ('idle','join','live','closing','wait','render'), phase
    print(json.dumps(sample(phase), separators=(',', ':')), flush=True)
    time.sleep(1)
else:
    raise SystemExit('benchmark sampler reached timeout')
