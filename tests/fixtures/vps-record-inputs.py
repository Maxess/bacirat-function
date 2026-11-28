"""Build only the synthetic test patterns; FFmpeg runs outside the 512 MiB VM."""
from pathlib import Path
import struct
import subprocess
import sys


def ffmpeg(*args):
    subprocess.run(['ffmpeg', '-hide_banner', '-v', 'error', '-nostdin', *args], check=True)


p = Path(sys.argv[1])
profile = sys.argv[2] if len(sys.argv) == 3 else 'rehearsal'
quality = {
    'rehearsal': [('640x360', 10, '260k'), ('1280x720', 10, '420k')],
    'low': [('128x128', 8, '25k'), ('640x360', 5, '70k')],
    'medium': [('256x256', 12, '150k'), ('960x540', 10, '315k')],
    'high': [('480x480', 18, '350k'), ('1280x720', 15, '500k')],
}[profile]
for (name, source, color), (size, fps, rate) in zip(
        [('camera', 'testsrc2', '0xe000ce'), ('screen', 'testsrc', '0x25dc30')], quality, strict=True):
    bounded = [] if profile == 'rehearsal' else ['-maxrate', rate, '-bufsize', f'{2*int(rate[:-1])}k']
    if profile == 'high':
        # A moving source fills most of the high preset's budget. The ordinary
        # screen pattern is too easy to compress to stress this experiment.
        source = 'testsrc2'
        bounded = ['-minrate', rate, '-maxrate', rate, '-bufsize', rate]
    ffmpeg('-f', 'lavfi', '-i', f'{source}=size={size}:rate={fps}', '-t', '3', '-an',
           '-vf', f'drawbox=x=3*iw/8:y=3*ih/8:w=iw/4:h=ih/4:color={color}:t=fill', '-c:v', 'libvpx',
           '-pix_fmt', 'yuv420p', '-deadline', 'realtime', '-cpu-used', '6', '-threads', '2', '-g', str(fps) if profile == 'high' else '15',
           '-b:v', rate, *bounded, '-f', 'ivf', str(p / f'{name}.ivf'))
hz_by_source = [('teacher', 440), ('pupil', 730)]
if profile != 'rehearsal':
    hz_by_source += [('pupil2', 1030)]
for name, hz in hz_by_source:
    ogg = p / f'{name}.ogg'
    opus = ['-b:a', '24k'] if profile == 'rehearsal' else ['-b:a', '12k', '-vbr', 'off', '-application', 'voip']
    ffmpeg('-f', 'lavfi', '-i', f'sine=frequency={hz}:sample_rate=48000', '-t', '2', '-vn',
           '-c:a', 'libopus', *opus, '-frame_duration', '20', str(ogg))
    raw = ogg.read_bytes()
    pos, packet, packets = 0, b'', []
    while pos < len(raw):
        assert raw[pos:pos+4] == b'OggS'
        n = raw[pos+26]
        sizes = raw[pos+27:pos+27+n]
        pos += 27+n
        for length in sizes:
            packet += raw[pos:pos+length]
            pos += length
            if length < 255:
                if not packet.startswith((b'OpusHead', b'OpusTags')):
                    packets.append(packet)
                packet = b''
    assert 95 <= len(packets) <= 101 and not packet
    (p / f'{name}.opus').write_bytes(b''.join(struct.pack('>H', len(packet)) + packet for packet in packets))
# A colored document with a real xref, safe to render with the bundled PDF.js.
paint = b'0.9 0.65 0.1 rg 0 0 480 300 re f 0.1 0.2 0.7 rg 100 80 280 140 re f\n'
items = [b'<</Type /Catalog /Pages 2 0 R>>', b'<</Type /Pages /Kids [3 0 R] /Count 1>>',
         b'<</Type /Page /Parent 2 0 R /MediaBox [0 0 480 300] /Contents 4 0 R /Resources <<>>>>',
         b'<</Length ' + str(len(paint)).encode() + b'>>\nstream\n' + paint + b'endstream']
pdf, offsets = b'%PDF-1.4\n', [0]
for i, item in enumerate(items, 1):
    offsets.append(len(pdf))
    pdf += f'{i} 0 obj\n'.encode() + item + b'\nendobj\n'
(p / 'board.pdf').write_bytes(pdf + b'xref\n0 5\n0000000000 65535 f \n' +
    b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:]) +
    f'trailer\n<</Size 5 /Root 1 0 R>>\nstartxref\n{len(pdf)}\n%%EOF\n'.encode())
