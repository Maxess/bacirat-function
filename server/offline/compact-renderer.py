#!/usr/bin/env python3
"""Use the same private renderer and replay for a small classroom, without GPU compositing."""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

from postprocess import SOCKET, SPOOL, serve


async def main():
    if os.geteuid() == 0:
        raise ValueError('require an unprivileged renderer')
    os.umask(0o077)
    ffmpeg = Path(__file__).with_name('compact-ffmpeg.py')
    await serve(SimpleNamespace(socket=SOCKET, spool=SPOOL, ffmpeg=ffmpeg), launch={
        'headless': True,
        'chromium_sandbox': True,
        # Site isolation and the browser sandbox remain enabled. Replay is a
        # single origin, with PDF.js and all inputs routed by the host process.
        'args': ['--disable-gpu', '--disable-software-rasterizer', '--renderer-process-limit=1',
                 '--num-raster-threads=1', '--js-flags=--max-old-space-size=128 --max-semi-space-size=4',
                 '--autoplay-policy=no-user-gesture-required'],
    })


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as error:
        print(f'compact postprocess: unavailable ({type(error).__name__})', flush=True)
        raise SystemExit(1) from None
