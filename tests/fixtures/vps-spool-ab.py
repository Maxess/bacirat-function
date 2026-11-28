"""Compare exact pinned r4 and candidate scans on an identical private spool."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'server'/'recorder'))
from archive import Budget  # noqa: E402


def bench(work, files):
    spec = importlib.util.spec_from_file_location('r4_archive', work/'baseline.py')
    r4 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(r4)
    spool = work/'spool'
    spool.mkdir()
    for i in range(files):
        path = spool/f'{i:032x}'
        (path/'documents').mkdir(parents=True)
        (path/'.render').mkdir()
        for name, n in (('meta.json',512),('source-000.baci',9_000_000),('source-000.idx',320),
                        ('documents/board.pdf',44_000),('.render/scratch.mp4',750_000),('receipt.json',220)):
            with (path/name).open('wb') as f:
                f.truncate(n)
    samples = [[], []]
    variants = (r4.Budget(spool), Budget(spool))
    variants[0].measure()
    variants[1].measure()
    assert [(b.total, b.rendered, b.per_job) for b in variants][0] == (variants[1].total, variants[1].rendered, variants[1].per_job)
    repeats = 60 if files < 100 else 25
    for i in range(repeats):
        for arm in (i%2,(i+1)%2):
            b = variants[arm]
            b.measured = -float('inf')
            start = time.perf_counter_ns()
            b.measure()
            samples[arm].append((time.perf_counter_ns()-start)/1e6)
    return {name:{'p50_ms':round(statistics.median(s),3), 'p95_ms':round(statistics.quantiles(s,n=20)[18],3)}
            for name,s in zip(('r4','candidate'),samples,strict=True)} | {'dirs':files,'regular_files':6*files}


def main():
    base = ROOT/'artifacts'/'baci-classroom-recorder-v0.4.23-r4.zip'
    assert sha256(base.read_bytes()).hexdigest() == '08da383d01c2d96d596e7d0df9e9bc9d751cf5edc8d8d09f020790c8258fa1bc'
    count = int(sys.argv[1])
    if count not in (18,256):
        raise ValueError('only bounded synthetic inventory workloads are supported')
    with tempfile.TemporaryDirectory() as temp, ZipFile(base) as r4:
        top = Path(temp)
        (top/'baseline.py').write_bytes(r4.read('archive.py'))
        print(json.dumps(bench(top,count)))


if __name__ == '__main__':
    main()
