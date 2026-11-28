"""Summarize only the phase-marked guest counter intervals; verify all receipts."""
import argparse
from array import array
import json
import math
from pathlib import Path
import re
import statistics
import subprocess


def require(ok, text):
    if not ok:
        raise ValueError(text)


def percentile(data, percent=.95):
    ordered = sorted(data)
    return ordered[max(0, math.ceil(len(ordered)*percent)-1)]


def busy(jiffies):
    # User, nice, system, IRQ and softIRQ. Don't count idle, iowait or steal.
    return sum(jiffies[i] for i in (0,1,2,5,6))


def metric(rows):
    windows = [(a,b,b['t']-a['t']) for a,b in zip(rows, rows[1:])]
    minimum = .18 if rows[0]['phase'] == 'join' else .7
    require(len(windows) >= 2 and all(minimum <= dt <= 4 for a,b,dt in windows), 'insufficient or discontinuous samples')
    a, b = rows[0], rows[-1]
    elapsed = b['t'] - a['t']
    delta = lambda get: get(b)-get(a)
    cpus = len(a['cpu'])-1
    cpu = [100*(busy(y['cpu']['cpu']) - busy(x['cpu']['cpu']))/(cpus*x['hz']*dt) for x,y,dt in windows]
    cores = [max(100*(busy(y['cpu'][core])-busy(x['cpu'][core]))/(x['hz']*dt)
                 for core in x['cpu'] if core != 'cpu') for x,y,dt in windows]
    steal = delta(lambda r:r['cpu']['cpu'][7])*100/(a['hz']*cpus*elapsed)
    used = [(r['mem_total']-r['mem_available'])/1024 for r in rows]
    network = lambda key: delta(lambda r:r['network'][key])/elapsed
    units = {}
    for u in a['units']:
        rates = [(y['units'][u]['cpu_us']-x['units'][u]['cpu_us'])/dt/1e6 for x,y,dt in windows]
        ram = [r['units'][u]['mem']/2**20 for r in rows]
        units[u] = dict(cpu_cores_mean=delta(lambda r:r['units'][u]['cpu_us'])/elapsed/1e6,
                        cpu_cores_p95=percentile(rates), cpu_cores_peak=max(rates),
                        ram_mib_peak_sampled=max(ram), ram_mib_p95=percentile(ram),
                        tasks_peak_sampled=max(r['units'][u]['pids'] for r in rows), oom_kills=delta(lambda r:r['units'][u]['oom_kill']))
    return dict(vcpus=cpus, seconds=elapsed, intervals=len(windows), samples=len(rows), cpu_mean_pct=statistics.mean(cpu), cpu_p95_pct=percentile(cpu),
        cpu_peak_pct=max(cpu), peak_core_p95_pct=percentile(cores), steal_mean_pct=steal, ram_mib_visible=a['mem_total']/1024, ram_mib_used_peak=max(used),
        ram_mib_used_p95=percentile(used), ram_mib_available_min=min(r['mem_available'] for r in rows)/1024,
        swap_mib_peak=max(r['swap_kib'] for r in rows)/1024, tx_mbps=network('tx_bytes')*.000008, rx_mbps=network('rx_bytes')*.000008,
        tx_p95_mbps=percentile([(y['network']['tx_bytes']-x['network']['tx_bytes'])*.000008/dt for x,y,dt in windows]),
        tx_pps=network('tx_packets'), rx_pps=network('rx_packets'), nic_drops=delta(lambda r:r['network']['tx_dropped']+r['network']['rx_dropped']),
        udp_receive_buffer_errors=delta(lambda r:r['udp'].get('RcvbufErrors',0)),
        udp_send_buffer_errors=delta(lambda r:r['udp'].get('SndbufErrors',0)),
        udp_buffer_errors=delta(lambda r:r['udp'].get('RcvbufErrors',0)+r['udp'].get('SndbufErrors',0)),
        lo_tx_mbps=delta(lambda r:r['loopback']['tx_bytes'])*.000008/elapsed,
        oom_kills=delta(lambda r:r['oom']), recorder_source_bytes_per_second=max(0,delta(lambda r:r['source_bytes']))/elapsed,
        min_live_sources=min(len(r['open_sources']) for r in rows), peak_live_sources=max(len(r['open_sources']) for r in rows),
        recorder_source_peak_bytes=max(r['source_bytes'] for r in rows), recorder_spool_peak_bytes=max(r['spool_bytes'] for r in rows), units=units)


