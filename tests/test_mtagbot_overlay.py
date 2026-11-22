"""Verify the shipped site overlay against a pinned Baci V1.15 site tree when provided."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / 'plugin-manager'
BACI = HOST / 'baci'
BACI_PATCH = HOST / 'patches/baci-4.221.0-e0f3f686.patch'
UPSTREAM = json.loads((BACI / 'UPSTREAM.json').read_text())
REPLACED = ['includes/header.php', 'includes/management_hub.php', 'includes/student_nav.php', 'includes/teacher_nav.php']
RESTORED = ['other-settings.php', 'includes/footer.php']
ADDED = ['includes/classroom-school-bots.php', 'school-classroom-settings.php']
CALENDAR = ROOT / 'school-addon/upload/online-classroom/calendar.php'
BUNDLE = 'baci-classroom-upgrade-v0.4.21.zip'
UPDATE = 'baci-plugin-manager-update-v0.1.27.zip'
CLASSROOM = 'school-classroom-plugin-v0.4.21.zip'
RECORDER = 'baci-classroom-recorder-v0.4.21.zip'
UPGRADER = 'upgrade-v0.4.21.sh'


def check_patch(test, upstream, overlay, paths, patch, verify_pin=False):
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        for path in paths:
            (dest / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(upstream / path, dest / path)
            if verify_pin:
                test.assertEqual(hashlib.sha256((dest / path).read_bytes()).hexdigest(), UPSTREAM['original_files'][path])
        command = ['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)]
        subprocess.run(command, cwd=tmp, check=True, capture_output=True)
        for path in paths:
            test.assertEqual((dest / path).read_bytes(), (overlay / path).read_bytes())
        subprocess.run(command + ['-R'], cwd=tmp, check=True, capture_output=True)
        for path in paths:
            test.assertEqual((dest / path).read_bytes(), (upstream / path).read_bytes())


class BaciOverlayTest(unittest.TestCase):
    def test_archives_ship_the_pinned_baci_overlay_and_no_watcher_or_bot_credentials(self):
        with ZipFile(ROOT / 'artifacts/baci-plugin-manager-v0.1.27.zip') as site:
            for path in REPLACED + RESTORED + ADDED + ['UPSTREAM.json']:
                self.assertEqual(site.read(path), (BACI / path).read_bytes())
            self.assertEqual(site.read('includes/classroom-calendar.php'), CALENDAR.read_bytes())
            self.assertEqual(site.read('README-fa.md'), (HOST / 'README-fa.md').read_bytes())
            self.assertEqual(site.read('plugins/school-plugins.php'), (HOST / 'plugins/school-plugins.php').read_bytes())
            self.assertEqual(site.read(f'patches/{BACI_PATCH.name}'), BACI_PATCH.read_bytes())
            for file in ('includes/bot_helpers.php', 'classes.php', 'import-schedule.php', 'config/database.php', 'sql/database.sql'):
                self.assertNotIn(file, site.namelist())
            for name in ('README-fa.md', 'WEBHOOK-fa.md', 'install.py', 'admission.py'):
                self.assertEqual(site.read(f'media/{name}'), (ROOT / 'server' / name).read_bytes())
        with ZipFile(ROOT / 'artifacts' / UPDATE) as update:
            self.assertEqual(sorted(update.namelist()), sorted(REPLACED + RESTORED + ADDED + ['includes/classroom-calendar.php', 'plugins/school-plugins.php']))
            self.assertEqual(update.read('includes/classroom-calendar.php'), CALENDAR.read_bytes())
            for path in REPLACED + RESTORED + ADDED:
                self.assertEqual(update.read(path), (BACI / path).read_bytes())
            for path in RESTORED:
                self.assertEqual(hashlib.sha256(update.read(path)).hexdigest(), UPSTREAM['original_files'][path])
            self.assertEqual(update.read('plugins/school-plugins.php'), (HOST / 'plugins/school-plugins.php').read_bytes())
        with ZipFile(ROOT / 'artifacts' / 'baci-plugin-manager-update-v0.1.22.zip') as previous, ZipFile(ROOT / 'artifacts' / UPDATE) as update:
            self.assertEqual(set(previous.namelist()), set(update.namelist()))
            self.assertEqual([name for name in update.namelist() if update.read(name) != previous.read(name)], ['plugins/school-plugins.php'])
        with ZipFile(ROOT / 'artifacts' / CLASSROOM) as plugin:
            manifest = json.loads(plugin.read('plugin.json'))
            self.assertEqual(manifest['routes']['events'], 'events.php')
            self.assertNotIn('watch', manifest['routes'])
            self.assertNotIn('watch.php', plugin.namelist())
            self.assertEqual(set(manifest['menus']), {'admin','deputy','teacher','student'})
            self.assertFalse(any('token' in item['key'] for item in manifest['settings']))
            self.assertIn('0.4.9', manifest['preserve_settings_from'])
            self.assertIn('0.4.10', manifest['preserve_settings_from'])
            self.assertIn('0.4.11', manifest['preserve_settings_from'])
            self.assertIn('0.4.12', manifest['preserve_settings_from'])
            self.assertIn('0.4.13', manifest['preserve_settings_from'])
            self.assertIn('0.4.14', manifest['preserve_settings_from'])
            self.assertIn('0.4.15', manifest['preserve_settings_from'])
            self.assertIn('0.4.16', manifest['preserve_settings_from'])
            self.assertIn('0.4.18', manifest['preserve_settings_from'])
            self.assertIn('0.4.19', manifest['preserve_settings_from'])
            self.assertIn('0.4.20', manifest['preserve_settings_from'])
            self.assertEqual(manifest['routes']['recording'], 'recording-api.php')
            presets = json.loads(plugin.read('video-presets.json'))
            fields_by_key = {field['key']: field for field in manifest['settings']}
            self.assertEqual(presets['camera']['sizes'], [f'{n}x{n}' for n in (32,64,128,256,360,480)])
            for kind in ('camera', 'screen'):
                self.assertEqual(len(presets[kind]['sizes']), 6)
                for role in ('teacher', 'student'):
                    default = fields_by_key[f'default_{role}_{kind}']
                    self.assertEqual((default['type'], default['default']), ('select', 'low'))
                    self.assertEqual(default['options'], ['low', 'medium', 'high'])
                for level in ('low', 'medium', 'high'):
                    size, fps = presets[kind]['defaults'][level]
                    resolution = fields_by_key[f'{kind}_{level}_size']
                    rate = fields_by_key[f'{kind}_{level}_fps']
                    self.assertEqual((resolution['type'], rate['type']), ('select', 'select'))
                    self.assertEqual((resolution['default'], rate['default']), (size, str(fps)))
                    self.assertEqual(resolution['options'], presets[kind]['sizes'])
                    self.assertEqual(rate['options'], [str(n) for n in range(5,21)])
            rectangles = [tuple(map(int, r.split('x'))) for r in presets['screen']['sizes']]
            self.assertTrue(all(w > h for w, h in rectangles))
            self.assertTrue(all(w0 < w1 and h0 < h1 for (w0,h0), (w1,h1) in zip(rectangles, rectangles[1:])))
            self.assertEqual(set(plugin.namelist()), set(manifest['files']) | {'plugin.json'})
            with ZipFile(BytesIO(plugin.read(manifest['pdf_resources']))) as resources:
                for name in ('pdf.min.mjs', 'pdf.worker.min.mjs'):
                    self.assertNotIn(f'assets/{name}', plugin.namelist())
                    self.assertEqual(resources.getinfo(f'runtime/{name}').compress_type, ZIP_DEFLATED)
                self.assertIn(b'ClassroomPDFPersian', resources.read('runtime/pdf.min.mjs'))
            self.assertEqual(plugin.read('calendar.php'), CALENDAR.read_bytes())
            self.assertLess((ROOT / 'artifacts' / CLASSROOM).stat().st_size, 4 * 1024 * 1024)
            for name, digest in manifest['files'].items():
                self.assertEqual(hashlib.sha256(plugin.read(name)).hexdigest(), digest)
            self.assertEqual(plugin.read('room-view.php'), (ROOT / 'school-addon/upload/online-classroom/room-view.php').read_bytes())
            fields = {field['key']: field['type'] for field in manifest['settings']}
            self.assertEqual(fields['screen_share'], 'switch')
            self.assertEqual(fields['camera_share'], 'switch')
            self.assertEqual(fields['repeat_bells'], 'switch')
            self.assertEqual(plugin.read('assets/room-attention.js'), (ROOT / 'school-addon/upload/online-classroom/assets/room-attention.js').read_bytes())
            self.assertEqual(fields['bell_hours'], 'bell-hours')
            self.assertEqual(fields['friday_test'], 'friday-test')

    def test_archive_release_packages_site_and_vps_separately_with_reviewed_hashes(self):
        with ZipFile(ROOT / 'artifacts' / BUNDLE) as bundle:
            self.assertEqual(set(bundle.namelist()), {UPDATE, CLASSROOM, RECORDER, UPGRADER, 'START-HERE-fa.txt', 'SHA256SUMS.txt'})
            notes = bundle.read('START-HERE-fa.txt').decode('utf-8')
            self.assertIn('هر ۱۰ فایل', notes)
            self.assertIn('بدون بازکردن', notes)
            hashes = dict(line.split('  ')[::-1] for line in bundle.read('SHA256SUMS.txt').decode('ascii').splitlines())
            self.assertEqual(set(hashes), {UPDATE, CLASSROOM, RECORDER, UPGRADER})
            for name in (UPDATE, CLASSROOM, RECORDER):
                archive_bytes = bundle.read(name)
                self.assertEqual(archive_bytes, (ROOT / 'artifacts' / name).read_bytes())
                self.assertEqual(hashlib.sha256(archive_bytes).hexdigest(), hashes[name])
            script = bundle.read(UPGRADER)
            self.assertEqual(script, (ROOT / 'server/recorder' / UPGRADER).read_bytes())
            self.assertEqual(hashlib.sha256(script).hexdigest(), hashes[UPGRADER])
            self.assertIn(f'readonly digest={hashes[RECORDER]}\n'.encode(), script)
            recorder_release = bundle.read(RECORDER)
            with ZipFile(BytesIO(recorder_release)) as recorder, ZipFile(BytesIO(bundle.read(CLASSROOM))) as classroom:
                for entry in ('board.js', 'livekit-client.umd.js', 'pdf-resources.zip'):
                    self.assertEqual(recorder.read(entry), classroom.read('assets/' + entry))
                self.assertIn('school-classroom-recorder.service', recorder.namelist())
                self.assertNotIn('config.json', recorder.namelist())
                self.assertIn('fonts/OFL.txt', recorder.namelist())

    @unittest.skipUnless(os.environ.get('MPM_TEST_BACI'), 'set MPM_TEST_BACI to the extracted, unmodified Baci Release_V1.15-Site.zip')
    def test_pinned_baci_patch_matches_replacements_and_can_be_reversed(self):
        upstream = Path(os.environ['MPM_TEST_BACI'])
        manifest = json.loads((upstream / 'RELEASE-MANIFEST.json').read_text())
        self.assertEqual(manifest['site_version'], UPSTREAM['site_version'])
        self.assertEqual(manifest['platform'], 'site')
        check_patch(self, upstream, BACI, REPLACED, BACI_PATCH, verify_pin=True)
        for path in RESTORED:
            self.assertEqual((upstream / path).read_bytes(), (BACI / path).read_bytes())

    @unittest.skipUnless(os.environ.get('MPM_TEST_MTAGBOT'), 'set MPM_TEST_MTAGBOT to a checked-out 726ef103 tree')
    def test_legacy_mtagbot_patch_still_matches_replacements_and_can_be_reversed(self):
        check_patch(self, Path(os.environ['MPM_TEST_MTAGBOT']), HOST / 'mtagbot',
                    ['other-settings.php', 'includes/header.php', 'includes/footer.php'], HOST / 'patches/mtagbot-726ef103.patch')
