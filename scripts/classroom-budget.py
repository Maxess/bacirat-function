#!/usr/bin/env python3
"""Estimate classroom wire traffic. This is sizing arithmetic, not a load test."""

import argparse
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Room:
    seats: int
    talkers: int = 1
    screen: int = 0  # concurrent remote receivers
    camera: int = 0  # concurrent remote receivers per active webcam
    webcams: int | None = None  # default: one webcam if this room has any webcam receivers

    @property
    def camera_publishers(self):
        return int(self.camera > 0) if self.webcams is None else self.webcams


def room_arg(text):
    try:
        parts = [int(value) for value in text.split(':')]
        room = Room(*parts)
        if (4 <= room.seats <= 60 and 0 <= room.talkers <= room.seats
            and 0 <= room.screen < room.seats and 0 <= room.camera < room.seats
            and int(room.camera > 0) <= room.camera_publishers <= room.seats):
            return room
    except (TypeError, ValueError):
        pass
    raise argparse.ArgumentTypeError('expected SEATS[:TALKERS[:SCREEN_VIEWERS[:VIEWERS_PER_WEBCAM[:WEBCAMS]]]] (4–60 seats)')


def traffic(rooms, speech=40.0, screen=90.0, camera=70.0, site=False, site_down=8.0, site_up=5.0):
    seats = sum(room.seats for room in rooms)
    tx = 4.0 * seats  # budgeted SFU per-seat control/signaling kbit/s (not a measurement)
    rx = 3.0 * seats
    copies = sum(room.talkers * (room.seats - 1) for room in rooms)
    tx += speech * copies + sum(screen * room.screen + camera * room.camera * room.camera_publishers for room in rooms)
    rx += sum(speech * room.talkers + screen * bool(room.screen) + camera * room.camera_publishers for room in rooms)
    if site:
        tx += site_down * seats
        rx += site_up * seats
    # A foreground listener can receive screen and webcams at once. This is a worst listener,
    # so within a room the counts overlap (but no one receives a network copy from themselves).
    client = max(speech * min(room.talkers, room.seats - 1) + screen * bool(room.screen)
                 + camera * min(room.camera_publishers, room.seats - 1) * bool(room.camera) for room in rooms)
    return seats, copies, tx / 1000, rx / 1000, client + 4 + site_down


def volume(mbps, minutes_daily, days):
    return mbps * minutes_daily * 60 * days / 8000  # decimal GB


def nonnegative(value):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError('expected a finite, nonnegative number')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--room', type=room_arg, action='append', metavar='SEATS[:T[:S[:C[:K]]]]',
                        help='per room: seats, talkers, screen viewers, viewers per webcam, webcams. Default: 3 rooms of 50, one talker each')
    parser.add_argument('--minutes-per-day', type=nonnegative, default=90)
    parser.add_argument('--days', type=int, default=8)
    parser.add_argument('--quota-gb', type=nonnegative, default=100, help='decimal GB')
    parser.add_argument('--extra-gb', type=nonnegative, default=0, help='other billed GB over the whole period (files, idle or other services)')
    parser.add_argument('--reserve-percent', type=nonnegative, default=20, help='leave this percentage of port and quota unused')
    parser.add_argument('--bill', choices=('egress', 'both'), default='both')
    parser.add_argument('--port-tx-mbps', type=nonnegative, help='known sustainable provider egress, not virtual NIC speed')
    parser.add_argument('--port-rx-mbps', type=nonnegative, help='known sustainable provider ingress')
    parser.add_argument('--site-on-vps', action='store_true', help='also count steady website/board HTTP on this VPS')
    parser.add_argument('--speech-kbps', type=nonnegative, default=40, help='wire budget, not Opus payload')
    parser.add_argument('--screen-kbps', type=nonnegative, default=90, help='wire budget for one light screen stream, not its encoder target')
    parser.add_argument('--camera-kbps', type=nonnegative, default=70, help='wire budget for one 144p camera stream, not its encoder target')
    parser.add_argument('--site-down-kbps', type=nonnegative, default=8, help='steady per-user HTTP budget (not assets/downloads)')
    parser.add_argument('--site-up-kbps', type=nonnegative, default=5)
    parser.add_argument('--client-kBps', dest='client_kbytes', type=nonnegative, default=50, help='decimal kilobytes/s downstream')
    a = parser.parse_args()
    if not (0 < a.minutes_per_day <= 1440 and a.days > 0 and a.quota_gb > 0 and a.reserve_percent < 100
            and a.client_kbytes > 0):
        parser.error('require 0 < minutes/day <= 1440, positive days/quota/client, 0 <= reserve < 100')
    rooms = a.room or [Room(50)] * 3
    seats, copies, tx, rx, client = traffic(rooms, a.speech_kbps, a.screen_kbps, a.camera_kbps,
                                            a.site_on_vps, a.site_down_kbps, a.site_up_kbps)
    usable = 1 - a.reserve_percent / 100
    used = volume(tx + (rx if a.bill == 'both' else 0), a.minutes_per_day, a.days) + a.extra_gb
    cap = a.quota_gb * usable
    average = max(0, cap - a.extra_gb) / volume(1, a.minutes_per_day, a.days)
    friendly = lambda value, limit: 'within-model-budget' if value <= limit else 'over-model-budget'
    print('ESTIMATE ONLY (no CPU, peak join or real network validation)')
    print('rooms=' + ','.join(f'{r.seats}:{r.talkers}:{r.screen}:{r.camera}:{r.camera_publishers}' for r in rooms)
          + f' seats_including_staff={seats} forwarded_audio_copies={copies} approx_audio_packets_per_s={copies*50}')
    print(f'tx_Mbps={tx:.3f} rx_Mbps={rx:.3f} site_on_vps={str(a.site_on_vps).lower()}')
    print(f'client_down_kbps={client:.1f} ({client/8:.1f} kB/s) limit={a.client_kbytes:.1f} kB/s '
          f'{friendly(client, a.client_kbytes * 8 * usable)}')
    print(f'{a.days}_days_{a.minutes_per_day:g}_min_per_day billed={a.bill} estimated_GB={used:.2f} '
          f'usable_GB={cap:.2f} max_avg_billed_Mbps={average:.3f} {friendly(used, cap)}')
    for direction, rate, limit in [('tx', tx, a.port_tx_mbps), ('rx', rx, a.port_rx_mbps)]:
        print(f'provider_{direction}=' + ('unknown' if limit is None else f'{limit:g} Mbps {friendly(rate, limit * usable)}'))


if __name__ == '__main__':
    main()
