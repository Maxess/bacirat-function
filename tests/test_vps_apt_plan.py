"""Exercise real APT on synthetic packages and a private, disposable dpkg status."""
from io import BytesIO
import hashlib
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import tarfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
APT = ROOT / 'server/offline/apt-local.sh'
DIAGNOSE = ROOT / 'scripts/diagnose-vps-apt.sh'
NAME = 'baci-classroom-vps-offline'


def package(name, version, *, installed=False, depends=None, held=False):
    control = f'Package: {name}\nVersion: {version}\nArchitecture: amd64\nPriority: optional\nSection: misc\n'
    if installed:
        control += 'Status: ' + ('hold' if held else 'install') + ' ok installed\n'
    else:
        # No actual .deb exists. Only the resolver consumes these records.
        control += f'Filename: ./pool/{name}.deb\nSize: 100\nSHA256: ' + '0' * 64 + '\n'
    if depends:
        control += f'Depends: {depends}\n'
    return control + 'Maintainer: Fixture <qa@example.test>\nDescription: Synthetic classroom APT fixture\n\n'


@unittest.skipUnless(Path('/usr/bin/apt-get').is_file() and platform.machine() == 'x86_64', 'requires amd64 APT')
class AptPlanTest(unittest.TestCase):
    def setUp(self):
        self.work = Path(subprocess.check_output(['mktemp', '-d', '/var/tmp/baci-vps.XXXXXXXX'], text=True).strip())
        self.addCleanup(shutil.rmtree, self.work)
        self.status = self.work / 'status'
        self.index = self.work / 'apt/Packages'
        self.index.parent.mkdir()
        self.goals = self.work / 'ubuntu-packages.txt'
        self.goals.write_text('baci-qa-renderer\n')
        self.index.write_text(package('baci-qa-core', '2') + package('baci-qa-renderer', '1', depends='baci-qa-core (= 2)'))
        self.status.write_text(package('baci-qa-core', '1', installed=True))

    def script(self, path, *args, pin=None):
        text = path.read_text()
        # Keep all reads and any unexpected write in the throwaway fixture. A
        # real dpkg transaction is impossible even if the shell script regresses.
        guard = 'Dir::State::lists "$work/lists";'
        self.assertIn(guard, text)
        text = text.replace('Dir::State::status "/var/lib/dpkg/status";\n', '').replace(guard, guard + f'''
Dir::State::status "{self.status}";
Dir::State::extended_states "{self.work}/extended_states";
Dir::Bin::dpkg "/bin/false";
Dir::Log "$work";''')
        # Production APT uses an isolated network namespace; this test's only
        # source is a file index, so do not require root/userns in unit tests.
        text = text.replace('/usr/bin/unshare --net -- /usr/bin/apt-get', '/usr/bin/apt-get')
        text = text.replace('dpkg-query -W', f'dpkg-query --admindir={shlex.quote(str(self.work))} -W')
        if pin is not None:
            old = '282f266d3df3d613a5209be41798764b61fb7d42dc33efdc76001f29ca720255'
            self.assertIn(old, text)
            text = text.replace(old, pin)
        script = self.work / 'fixture.sh'
        script.write_text(text)
        before = self.status.read_bytes()
        result = subprocess.run(['/bin/bash', script, *args], cwd=self.work, env=os.environ.copy(),
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(self.status.read_bytes(), before)
        self.assertFalse((self.work / 'extended_states').exists())
        self.assertNotIn('/etc/apt/-/', result.stdout + result.stderr)
        return result

    def block_removal(self):
        with self.status.open('a') as file:
            file.write(package('baci-qa-provider-extra', '1', depends='baci-qa-core (= 1)', installed=True))

    def archive(self):
        archive = self.work / 'fixture.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for path in (self.index, self.goals):
                body = path.read_bytes()
                info = tarfile.TarInfo(f'{NAME}/{path.relative_to(self.work)}')
                info.size = len(body)
                tar.addfile(info, BytesIO(body))
        return archive

    def test_preflight_accepts_an_add_or_upgrade_without_changing_packages(self):
        result = self.script(APT, self.work, '--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('apt_plan=ok', result.stdout)

    def test_solver_names_the_proposed_removal_in_preflight_and_install(self):
        self.block_removal()
        for flags in (('--check',), ()):
            with self.subTest(flags=flags):
                result = self.script(APT, self.work, *flags)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('apt_plan=blocked', result.stderr)
                self.assertIn('Remv baci-qa-provider-extra', result.stderr)
                self.assertIn('no package install was attempted', result.stderr)

    def test_available_companion_candidate_avoids_removal_but_addon_stays_frozen(self):
        self.block_removal()
        with self.index.open('a') as file:
            file.write(package('baci-qa-provider-extra', '2', depends='baci-qa-core (= 2)'))
        fresh = self.script(APT, self.work, '--check')
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self.assertIn('apt_plan=ok', fresh.stdout)
        addon = self.script(APT, self.work, '--check', '--no-upgrades')
        self.assertEqual(addon.returncode, 1, addon.stderr)
        self.assertRegex(addon.stderr, r'apt_plan=(blocked|unresolved)')

    def test_addon_refuses_to_upgrade_an_installed_package_to_satisfy_deps(self):
        result = self.script(APT, self.work, '--check', '--no-upgrades')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertRegex(result.stderr, r'apt_plan=(blocked|unresolved)')
        self.assertIn('baci-qa-core', result.stderr)

    def test_public_release_diagnostic_checks_the_archive_and_reports_the_plan(self):
        self.block_removal()
        archive = self.archive()
        refused = self.script(DIAGNOSE, archive)
        self.assertEqual(refused.returncode, 1, refused.stderr)
        self.assertIn('SHA-256', refused.stderr)
        self.assertNotIn('apt_plan=', refused.stdout)
        result = self.script(DIAGNOSE, archive, pin=hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('apt_plan=blocked', result.stdout)
        self.assertIn('Remv baci-qa-provider-extra', result.stdout)


if __name__ == '__main__':
    unittest.main()
