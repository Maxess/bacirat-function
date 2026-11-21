#!/usr/bin/env python3
"""Read-only Linux VPS report during a pilot bell. No test traffic or endpoint changes."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import time

NET = Path('/sys/class/net')
COUNTERS = ('rx_bytes', 'tx_bytes', 'rx_packets', 'tx_packets', 'rx_dropped', 'tx_dropped', 'rx_errors', 'tx_errors')


def command(*args):
    try:
        return subprocess.check_output(args, timeout=2, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def nic():
    # These are kernel route lookups only; they do not send packets.
    for family, address in [('-4', '1.1.1.1'), ('-6', '2606:4700:4700::1111')]:
        match = re.search(r'\bdev (\S+)', command('ip', family, 'route', 'get', address))
        if match:
            return match[1]
    return ''


def read_number(path):
    try:
        return int(path.read_text().strip())
    except (ValueError, OSError):
        return None


def proc_fields(path, keys):
    result = {}
    try:
        for line in path.read_text().splitlines():
            key, sep, value = line.partition(':')
            if sep and key in keys:
                result[key] = int(value.split()[0])
    except (ValueError, OSError):
        pass
    return result


def snapshot(device):
    row = tuple(int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:9])
    return time.monotonic(), [read_number(device / 'statistics' / key) for key in COUNTERS], row


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--interface', help='interface to sample; default is interface of the default route')
    p.add_argument('--interval', type=int, default=5, help='seconds per sample (1–30)')
    p.add_argument('--samples', type=int, default=12, help='number of samples (1–180)')
    args = p.parse_args()
    name = args.interface or nic()
    if not re.fullmatch(r'[A-Za-z0-9_:.@-]{1,32}', name) or name in {'.', '..'} or not (NET / name / 'statistics').is_dir():
        p.error('cannot determine interface; specify --interface without an address')
    if not (1 <= args.interval <= 30 and 1 <= args.samples <= 180):
        p.error('interval must be 1–30 s and samples 1–180')
    dev = NET / name
    speed = read_number(dev / 'speed')
    total_ram = proc_fields(Path('/proc/meminfo'), {'MemTotal'}).get('MemTotal')
    cpu = len(os.sched_getaffinity(0))
    print(f'interface={name} nic_nominal_Mbps={speed if speed and speed > 0 else "unknown"} '
          f'vCPU={cpu} ram_MiB={total_ram // 1024 if total_ram else "unknown"}', flush=True)
    print('Passive samples. NIC nominal speed and observed traffic are NOT the provider limit.', flush=True)
    before, totals, ticks = snapshot(dev)
    sum_bytes = [0, 0]
    complete = True
    for _ in range(args.samples):
        time.sleep(args.interval)
        now, counters, after = snapshot(dev)
        rates = [y-x if x is not None and y is not None and y >= x else None for x, y in zip(totals, counters)]
        duration = now - before
        for i in range(2):
            if rates[i] is None:
                complete = False
            else:
                sum_bytes[i] += rates[i]
        periods = [max(0, y-x) for x, y in zip(ticks, after)]
        period = max(1, sum(periods))
        steal = periods[7] * 100 / period
        busy = (period - periods[3] - periods[4] - periods[7]) * 100 / period
        mem = proc_fields(Path('/proc/meminfo'), {'MemAvailable'}).get('MemAvailable')
        pid = command('systemctl', 'show', '--property=MainPID', '--value', 'school-classroom-sfu.service')
        sfu = proc_fields(Path('/proc') / pid / 'status', {'VmRSS'}).get('VmRSS') if pid.isascii() and pid.isdigit() and pid != '0' else None
        stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        display = lambda num, scale: 'na' if num is None else f'{num / scale:.2f}'
        print(f'{stamp} dt={duration:.2f}s '
              f'RX_Mbps={display(rates[0],duration*125000)} TX_Mbps={display(rates[1],duration*125000)} '
              f'RX_pps={display(rates[2],duration)} TX_pps={display(rates[3],duration)} '
              f'RX_drop={rates[4]} TX_drop={rates[5]} RX_err={rates[6]} TX_err={rates[7]} '
              f'cpu_busy_pct={busy:.1f} cpu_steal_pct={steal:.1f} ram_available_MiB={display(mem,1024)} '
              f'sfu_rss_MiB={display(sfu,1024)}', flush=True)
        before, totals, ticks = now, counters, after
    if complete:
        print(f'sampled_RX_MB={sum_bytes[0]/1e6:.3f} sampled_TX_MB={sum_bytes[1]/1e6:.3f}')


if __name__ == '__main__':
    main()
