"""Test the offline/online transport, profile selection and privilege handoff."""
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = (ROOT / 'install-vps.sh').read_text()
RELEASE = json.loads((ROOT / 'docs/vps-offline-release.json').read_text())
ARCHIVE_URL = (f"https://github.com/Maxess/bacirat-function/releases/download/vps-offline-v{RELEASE['version']}/"
               f"baci-classroom-vps-offline-v{RELEASE['version']}-ubuntu24.04-amd64.tar.gz")


class GitHubLauncherTest(unittest.TestCase):
    def setUp(self):
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='baci-github-test-')))
        self.cwd = self.work / 'ssh home'
        self.tmp = self.work / 'download staging'
        self.bin = self.work / 'bin'
        for directory in (self.cwd, self.tmp, self.bin):
            directory.mkdir()
        self.env = {'TMPDIR': str(self.tmp), 'MOCK': str(self.work)}
        self.mock('grep', 'if [[ ${@: -1} == /etc/os-release ]]; then exit "${WRONG_OS:-0}"; fi\nexec /bin/grep "$@"')
        self.mock('uname', 'echo "${ARCH:-x86_64}"')
        self.mock('dpkg', 'echo amd64')
        self.mock('awk', 'if [[ ${@: -1} == /proc/meminfo ]]; then echo "${RAM:-984744} ${SWAP:-0}"; else exec /usr/bin/awk "$@"; fi')
        self.mock('curl', '''printf '%s\\0' "$@" > "$MOCK/curl.log"
while (( $# )); do
    if [[ $1 == --output ]]; then cp "$MOCK/fixture.tar.gz" "$2"; break; fi
    shift
done
exit "${DOWNLOAD_ERROR:-0}"''')
        self.mock('sudo', '''printf '%s\\0' "$@" > "$MOCK/sudo.log"
[[ $1 == -- ]] || exit 37
shift
exec "$@"''')
        script = b'''printf '%s\\0' "$PWD" "$@" > "$MOCK/install.log"
exit "${INSTALL_ERROR:-0}"
'''
        entry = tarfile.TarInfo('baci-classroom-vps-offline/install.sh')
        entry.size = len(script)
        self.archive = self.work / 'fixture.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as tar:
            tar.addfile(entry, BytesIO(script))

    def mock(self, name, command):
        path = self.bin / name
        path.write_text('#!/bin/bash\nset -eu\n' + command + '\n')
        path.chmod(0o700)

    def launch(self, *args, fixture_pin=True):
        # Only the harness replaces the PATH and (for success cases) the digest.
        # Mirrors are only another source for the same bytes; checksum is not overridable.
        original_path = 'export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C'
        self.assertIn(original_path, LAUNCHER)
        text = LAUNCHER.replace(original_path, f'export PATH={shlex.quote(str(self.bin))}:/usr/bin:/bin LC_ALL=C')
        if fixture_pin:
            text = text.replace(RELEASE['installer_sha256'], sha256(self.archive.read_bytes()).hexdigest())
        launcher = self.work / 'launcher.sh'
        launcher.write_text(text)
        result = subprocess.run(['/bin/bash', launcher, *args], cwd=self.cwd, env=self.env,
                                capture_output=True, encoding='utf-8', timeout=8)
        self.assertEqual(list(self.tmp.iterdir()), [], 'temporary downloads and extraction must be reclaimed')
        return result

    def argv(self, name):
        return (self.work / f'{name}.log').read_bytes().decode().split('\0')[:-1]

    def test_launcher_pins_public_installer_from_release_intent(self):
        for key, value in (('VERSION', RELEASE['version']), ('SHA256', RELEASE['installer_sha256'])):
            self.assertEqual(re.findall(rf'^readonly {key}=(.+)$', LAUNCHER, re.M), [value])

    def test_verified_download_forwards_exact_flags_with_original_directory(self):
        args = ('--check', '--tls', 'files', '--site', 'https://school.example.test/campus',
                '--media', 'rtc.example.test', '--ipv4', '192.0.0.9', '--cert', 'a chain.pem', '--key', './privkey.pem',
                '--profile', 'live')
        result = self.launch(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"archive_sha256=ok version={RELEASE['version']}", result.stdout)
        self.assertEqual(self.argv('install'), [str(self.cwd), *args])
        curl = self.argv('curl')
        self.assertEqual(curl[0], '--disable')
        self.assertEqual(curl[-1], ARCHIVE_URL)
        for option in ('--proto', '--proto-redir'):
            self.assertEqual(curl[curl.index(option) + 1], '=https')
        if os.geteuid():
            sudo = self.argv('sudo')
            self.assertEqual(sudo[:2], ['--', '/bin/bash'])
            self.assertTrue(sudo[2].endswith('/baci-classroom-vps-offline/install.sh'))
            self.assertEqual(sudo[3:], list(args))

    def test_wrong_digest_does_not_elevate_or_run(self):
        result = self.launch('--tls', 'auto', fixture_pin=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('SHA-256', result.stderr)
        self.assertNotIn('archive_sha256=ok', result.stdout)
        for name in ('sudo', 'install'):
            self.assertFalse((self.work / f'{name}.log').exists())

    def test_interrupted_download_does_not_run(self):
        self.env['DOWNLOAD_ERROR'] = '18'
        result = self.launch('--tls', 'auto')
        self.assertEqual(result.returncode, 1)
        self.assertIn('با SFTP منتقل کن', result.stderr)
        self.assertFalse((self.work / 'sudo.log').exists())
        self.assertFalse((self.work / 'install.log').exists())

    def test_installer_failure_status_is_not_masked(self):
        self.env['INSTALL_ERROR'] = '23'
        self.assertEqual(self.launch('--tls', 'auto').returncode, 23)
        self.assertEqual(self.argv('install')[1:], ['--tls', 'auto', '--profile', 'compact'])

    def test_local_bundle_uses_no_curl_and_selects_compact(self):
        (self.bin / 'curl').unlink()
        args = ('--tls', 'files', '--check', '--cert', 'chain file.pem', '--key', 'private key.pem')
        result = self.launch('--bundle', str(self.archive), *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.argv('install'), [str(self.cwd), *args, '--profile', 'compact'])
        self.assertFalse((self.work / 'curl.log').exists())

    def test_auto_detected_archive_is_verified_and_skips_remote_fetch(self):
        archive = self.cwd / ARCHIVE_URL.rsplit('/', 1)[1]
        archive.write_bytes(self.archive.read_bytes())
        self.env['RAM'] = '2000000'
        result = self.launch('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.argv('install'), [str(self.cwd), '--check', '--profile', 'full'])
        self.assertFalse((self.work / 'curl.log').exists())
        result = self.launch('--check', fixture_pin=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('SHA-256', result.stderr)
        self.assertFalse((self.work / 'curl.log').exists())

    def test_on_small_hardware_never_silently_drops_recording(self):
        self.env['RAM'] = '469200'
        refused = self.launch('--check', '--bundle', str(self.archive))
        self.assertIn('swap', refused.stderr)
        self.assertEqual(refused.returncode, 1)
        self.assertFalse((self.work / 'install.log').exists())
        self.assertEqual(self.launch('--profile=auto', '--check', '--bundle', str(self.archive)).returncode, 1)
        self.env['SWAP'] = '524284'
        ok = self.launch('--profile=auto', '--check', '--bundle', str(self.archive))
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.argv('install')[-2:], ['--profile', 'compact'])

    def test_https_mirror_is_an_alternative_for_the_exact_same_archive(self):
        url = 'https://mirror.example.ir/baci/test.tar.gz'
        bad = self.launch('--mirror=' + url, '--check', fixture_pin=False)
        self.assertEqual(bad.returncode, 1, bad.stderr)
        self.assertFalse((self.work / 'install.log').exists())
        ok = self.launch('--mirror=' + url, '--check')
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.argv('curl')[-1], url)
        self.assertEqual(self.argv('install'), [str(self.cwd), '--check', '--profile', 'compact'])
        self.assertNotIn('--insecure', self.argv('curl'))

    def test_rejects_insecure_mirror_and_conflicting_inputs_before_fetch(self):
        for args in [('--mirror=http://mirror.example.ir/vps.tar.gz',), ('--mirror=https://x:y@example.ir/vps.tar.gz',),
                     ('--mirror=https://example.ir/vps.tar.gz', '--bundle', str(self.archive)), ('--bundle',),
                     ('--profile', 'compact', '--profile', 'full')]:
            self.assertEqual(self.launch(*args).returncode, 1, args)
            self.assertFalse((self.work / 'curl.log').exists())
            self.assertFalse((self.work / 'install.log').exists())

    def test_help_and_unsupported_os_do_not_download(self):
        self.env['WRONG_OS'] = '1'
        self.assertEqual(self.launch('--help').returncode, 0)
        unsupported = self.launch('--tls', 'auto')
        self.assertEqual(unsupported.returncode, 1)
        self.assertIn('24.04 LTS', unsupported.stderr)
        self.assertFalse((self.work / 'curl.log').exists())


if __name__ == '__main__':
    unittest.main()
