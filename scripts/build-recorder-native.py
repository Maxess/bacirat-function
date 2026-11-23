#!/usr/bin/env python3
"""Build and verify the Linux/amd64 native Recorder from the pinned Go modules."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from contextlib import ExitStack


SOURCE = Path(__file__).resolve().parents[1] / 'server' / 'recorder' / 'native'
GO_VERSION = 'go1.26.2'
BINARY = 'baci-native-receiver'


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('output', type=Path, help='new/empty output directory, outside the source tree')
    cli.add_argument('--module-proxy', type=Path, help='use a local Go module mirror with the unchanged go.sum')
    args = cli.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(SOURCE) or (output.exists() and any(output.iterdir())):
        cli.error('Use a new or empty directory outside the source tree')
    env = dict(os.environ, GOENV='off', GOWORK='off', GOTOOLCHAIN='local', GOFLAGS='',
               GOOS='linux', GOARCH='amd64', GOAMD64='v1', CGO_ENABLED='0', GOEXPERIMENT='',
               GOPROXY='https://proxy.golang.org', GOSUMDB='sum.golang.org', GOPRIVATE='',
               GONOPROXY='', GONOSUMDB='', GOINSECURE='')

    with ExitStack() as cleanup:
        if args.module_proxy:
            # Start with an empty cache so every dependency used by the build is
            # checked by Go against go.sum on download, even in offline builds.
            if not args.module_proxy.is_dir():
                cli.error('Local module mirror not found')
            env['GOPROXY'] = args.module_proxy.resolve().as_uri()
            env['GOMODCACHE'] = cleanup.enter_context(tempfile.TemporaryDirectory(prefix='baci-go-mod-'))
        build(cli, output, env, offline=bool(args.module_proxy))


def build(cli, output, env, *, offline):
    def go(*argv, read=False):
        return subprocess.run(['go', *argv], cwd=SOURCE, env=env, check=True,
                              text=True, stdout=subprocess.PIPE if read else None).stdout

    if go('env','GOVERSION', read=True).strip() != GO_VERSION:
        cli.error(f'Build with the reviewed Go toolchain {GO_VERSION}')
    locks = {name: (SOURCE / name).read_bytes() for name in ('go.mod','go.sum')}
    if json.loads(go('mod','edit','-json', read=True)).get('Replace'):
        cli.error('Go replace directives are not allowed in the release build')
    go('test', '-mod=readonly', './...')
    go('vet', '-mod=readonly', './...')
    output.mkdir(parents=True, exist_ok=True)
    binary = output / BINARY
    go('build', '-mod=readonly', '-trimpath', '-buildvcs=false', '-ldflags=-s -w -buildid=', '-o', str(binary), '.')
    if not offline:  # May seek historic, unlinked .mod files from the public Go registry.
        go('mod', 'verify')
    if any((SOURCE / name).read_bytes() != data for name,data in locks.items()):
        cli.error('Go lock files changed during the build; review them before publishing')
    if subprocess.check_output([binary, '--check'], timeout=5) != b'baci-rtp/2\n':
        cli.error('Wrong native recorder IPC version')
    # Keep relevant upstream notices alongside the compiled executable.
    goroot = Path(go('env','GOROOT',read=True).strip())
    modules = go('list','-mod=readonly','-deps','-f',
        '{{with .Module}}{{if not .Main}}{{.Path}} {{.Version}} {{.Dir}}{{end}}{{end}}','.',read=True)
    notices = [b'Baci native receiver: notices for Go and the linked Go modules.\n', (goroot / 'LICENSE').read_bytes()]
    for module in sorted(set(modules.splitlines()) - {''}):
        path, version, directory = module.split(' ',2)
        notices.append(f'\n----- {path} {version} -----\n'.encode())
        for item in sorted(Path(directory).iterdir()):
            if item.is_file() and item.name.upper().startswith(('LICENSE', 'NOTICE', 'COPYING')):
                notices.extend((f'\n{item.name}:\n'.encode(), item.read_bytes()))
    (output / 'baci-native-NOTICES.txt').write_bytes(b'\n'.join(notices))
    hashes = ''.join(f'{hashlib.sha256(item.read_bytes()).hexdigest()}  {item.name}\n' for item in sorted(output.iterdir()))
    (output / 'SHA256SUMS.txt').write_text(hashes, encoding='ascii')
    print(hashes, end='')


if __name__ == '__main__':
    main()