def verify_film(file, expected_minimum, room):
    expected_mics = 3 if room <= 7 else 2
    ffprobe = subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(file)])
    probe = json.loads(ffprobe)
    streams = {s['codec_type']:s for s in probe['streams']}
    require(set(streams) == {'video','audio'} and len(probe['streams']) == 2, 'video and audio required')
    v, a = streams['video'], streams['audio']
    require((v['codec_name'],v['width'],v['height'],a['codec_name'],a['channels']) == ('h264',1280,720,'aac',1), 'unexpected packaged output')
    duration = float(probe['format']['duration'])
    require(expected_minimum <= duration < expected_minimum+60, f'unexpected film length {duration:.2f}')
    subprocess.run(['ffmpeg','-v','error','-xerror','-nostdin','-threads','2','-i',str(file),'-f','null','-'],check=True)
    # Take several off-boundary snippets. Every expected test microphone must be audible.
    peaks = []
    for skip in (7, int(duration/2), int(duration)-8):
        out = subprocess.check_output(['ffmpeg','-v','error','-nostdin','-ss',str(skip),'-i',str(file),'-t','1.0',
                                       '-vn','-ac','1','-ar','8000','-f','s16le','-'])
        pcm = array('h',out)
        require(len(pcm) >= 7200, 'missing audio region')
        levels = []
        for hz in (440,730,1030):
            step = 2*math.pi*hz/8000
            levels.append(round(2*abs(sum(v*complex(math.cos(i*step),math.sin(i*step)) for i,v in enumerate(pcm)))/len(pcm),1))
        require(all(value >= 15 for value in levels[:expected_mics]), f'missing synthetic voice at {skip}s: {levels}')
        peaks.append(levels)
    width, height = 160, 90
    raw = subprocess.check_output(['ffmpeg','-v','error','-xerror','-nostdin','-threads','2','-ss','5','-i',str(file),
                                   '-t',str(int(duration)-10),'-an','-vf',f'fps=1,scale={width}:{height}',
                                   '-pix_fmt','rgb24','-f','rawvideo','-'],timeout=45)
    size = width*height*3
    frames = [raw[i:i+size] for i in range(0,len(raw),size)]
    need_frames = max(8,len(frames)//2)
    markers = []
    for frame in frames:
        gold = blue = ink = camera = screen = 0
        for r,g,b in zip(frame[::3],frame[1::3],frame[2::3]):
            gold += r > 170 and 105 < g < 198 and b < 70
            blue += r < 75 and 24 < g < 95 and b > 132
            ink += r > 154 and g < 90 and 50 < b < 135
            camera += r > 144 and g < 85 and b >= 148
            screen += r < 112 and g > 148 and b < 95
        markers.append((gold > 120 and blue > 100 and ink >= 10,camera >= 12,screen >= 12))
    proof = 'pdf_and_ink' if room <= 7 else ('camera' if room == 8 else 'screen')
    seen = sum(row[0 if room <= 7 else room-7] for row in markers)
    require(seen >= need_frames, f'expected {proof} in {file.name}: {seen}/{len(frames)} sampled frames')
    if room > 7:
        moving = sum(sum(abs(a-b) for a,b in zip(u,v)) > 2*size for u,v in zip(frames,frames[1:]))
        require(moving >= 4, f'{proof} is static in {file.name}')
    return dict(duration_s=duration, bytes=file.stat().st_size, tones_checked=expected_mics, tone_levels=peaks,
                checked_visual=proof, marker_frames=seen, sampled_frames=len(frames))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('workdir',type=Path)
    p.add_argument('output',type=Path)
    p.add_argument('count',type=int,choices=[0,3,9])
    p.add_argument('quality',choices=['low','medium','high'])
    p.add_argument('seconds',type=int)
    p.add_argument('trial',nargs='?',default='standard',choices=['standard','baseline','udp-batch','candidate'])
    p.add_argument('--bell', type=int, choices=[1,2], default=1)
    a = p.parse_args()
    work = a.workdir
    rows = [json.loads(line) for line in (work/'counters.jsonl').read_text().splitlines()]
    results = {phase:metric(phase_rows) for phase in ('idle','join','warmup','live','reconnect','wait','render')
               if len(phase_rows := [r for r in rows if r['phase'] == phase]) >= 3}
    live = results['live']
    live_rows = [r for r in rows if r['phase'] == 'live']
    board = [json.loads(row) for row in (work/'board.jsonl').read_text().splitlines()]
    require(len(board) >= (a.seconds-6)//2 and all(t['readers'] == 7 and t['tick'] == i for i,t in enumerate(board,1)),
            'all seven PDF whiteboards must receive the synthetic updates')
    offset = (a.bell-1)*16
    rooms = range(1,10) if a.count == 9 else [1,8,9] if a.count == 3 else []
    expected = {str(offset+i) for i in rooms}
    require(all(set(r['open_sources']) == expected for r in live_rows), 'expected independent active source per approved class throughout window')
    observed = {key:live_rows[-1]['open_sources'][key]-live_rows[0]['open_sources'][key] for key in expected}
    require(all(size > 5000*live['seconds'] for size in observed.values()), 'one or more live sources did not grow through the measurement')
    require(live['seconds'] >= a.seconds-3 and live['vcpus'] == 2, 'expected steady interval on 2 vCPUs')
    require(live['oom_kills'] == live['swap_mib_peak'] == 0 and all(not u['oom_kills'] for u in live['units'].values()), 'guest memory failure')
    require(10 < live['tx_mbps'] < 80 and 0 < live['rx_mbps'] < 20 and live['tx_pps'] > 23000, 'guest did not forward the expected workload')
    require((live['recorder_source_bytes_per_second'] > 0) == (a.count > 0), 'source growth must match capture status')
    status = json.loads((work/'closed.json').read_text())
    require(len(status) == a.count, 'unexpected number of school receipts')
    require(len(list(work.glob('received-*.mp4'))) == a.count, 'missing or excess uploads')
    peer = (work/'load.log').read_text()
    media = [dict(re.findall(r'(\w+)=([.\d]+)', line)) for line in peer.splitlines() if line.startswith('QA_SCHOOL_MEDIA ')]
    require(len(media) >= 6, 'too few valid offered and received media windows')
    if a.quality == 'high':
        require(statistics.median(float(w['camera_kbps']) for w in media) >= 310
                and statistics.median(float(w['screen_kbps']) for w in media) >= 435, 'high-profile source was under-driven')
    films = {}
    for name, record in status.items():
        require(re.fullmatch(r'QA-C0[1-9]',name) is not None, 'unexpected synthetic class')
        index = int(name[-2:])
        require(record['state'] == 'delivered' and record['cleaned'] and record['sent'] == record['total'] == 1
                and str(record['message']) == str(3200+index+100*(a.bell-1)), 'school receipt did not settle')
        require(record['rendered_at'] >= record['not_before'], 'render before permission')
        print(f'QA_SCHOOL_RECEIPT verifying {name}: {record["state"]}',flush=True)
        films[name] = verify_film(work/f'received-{name}.mp4', a.seconds-2, index)
        films[name]['rendered_after_eligible_s'] = record['rendered_at'] - record['not_before']
    compact = lambda val: json.dumps(val,ensure_ascii=True,separators=(',',':'))
    for phase,m in results.items():
        print(f"QA_SCHOOL_METRICS phase={phase} captures={a.count} quality={a.quality} vcpus={m['vcpus']} seconds={m['seconds']:.1f} "
              f"cpu_mean={m['cpu_mean_pct']:.1f}% cpu_p95={m['cpu_p95_pct']:.1f}% hot_core_p95={m['peak_core_p95_pct']:.1f}% "
              f"steal_mean={m['steal_mean_pct']:.1f}% ram_peak_mib={m['ram_mib_used_peak']:.1f} recorder_ram_mib={m['units']['recorder']['ram_mib_peak_sampled']:.1f} "
              f"sfu_cpu_cores={m['units']['sfu']['cpu_cores_mean']:.3f} recorder_cpu_cores={m['units']['recorder']['cpu_cores_mean']:.3f} "
              f"renderer_ram_mib={m['units']['postprocess']['ram_mib_peak_sampled']:.1f} "
              f"tx={m['tx_mbps']:.3f}Mbps tx_p95={m['tx_p95_mbps']:.3f}Mbps rx={m['rx_mbps']:.3f}Mbps tx_pps={m['tx_pps']:.0f} "
              f"lo={m['lo_tx_mbps']:.3f}Mbps drops={m['nic_drops']} udp_buffer_errors={m['udp_buffer_errors']} source_Bps={m['recorder_source_bytes_per_second']:.1f}")
    print('QA_SCHOOL_FILMS', compact({name:{key:value for key,value in info.items() if key != 'tone_levels'} for name,info in films.items()}))
    overview = dict(cpu_model=(work/'cpu-model.txt').read_text().strip(), active_whiteboards=7, strokes_per_board=len(board),
                    max_board_delta_json_bytes=max(t['max_delta_json_bytes'] for t in board), capture_bytes_by_room=observed,
                    join=results.get('join'), live=live, render=results.get('render'))
    print('QA_SCHOOL_RESULT', compact(overview))
    bounds = dict(min_subscriber_payload_ratio=min(float(w['min_payload_ratio']) for w in media),
                  min_offer_pace=min(float(w['min_offer_pace']) for w in media),
                  **{key+'_median':statistics.median(float(w[key]) for w in media) for key in
                     ('audio_kbps','camera_kbps','screen_kbps','audio_source_pps')},
                  median_late_audio_gap_pct=statistics.median(float(w['late_audio_gap_pct']) for w in media),
                  max_late_audio_gap_pct=max(float(w['late_audio_gap_pct']) for w in media),
                  worst_receiver_late_audio_gap_pct=max(float(w['worst_audio_gap_pct']) for w in media), media_windows=len(media))
    print('QA_SCHOOL_OFFER', compact(bounds))
    probe = work/'udp-probe.json'
    measured = {'audio_probe':json.loads(probe.read_text())} if probe.exists() else {}
    a.output.write_text(json.dumps(dict(pupils=180,teachers=9,rooms=9,active_microphones=25,quality=a.quality,recordings=a.count,variant=a.trial,
        profile='full',test_only_raised_capture_limit=a.count == 9 and a.trial != 'candidate',phases=results,media=bounds,films=films,
        **measured, **{k:overview[k] for k in ('cpu_model','active_whiteboards','strokes_per_board','max_board_delta_json_bytes','capture_bytes_by_room')}),indent=2)+'\n')


if __name__ == '__main__':
    main()
