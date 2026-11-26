#!/usr/bin/python3
"""Replay the v1.3.0 removals against signed Noble GA Server package metadata.

This is an APT-state fixture, not a copy of any user's machine. No dpkg
transaction is performed. With --candidate, the tested indexes come from the
exact installer archive; without it, only signed index metadata is needed.
"""
import argparse
from email.parser import Parser
import importlib.util
import os
from pathlib import Path
import re
import subprocess as sp
import tarfile
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('builder', HERE / 'build-vps-offline.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
OLD_SHA = '282f266d3df3d613a5209be41798764b61fb7d42dc33efdc76001f29ca720255'
# Stock Server metapackages plus the ordinary image packages seen in the
# report. Exact checks keep this anchored to the GA pocket, not current updates.
SERVER = ('ubuntu-server', 'ubuntu-server-minimal', 'ubuntu-standard', 'vim', 'upower', 'snapd', 'packagekit-tools', 'modemmanager', 'udisks2',
          'dbus-user-session', 'gir1.2-glib-2.0', 'gir1.2-girepository-2.0', 'libglib2.0-bin', 'networkd-dispatcher', 'python3-gi')
EXPECTED = {
    'ubuntu-server': '1.539', 'ubuntu-server-minimal': '1.539',
    'systemd-resolved': '255.4-1ubuntu8', 'libpam-systemd': '255.4-1ubuntu8',
    'dbus': '1.14.10-4ubuntu4', 'dbus-user-session': '1.14.10-4ubuntu4',
    'gir1.2-glib-2.0': '2.80.0-6ubuntu1', 'libglib2.0-bin': '2.80.0-6ubuntu1',
    'libpython3.12t64': '3.12.3-1', 'vim': '2:9.1.0016-1ubuntu7',
}


def resolve(goals, env, *, no_remove=False):
    argv = ['apt-get', '-q', '--simulate']
    if no_remove:
        argv.append('--no-remove')
    return sp.run([*argv, 'install', *goals], env=env, text=True,
                  stdout=sp.PIPE, stderr=sp.STDOUT, timeout=90)


def solved_index(result, env):
    if result.returncode:
        raise RuntimeError(result.stdout)
    versions = dict(re.findall(r'^Inst (\S+) \((\S+) ', result.stdout, re.M))
    if not versions:
        raise ValueError('The release-suite solver returned no package versions')
    rows = build.run('apt-cache', 'show', *(f'{name}={version}' for name, version in versions.items()),
                     env=env, text=True, capture_output=True)
    controls = {}
    for raw in rows.strip().split('\n\n'):
        record = Parser().parsestr(raw)
        if record['Package'] in versions and record['Version'] == versions[record['Package']]:
            controls[record['Package']] = raw
    if set(versions) != set(controls):
        raise ValueError(f'No signed control data for {sorted(set(versions) - set(controls))}')
    return versions, controls


def ga_status(root):
    env = build.apt_environment(root / 'ga', 'noble')
    versions, controls = solved_index(resolve(SERVER, env, no_remove=True), env)
    for name, version in EXPECTED.items():
        if versions.get(name) != version:
            raise ValueError(f'GA fixture: expected {name}={version}; got {versions.get(name)}')
    status = root / 'ga-installed.status'
    status.write_text(''.join(f'Status: install ok installed\n{raw}\n\n' for raw in controls.values()))
    print(f'QA_APT GA fixture has {len(versions)} installed packages; reported base versions match.', flush=True)
    return status


def unpack_indexes(archive, root):
    paths = {f'{build.BUNDLE}/apt/Packages': root / 'Packages',
             f'{build.BUNDLE}/ubuntu-packages.txt': root / 'goals'}
    with tarfile.open(archive, 'r|gz') as tar:
        for member in tar:
            if member.name in paths:
                if not member.isfile() or member.size > 16 * 1024**2:
                    raise ValueError('Invalid archived APT index or goals')
                paths.pop(member.name).write_bytes(tar.extractfile(member).read())
            if not paths:
                break
    if paths:
        raise ValueError(f'Archive is missing APT indexes: {sorted(paths)}')
    return [line for line in (root / 'goals').read_text().splitlines() if line and not line.startswith('#')]


def file_environment(repo, status):
    work = repo / 'state'
    for directory in ('empty', 'lists/partial', 'archives/partial', 'log'):
        (work / directory).mkdir(parents=True)
    sources = work / 'sources.list'
    sources.write_text(f'deb [trusted=yes arch=amd64] file:{repo} ./\n')
    config = work / 'apt.conf'
    config.write_text(f'''Dir::Etc::main "/dev/null";
Dir::Etc::parts "{work}/empty";
Dir::Etc::sourcelist "{sources}";
Dir::Etc::sourceparts "{work}/empty";
Dir::Etc::preferences "/dev/null";
Dir::Etc::preferencesparts "{work}/empty";
Dir::State::status "{status}";
Dir::State::extended_states "{work}/extended_states";
Dir::State::lists "{work}/lists";
Dir::Cache::archives "{work}/archives";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
Dir::Log "{work}/log";
APT::Architecture "amd64";
APT::Architectures {{ "amd64"; }};
APT::Get::Always-Include-Phased-Updates "true";
APT::Install-Recommends "false";
APT::Install-Suggests "false";
Acquire::Languages "none";
''')
    env = os.environ | {'APT_CONFIG': str(config), 'LC_ALL': 'C'}
    build.run('apt-get', '-qq', '-o', 'APT::Update::Error-Mode=any', 'update', env=env)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('previous', type=Path, help='the exact public v1.3.0 installer archive')
    parser.add_argument('--candidate', type=Path, help='use the candidate archive instead of solving new signed metadata')
    args = parser.parse_args()
    if build.sha(args.previous) != OLD_SHA:
        raise ValueError('Wrong SHA-256 for the v1.3.0 regression baseline')
    with TemporaryDirectory(prefix='baci-noble-compat-') as name:
        work = Path(name)
        status = ga_status(work)
        original = status.read_bytes()
        old, new = work / 'previous', work / 'candidate'
        old.mkdir()
        new.mkdir()
        old_goals = unpack_indexes(args.previous, old)
        previous_versions = {record['Package']: record['Version'] for raw in (old / 'Packages').read_text().split('\n\n')
                             if (record := Parser().parsestr(raw))['Package']}
        cohort = ('libdbus-1-3', 'dbus', 'dbus-daemon', 'dbus-user-session', 'libglib2.0-0t64', 'libglib2.0-bin',
                  'gir1.2-glib-2.0', 'python3', 'python3.12', 'libpython3.12t64')
        print('QA_APT v1.3.0 index: ' + ', '.join(f'{pkg}={previous_versions.get(pkg, "absent")}' for pkg in cohort), flush=True)
        old_env = file_environment(old, status)
        before = resolve(old_goals, old_env)
        removed = set(re.findall(r'^Remv (\S+)', before.stdout, re.M))
        if before.returncode or not {'ubuntu-server', 'systemd-resolved', 'dbus', 'libpython3.12t64'} <= removed:
            raise RuntimeError('The v1.3.0 failure was not reproduced on the GA fixture:\n' + before.stdout)
        blocked = resolve(old_goals, old_env, no_remove=True)
        if blocked.returncode == 0 or 'remove is disabled' not in blocked.stdout:
            raise RuntimeError('Could not reproduce the old guard refusal:\n' + blocked.stdout)
        print(f'QA_APT v1.3.0 refuses {len(removed)} proposed removals, including Ubuntu Server, systemd-resolved, D-Bus and libpython.', flush=True)
        runtime = build.package_goals('ubuntu-packages.txt')
        compat = build.package_goals('ubuntu-compat-packages.txt')
        if set(runtime) & set(compat):
            raise ValueError('Compatibility candidates must not become unconditional install goals')
        if args.candidate:
            if unpack_indexes(args.candidate, new) != runtime:
                raise ValueError('The archive runtime goals do not match the reviewed list')
        else:
            env = build.apt_environment(work / 'current')
            _, controls = solved_index(resolve(runtime + compat, env, no_remove=True), env)
            (new / 'Packages').write_text('\n\n'.join(controls.values()) + '\n\n')
        after = resolve(runtime, file_environment(new, status), no_remove=True)
        if after.returncode or re.search(r'^(Remv |Purg |The following (packages will be DOWNGRADED|held packages will be changed):)', after.stdout, re.M):
            raise RuntimeError('The new offline plan is not safe on the GA fixture:\n' + after.stdout)
        if not all(re.search(rf'^Inst {re.escape(name)} \[', after.stdout, re.M) for name in compat):
            raise RuntimeError('The formerly mismatched installed families were not aligned:\n' + after.stdout)
        if re.search(r'^Inst (?:systemd|systemd-resolved|libpam-systemd|openssh-server|linux-image\S*)\s', after.stdout, re.M):
            raise RuntimeError('The repair unexpectedly updates systemd, SSH or the kernel:\n' + after.stdout)
        if status.read_bytes() != original:
            raise RuntimeError('APT simulation changed the GA fixture')
        print('QA_APT candidate reconciles the installed D-Bus, GLib and Python cohorts; no removals, downgrades or Ubuntu service/SSH/kernel replacements.')


if __name__ == '__main__':
    main()
