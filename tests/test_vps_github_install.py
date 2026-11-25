"""Test the GitHub transport and privilege handoff without running a real installer."""
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
        self.mock('uname', 'echo x86_64')
        self.mock('dpkg', 'echo amd64')
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
        # The shipped launcher takes neither an override URL nor an override digest.
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
        args = ('--check', '--profile', 'live', '--tls', 'files', '--site', 'https://school.example.test/campus',
                '--media', 'rtc.example.test', '--ipv4', '192.0.0.9', '--cert', 'a chain.pem', '--key', './privkey.pem')
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
        self.assertIn('دانلود GitHub کامل نشد', result.stderr)
        self.assertFalse((self.work / 'sudo.log').exists())
        self.assertFalse((self.work / 'install.log').exists())

    def test_installer_failure_status_is_not_masked(self):
        self.env['INSTALL_ERROR'] = '23'
        self.assertEqual(self.launch('--tls', 'auto').returncode, 23)
        self.assertEqual(self.argv('install')[1:], ['--tls', 'auto'])

    def test_help_and_unsupported_os_do_not_download(self):
        self.env['WRONG_OS'] = '1'
        self.assertEqual(self.launch('--help').returncode, 0)
        unsupported = self.launch('--tls', 'auto')
        self.assertEqual(unsupported.returncode, 1)
        self.assertIn('24.04 LTS', unsupported.stderr)
        self.assertFalse((self.work / 'curl.log').exists())


if __name__ == '__main__':
    unittest.main()
