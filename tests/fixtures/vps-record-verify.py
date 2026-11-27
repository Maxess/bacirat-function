"""Decode the uploaded QA MP4 and prove it contains the exercise media."""
from array import array
import json
import math
from pathlib import Path
import subprocess
import sys


video = Path(sys.argv[1])
meta = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', video], timeout=15))
assert sorted(t['codec_name'] for t in meta['streams']) == ['aac', 'h264']
track = next(t for t in meta['streams'] if t['codec_name'] == 'h264')
assert (track['width'], track['height']) == (1280, 720)
length = float(meta['format']['duration'])
assert 30 <= length <= 61, f'Unexpected capture length: {length:.2f}s'


def decode(*args):
    return subprocess.check_output(['ffmpeg', '-v', 'error', '-nostdin', '-i', video, *args, 'pipe:1'], timeout=35)


width, height = 160, 90
raw = decode('-an', '-vf', f'fps=1,scale={width}:{height}', '-pix_fmt', 'rgb24', '-f', 'rawvideo')
frames = list(range(0, len(raw), width * height * 3))
assert len(frames) >= 30

def pane(frame, x, y, w, h):
    return b''.join(raw[p:p+3*w] for p in (frame+(line*width+x)*3 for line in range(y,y+h)))


# Sample the right content pane, not the labels or the test camera's
# multicolored pixels on the left. Require both large PDF color fields.
def pdf_visible(frame):
    rgb = pane(frame, 81, 15, 75, 68)
    gold = blue = 0
    for r, g, b in zip(rgb[0::3], rgb[1::3], rgb[2::3]):
        gold += r > 170 and 105 < g < 198 and b < 70
        blue += r < 75 and 24 < g < 95 and b > 132
    return gold > 420 and blue > 260


visible = [index for index in frames[5:] if pdf_visible(index)]
assert len(visible) >= 8, 'two-color PDF missing from the right panel'
def moving(samples, x):
    # Detect changes in picture content, not timestamps, labels or H.264 noise.
    pixels = [pane(i, x, 27, 70, 43) for i in samples]
    return sum(sum(abs(a-b) for a,b in zip(u,v)) > 2.0*len(u) for u,v in zip(pixels,pixels[1:]))


if sys.argv[2:] == ['1']:
    def marked(frame, x, y, test):
        rgb = pane(frame, x - 4, y - 3, 9, 7)
        return sum(test(r,g,b) for r,g,b in zip(rgb[0::3],rgb[1::3],rgb[2::3])) >= 45

    def green(r,g,b):
        return r < 80 and g > 158 and b < 80

    def pink(r,g,b):
        return r > 165 and g < 72 and b > 125

    focused = [index for index in frames if marked(index,41,50,green) and marked(index,119,50,pink)]
    assert len(focused) >= 5, 'no sustained large selected camera beside the preferred screen'
    # With the camera back in the gallery, the center of the two main panes moves up.
    restored = sum(frame > focused[-1] and marked(frame,41,39,green) for frame in visible)
    assert restored >= 5, f'board did not return beside the preferred screen ({restored} samples)'
    assert moving(focused,83) >= 3, 'selected webcam is static'
    print('QA_CAMERA uploaded MP4: preferred screen retained, selected webcam replaced the board, board restored after unpin.')
else:
    assert moving(visible,5) >= 5, 'left video pane is static'

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
