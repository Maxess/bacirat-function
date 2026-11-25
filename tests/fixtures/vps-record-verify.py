"""Decode the uploaded QA MP4 and prove it contains the exercise media."""
from array import array
import json
import math
from pathlib import Path
import subprocess
import sys


video = Path(sys.argv[1])
meta = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', video], timeout=15))
assert {t['codec_name'] for t in meta['streams']} == {'h264', 'aac'}
length = float(meta['format']['duration'])
assert 30 <= length <= 61, f'Unexpected capture length: {length:.2f}s'


def decode(*args):
    return subprocess.check_output(['ffmpeg', '-v', 'error', '-nostdin', '-i', video, *args, 'pipe:1'], timeout=35)


width, height = 160, 90
raw = decode('-an', '-vf', f'fps=1,scale={width}:{height}', '-pix_fmt', 'rgb24', '-f', 'rawvideo')
frames = list(range(0, len(raw), width * height * 3))
assert len(frames) >= 30

def colors(frame):
    return {tuple(raw[i:i+3]) for i in range(frame, frame+width*height*3, 3)}

# Gold and blue come from the test PDF, together with multiple different boards/video frames.
assert len({raw[i:i+width*height*3] for i in frames}) >= 10, 'movie has no usable moving picture'
assert any(sum(r > 165 and 85 < g < 200 and b < 70 for r, g, b in colors(i)) >= 5 for i in frames[5:]), 'PDF colors missing'
sound = array('h', decode('-vn', '-ac', '1', '-ar', '8000', '-f', 's16le'))
if sys.byteorder != 'little':
    sound.byteswap()
assert len(sound) >= 30 * 8000


def amplitude(freq, samples):
    step = 2 * math.pi * freq / 8000
    return 2 * math.hypot(sum(s*math.cos(i*step) for i, s in enumerate(samples)),
                          sum(s*math.sin(i*step) for i, s in enumerate(samples))) / len(samples)


# A sustained second anywhere in the middle must contain both active mics.
measured = max(min(amplitude(440, sound[i:i+8000]), amplitude(730, sound[i:i+8000]))
               for i in range(40000, min(len(sound)-8000, 120000), 8000))
assert measured > 100, f'both source tones must survive the audio mix, minimum amplitude={measured:.0f}'
print(f'QA_MP4 H.264+AAC duration_s={length:.2f} moving_video=yes PDF=yes two_mics=yes')
