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
UDP_ERRORS = ('InErrors', 'RcvbufErrors', 'SndbufErrors', 'NoPorts')
TUNABLES = ('rmem_default', 'rmem_max', 'wmem_default', 'wmem_max', 'netdev_max_backlog')


def command(*args, unavailable=''):
    try:
        return subprocess.check_output(args, timeout=2, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        return unavailable


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


def udp4_fields(text):
    rows = [line.split()[1:] for line in text.splitlines() if line.startswith('Udp:')]
    if len(rows) != 2 or len(rows[0]) != len(rows[1]):
        return {}
    return {key: int(value) for key, value in zip(*rows) if key in UDP_ERRORS and value.isascii() and value.isdigit()}


def softnet_fields(text):
    # Linux softnet_stat's first three columns are processed, dropped, time_squeeze.
    # Sum per-CPU kernel backlog counters, not individual LiveKit sockets.
    rows = [line.split() for line in text.splitlines() if line.strip()]
    if not rows or any(len(row) < 3 for row in rows):
        return {}
    try:
        return {key: sum(int(row[col], 16) for row in rows) for col, key in ((1,'drop'), (2,'squeeze'))}
    except ValueError:
        return {}


def listener_scope(listeners, protocol, port):
    if listeners is None:
        return 'unknown'
    scopes = set()
    for line in listeners.splitlines():
        row = line.split()
        if len(row) < 6 or row[0] != protocol:
            continue
        address, _, local_port = row[4].rpartition(':')
        if local_port != str(port):
            continue
        address = address.strip('[]').split('%', 1)[0]
        scopes.add('wildcard' if address in ('*', '0.0.0.0', '::') else
                   'loopback-only' if address == '::1' or address.startswith(('127.', '::ffff:127.')) else 'non-loopback')
    return next((kind for kind in ('wildcard', 'non-loopback', 'loopback-only') if kind in scopes), 'absent')


def net_counters():
    def proc(name):
        try:
            return (Path('/proc/net') / name).read_text()
        except OSError:
            return ''
    udp4 = udp4_fields(proc('snmp'))
    udp6 = {key[4:]: int(value) for key, value in (line.split() for line in proc('snmp6').splitlines() if len(line.split()) == 2)
            if key.startswith('Udp6') and key[4:] in UDP_ERRORS and value.isascii() and value.isdigit()}
    softnet = softnet_fields(proc('softnet_stat'))
    return ({f'{family}_{key}': fields.get(key) for family, fields in [('UDP4', udp4), ('UDP6', udp6)] for key in UDP_ERRORS}
            | {f'softnet_{key}': softnet.get(key) for key in ('drop', 'squeeze')})


def counter_delta(old, new):
    return new - old if old is not None and new is not None and new >= old else 'na'


def snapshot(device):
    row = tuple(int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:9])
    return time.monotonic(), [read_number(device / 'statistics' / key) for key in COUNTERS], row, net_counters()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--interface', help='interface to sample; default is interface of the default route')
    p.add_argument('--interval', type=int, default=5, help='seconds per sample (1–30)')
    p.add_argument('--samples', type=int, default=12, help='number of samples (1–180)')
    p.add_argument('--udp-mux-port', type=int, default=7882, help='expected local UDP media listener (default: 7882)')
    args = p.parse_args()
    if not 1 <= args.udp_mux_port <= 65535:
        p.error('UDP mux port must be 1–65535')
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
    sockets = command('ss', '-H', '-lntu', unavailable=None)
    ports = [('udp',args.udp_mux_port), ('tcp',7881), ('tcp',7880), ('tcp',7883), ('udp',3478), ('udp',443)]
    print('local_listeners ' + ' '.join(f'{proto}_{port}={listener_scope(sockets, proto, port)}' for proto,port in dict.fromkeys(ports)), flush=True)
    core = Path('/proc/sys/net/core')
    print('kernel_limits ' + ' '.join(f'{key}={value if (value := read_number(core / key)) is not None else "na"}' for key in TUNABLES), flush=True)
    print('Local binds and host-wide kernel counters do NOT confirm the firewall, advertised ICE IP or client UDP path.', flush=True)
    before, totals, ticks, net = snapshot(dev)
    sum_bytes = [0, 0]
    complete = True
    for _ in range(args.samples):
        time.sleep(args.interval)
        now, counters, after, net_after = snapshot(dev)
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
        delta = ' '.join(f'{key}={counter_delta(net[key], value)}' for key,value in net_after.items())
        print('kernel_delta ' + delta, flush=True)
        before, totals, ticks, net = now, counters, after, net_after
    if complete:
        print(f'sampled_RX_MB={sum_bytes[0]/1e6:.3f} sampled_TX_MB={sum_bytes[1]/1e6:.3f}')


if __name__ == '__main__':
    main()
