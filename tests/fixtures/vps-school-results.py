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
    require(len(windows) >= 2 and all(.7 <= dt <= 4 for a,b,dt in windows), 'insufficient or discontinuous samples')
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
    units = {u: {
        'cpu_cores_mean': delta(lambda r:r['units'][u]['cpu_us'])/elapsed/1e6,
        'ram_mib_peak_sampled': max(r['units'][u]['mem'] for r in rows)/2**20,
        'tasks_peak_sampled': max(r['units'][u]['pids'] for r in rows),
        'oom_kills': delta(lambda r:r['units'][u]['oom_kill']),
    } for u in a['units']}
    return dict(vcpus=cpus, seconds=elapsed, intervals=len(windows), samples=len(rows), cpu_mean_pct=statistics.mean(cpu), cpu_p95_pct=percentile(cpu),
        peak_core_p95_pct=percentile(cores), steal_mean_pct=steal, ram_mib_visible=a['mem_total']/1024, ram_mib_used_peak=max(used),
        ram_mib_used_p95=percentile(used), ram_mib_available_min=min(r['mem_available'] for r in rows)/1024,
        swap_mib_peak=max(r['swap_kib'] for r in rows)/1024, tx_mbps=network('tx_bytes')*.000008, rx_mbps=network('rx_bytes')*.000008,
        tx_p95_mbps=percentile([(y['network']['tx_bytes']-x['network']['tx_bytes'])*.000008/dt for x,y,dt in windows]),
        tx_pps=network('tx_packets'), rx_pps=network('rx_packets'), nic_drops=delta(lambda r:r['network']['tx_dropped']+r['network']['rx_dropped']),
        udp_buffer_errors=delta(lambda r:r['udp'].get('RcvbufErrors',0)+r['udp'].get('SndbufErrors',0)),
        lo_tx_mbps=delta(lambda r:r['loopback']['tx_bytes'])*.000008/elapsed,
        oom_kills=delta(lambda r:r['oom']), recorder_source_bytes_per_second=max(0,delta(lambda r:r['spool_bytes']))/elapsed,
        recorder_source_peak_bytes=max(r['spool_bytes'] for r in rows), units=units)


def verify_film(file, expected_minimum, expected_mics):
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
    return dict(duration_s=duration, bytes=file.stat().st_size, tones_checked=expected_mics, tone_levels=peaks)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('workdir',type=Path)
    p.add_argument('output',type=Path)
    p.add_argument('count',type=int,choices=[0,3,9])
    p.add_argument('quality',choices=['low','medium'])
    p.add_argument('seconds',type=int)
    a = p.parse_args()
    work = a.workdir
    rows = [json.loads(line) for line in (work/'counters.jsonl').read_text().splitlines()]
    results = {phase:metric(phase_rows) for phase in ('idle','join','live','wait','render')
               if len(phase_rows := [r for r in rows if r['phase'] == phase]) >= 3}
    live = results['live']
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
    films = {}
    for name, record in status.items():
        require(re.fullmatch(r'QA-C0[1-9]',name) is not None, 'unexpected synthetic class')
        index = int(name[-2:])
        require(record['state'] == 'delivered' and record['cleaned'] and record['sent'] == record['total'] == 1
                and str(record['message']) == str(3200+index), 'school receipt did not settle')
        require(record['rendered_at'] >= record['not_before'], 'render before permission')
        films[name] = verify_film(work/f'received-{name}.mp4', a.seconds-2, 3 if index <= 7 else 2)
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
    print('QA_SCHOOL_FILMS', compact(films))
    bounds = dict(min_subscriber_payload_ratio=min(float(w['min_payload_ratio']) for w in media),
                  min_offer_pace=min(float(w['min_offer_pace']) for w in media),
                  **{key+'_median':statistics.median(float(w[key]) for w in media) for key in
                     ('audio_kbps','camera_kbps','screen_kbps','audio_source_pps')},
                  media_windows=len(media))
    print('QA_SCHOOL_OFFER', compact(bounds))
    a.output.write_text(json.dumps(dict(pupils=180,teachers=9,rooms=9,active_microphones=25,quality=a.quality,recordings=a.count,
        profile='full',test_only_raised_capture_limit=a.count == 9,phases=results,media=bounds,films=films,
        cpu_model=(work/'cpu-model.txt').read_text().strip()),indent=2)+'\n')


if __name__ == '__main__':
    main()
