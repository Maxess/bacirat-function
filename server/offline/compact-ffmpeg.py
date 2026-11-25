#!/usr/bin/python3 -IB
"""Resource profile for the pinned r1 single-pipe H.264/AAC encoder."""
import os
import sys


def command(args):
    # Keep container, dimensions, segment/keyframe rules and bitrate caps. The
    # output encoder no longer buffers a long lookahead on a small VPS.
    options = list(args)
    if options.count('-threads') != 2 or options.count('-thread_queue_size') != 1 or '-tune' in options:
        raise ValueError('unexpected renderer encoder arguments')
    if options[options.index('-c:v') + 1] != 'libx264' or options[options.index('-f') + 1] != 'matroska':
        raise ValueError('unexpected renderer encoder format')
    for i, arg in enumerate(options):
        if arg == '-threads':
            options[i+1] = '1'
        elif arg == '-thread_queue_size':
            options[i+1] = '2'
    options[options.index('-pix_fmt'):options.index('-pix_fmt')] = ['-tune', 'zerolatency']
    return ['/usr/bin/ffmpeg', *options]


if __name__ == '__main__':
    argv = command(sys.argv[1:])
    os.execv(argv[0], argv)
