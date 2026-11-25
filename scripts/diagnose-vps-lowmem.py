"""Repackage a verified baseline *only* for an isolated synthetic VM diagnosis.

Nothing produced by this script is a release candidate. Compare the normal
renderer and a software-only browser, with the sandbox enabled in both cases.
"""
from pathlib import Path
import sys
from zipfile import ZipFile


def replace(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


bundle = Path(sys.argv[1])
base = bundle / 'setup.py'
patch = bundle / 'payload/baci-classroom-renderer-v0.4.23-r1.zip'
with ZipFile(patch) as archive:
    files = {name: archive.read(name) for name in archive.namelist()}
src = files['postprocess.py'].decode()
src = replace(src, '                try:\n                    await asyncio.wait_for(self.self_test(), 18)', '''                try:
                    began = time.monotonic()
                    await asyncio.wait_for(self.self_test(), 36)
                    print(f'QA_BROWSER success in {time.monotonic()-began:.2f}s', flush=True)''')
src = replace(src, "                    print(f'postprocess: startup self-test failed ({type(error).__name__})', flush=True)", '''                    print(f'QA_BROWSER {type(error).__name__} after {time.monotonic()-began:.2f}s', flush=True)
                    for line in str(error).splitlines():
                        if len(line) < 650 and any(word in line.lower() for word in ('signal', 'fatal', 'error:', 'exitcode=', 'timeout', 'crash')):
                            print('QA_DIAG ' + line, flush=True)''')
src = replace(src, 'timeout=7000)', 'timeout=24000)')
src = replace(src, "        self.stopping = asyncio.Event()", '''        if os.getenv('BACI_QA_SOFTWARE'):
            self.launch['args'] += ['--disable-gpu', '--disable-software-rasterizer', '--renderer-process-limit=1']
        self.stopping = asyncio.Event()''')
files['postprocess.py'] = src.encode()
with ZipFile(patch, 'w') as out:
    for name, data in files.items():
        out.writestr(name, data)
import hashlib
text = replace(base.read_text(), 'FULL_RAM_KIB = 1792 * 1024', 'FULL_RAM_KIB = 384 * 1024')
text = replace(text, "07d51104f5fa7bbfd90d6329931fa0e9d03967b9c8fc617c8fe08f10b4d38cfd", hashlib.sha256(patch.read_bytes()).hexdigest())
text = replace(text, 'conn.settimeout(22 if test else 3)', 'conn.settimeout(41 if test else 3)')
start = text.index('        if self.full:\n            probe_renderer(test=True)')
stop = text.index("        print('۴/۴", start)
# Self-tests are safe on the isolated empty guest. Restart only the renderer
# between probes, preserving the failed outcome in the test log.
text = text[:start] + '''        for label in ('normal', 'software'):
            if label == 'software':
                diagnostic = SYSTEMD / (RENDERER + '.d/99-ci-debug.conf')
                write(diagnostic, '[Service]\\nEnvironment=BACI_QA_SOFTWARE=1\\nEnvironment=XDG_CACHE_HOME=/tmp/baci-cache\\n')
                run('systemctl', 'daemon-reload')
                run('systemctl', 'restart', RENDERER)
            began = time.monotonic()
            try:
                probe_renderer(test=True)
                passed = True
            except (Refused, OSError, ValueError):
                passed = False
            after = Path('/sys/fs/cgroup/system.slice') / RENDERER
            stat_line = ' '.join((after/f).read_text().strip().replace('\\n', ',') for f in ('memory.peak', 'memory.events'))
            print(f'QA_MEMORY {label} ok={passed} seconds={time.monotonic()-began:.2f} peak_bytes,events={stat_line}; '
                  + re.search(r'^MemAvailable:.*', Path('/proc/meminfo').read_text(), re.M)[0], flush=True)
            run('journalctl','-n','10','-u',RENDERER,'--no-pager','-o','cat', show=True)
        need(passed, 'The sandboxed software renderer also failed.')
''' + text[stop:]
compile(text, str(base), 'exec')
base.write_text(text)
fixture = Path('scripts/.vps-lowmem-probe-ci.sh')
text = Path('scripts/test-vps-offline-vm.sh').read_text().replace('cpus=1; profile=live', 'cpus=1; profile=full')
fixture.write_text(text)
