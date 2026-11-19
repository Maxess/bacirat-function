"""Check the shipped host overlay, optionally against a local copy of mtagbot@726ef103."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / 'plugin-manager'
REPLACED = ['other-settings.php', 'includes/header.php', 'includes/footer.php']


class MtagbotOverlayTest(unittest.TestCase):
    def test_archives_ship_the_native_overlay_and_no_watcher_or_bot_credentials(self):
        with ZipFile(ROOT / 'artifacts/mtagbot-plugin-manager-v0.1.6.zip') as site:
            for path in REPLACED + ['includes/classroom-school-bots.php']:
                self.assertEqual(site.read(path), (HOST / 'mtagbot' / path).read_bytes())
            self.assertEqual(site.read('plugins/school-plugins.php'), (HOST / 'plugins/school-plugins.php').read_bytes())
            self.assertEqual(site.read('patches/mtagbot-726ef103.patch'), (HOST / 'patches/mtagbot-726ef103.patch').read_bytes())
            self.assertNotIn('includes/bot_helpers.php', site.namelist())
        with ZipFile(ROOT / 'artifacts/school-classroom-plugin-v0.4.0.zip') as plugin:
            manifest = json.loads(plugin.read('plugin.json'))
            self.assertEqual(manifest['routes']['events'], 'events.php')
            self.assertNotIn('watch', manifest['routes'])
            self.assertNotIn('watch.php', plugin.namelist())
            self.assertEqual(set(manifest['menus']), {'admin','deputy','teacher','student'})
            self.assertFalse(any('token' in item['key'] for item in manifest['settings']))

    @unittest.skipUnless(os.environ.get('MPM_TEST_MTAGBOT'), 'set MPM_TEST_MTAGBOT to a checked-out 726ef103 tree')
    def test_pinned_host_patch_matches_replacements_and_can_be_reversed(self):
        upstream = Path(os.environ['MPM_TEST_MTAGBOT'])
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            for path in REPLACED:
                (dest / path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(upstream / path, dest / path)
            patch = HOST / 'patches/mtagbot-726ef103.patch'
            subprocess.run(['patch', '--batch', '-p1', '-i', str(patch)], cwd=tmp, check=True, capture_output=True)
            for path in REPLACED:
                self.assertEqual((dest / path).read_bytes(), (HOST / 'mtagbot' / path).read_bytes())
            subprocess.run(['patch', '--batch', '-R', '-p1', '-i', str(patch)], cwd=tmp, check=True, capture_output=True)
            for path in REPLACED:
                self.assertEqual((dest / path).read_bytes(), (upstream / path).read_bytes())
