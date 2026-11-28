"""Verify the site-only update, including its trust pin and unchanged Recorder protocol."""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'artifacts'
PLUGIN = 'school-classroom-plugin-v0.4.24.zip'
UPDATE = 'baci-plugin-manager-update-v0.1.30.zip'
FULL = 'baci-plugin-manager-v0.1.30.zip'
BUNDLE = 'baci-classroom-site-upgrade-v0.4.24.zip'
GUIDE = ROOT / 'docs/baci-classroom-site-v0.4.24.fa.txt'
GATEWAY = 'plugins/school-plugins.php'


def hashes(data):
    return {name: checksum for checksum, name in (line.split('  ', 1) for line in data.decode('ascii').splitlines())}


class SiteReleaseTest(unittest.TestCase):
    def test_site_bundle_inventory_and_digests(self):
        expected = hashes((ARTIFACTS / 'SHA256SUMS-v0.4.24.txt').read_bytes())
        self.assertEqual(set(expected), {PLUGIN, UPDATE, FULL, BUNDLE})
        for name, digest in expected.items():
            self.assertEqual(sha256((ARTIFACTS / name).read_bytes()).hexdigest(), digest, name)
        with ZipFile(ARTIFACTS / BUNDLE) as bundle:
            self.assertCountEqual(bundle.namelist(), [PLUGIN, UPDATE, 'START-HERE-fa.txt', 'SHA256SUMS.txt'])
            packaged = hashes(bundle.read('SHA256SUMS.txt'))
            self.assertEqual(set(packaged), {PLUGIN, UPDATE, 'START-HERE-fa.txt'})
            for name, digest in packaged.items():
                self.assertEqual(sha256(bundle.read(name)).hexdigest(), digest, name)
            for name in (PLUGIN, UPDATE):
                self.assertEqual(packaged[name], expected[name])
            self.assertEqual(bundle.read('START-HERE-fa.txt'), GUIDE.read_bytes())
            with ZipFile(BytesIO(bundle.read(UPDATE))) as update, \
                    ZipFile(ARTIFACTS / 'baci-plugin-manager-update-v0.1.29.zip') as old:
                self.assertCountEqual(update.namelist(), old.namelist())
                self.assertEqual([name for name in update.namelist() if update.read(name) != old.read(name)], [GATEWAY])

    def test_gateway_trusts_exact_site_manifest_with_one_new_setting(self):
        with ZipFile(ARTIFACTS / PLUGIN) as plugin, ZipFile(ARTIFACTS / UPDATE) as update, \
                ZipFile(ARTIFACTS / FULL) as full, ZipFile(ARTIFACTS / 'school-classroom-plugin-v0.4.23.zip') as old:
            raw = plugin.read('plugin.json')
            manifest = json.loads(raw)
            previous = json.loads(old.read('plugin.json'))
            manager = update.read(GATEWAY)
            self.assertIn(b"const MPM_VERSION = '0.1.30';", manager)
            self.assertIn(f"'0.4.24' => '{sha256(raw).hexdigest()}'".encode('ascii'), manager)
            for name in update.namelist():
                self.assertEqual(full.read(name), update.read(name), name)
            self.assertEqual(manifest['version'], '0.4.24')
            self.assertEqual(manifest['preserve_settings_from'], previous['preserve_settings_from'] + ['0.4.23'])
            new_fields = [field for field in manifest['settings'] if field not in previous['settings']]
            [field] = new_fields
            self.assertEqual((field['key'], field['type'], field['min'], field['max'], field['default']),
                             ('attendance_min_minutes', 'integer', 0, 120, 0))
            self.assertEqual([old_field for old_field in manifest['settings'] if old_field != field], previous['settings'])
            self.assertEqual(manifest['routes'], previous['routes'])
            self.assertCountEqual(plugin.namelist(), ['plugin.json', *manifest['files']])
            for name, digest in manifest['files'].items():
                self.assertEqual(sha256(plugin.read(name)).hexdigest(), digest, name)
            for name in ('recording.php', 'recording-api.php', 'recordings-view.php', 'assets/board.js',
                         'assets/livekit-client.umd.js', 'assets/pdf-resources.zip'):
                self.assertEqual(plugin.read(name), old.read(name), name)
            self.assertIn(b"CL_REC_RELEASE = '0.4.23'", plugin.read('recording.php'))


class Site25ReleaseTest(unittest.TestCase):
    def test_bundle_only_updates_the_site_and_keeps_the_trust_chain(self):
        plugin = 'school-classroom-plugin-v0.4.25.zip'
        update = 'baci-plugin-manager-update-v0.1.32.zip'
        full = 'baci-plugin-manager-v0.1.32.zip'
        bundle = 'baci-classroom-site-upgrade-v0.4.25.zip'
        guide = 'START-HERE-fa.txt'
        expected = hashes((ARTIFACTS / 'SHA256SUMS-v0.4.25.txt').read_bytes())
        self.assertEqual(set(expected), {plugin, update, full, bundle})
        for name, digest in expected.items():
            self.assertEqual(sha256((ARTIFACTS / name).read_bytes()).hexdigest(), digest, name)
        with ZipFile(ARTIFACTS / bundle) as outer:
            self.assertCountEqual(outer.namelist(), [plugin, update, guide, 'SHA256SUMS.txt'])
            self.assertEqual(hashes(outer.read('SHA256SUMS.txt')), {
                plugin: expected[plugin], update: expected[update], guide: sha256(outer.read(guide)).hexdigest(),
            })
            for name in (plugin, update):
                self.assertEqual(sha256(outer.read(name)).hexdigest(), expected[name], name)
            self.assertEqual(outer.read(guide), (ROOT / 'docs/baci-classroom-site-v0.4.25.fa.txt').read_bytes())
        with ZipFile(ARTIFACTS / update) as updater, ZipFile(ARTIFACTS / full) as complete, \
                ZipFile(ARTIFACTS / 'baci-plugin-manager-update-v0.1.31.zip') as previous:
            self.assertCountEqual(updater.namelist(), previous.namelist())
            self.assertEqual([n for n in updater.namelist() if updater.read(n) != previous.read(n)], [GATEWAY])
            self.assertTrue(all(complete.read(n) == updater.read(n) for n in updater.namelist()))
            gateway = updater.read(GATEWAY)
            self.assertIn(b"const MPM_VERSION = '0.1.32';", gateway)
        with ZipFile(ARTIFACTS / plugin) as new, ZipFile(ARTIFACTS / PLUGIN) as old:
            raw = new.read('plugin.json')
            current, prior = json.loads(raw), json.loads(old.read('plugin.json'))
            self.assertIn(f"'0.4.25' => '{sha256(raw).hexdigest()}'".encode('ascii'), gateway)
            self.assertEqual(current['version'], '0.4.25')
            self.assertEqual(current['settings'], prior['settings'])
            self.assertEqual(current['routes'], prior['routes'])
            self.assertEqual(current['preserve_settings_from'], prior['preserve_settings_from'] + ['0.4.24'])
            self.assertCountEqual(new.namelist(), ['plugin.json', *current['files']])
            for name, digest in current['files'].items():
                data = new.read(name)
                self.assertEqual(sha256(data).hexdigest(), digest, name)
            for name in ('recording-api.php', 'assets/board.js', 'assets/livekit-client.umd.js', 'assets/pdf-resources.zip'):
                self.assertEqual(new.read(name), old.read(name), name)
            source = new.read('recording.php')
            self.assertIn(b'CL_REC_PROTOCOL = 2;', source)
            self.assertIn(b"CL_REC_RELEASE = '0.4.23';", source)


class Site26ReleaseTest(unittest.TestCase):
    def test_bundle_only_updates_the_site_and_keeps_the_trust_chain(self):
        plugin = 'school-classroom-plugin-v0.4.26.zip'
        update = 'baci-plugin-manager-update-v0.1.33.zip'
        full = 'baci-plugin-manager-v0.1.33.zip'
        bundle = 'baci-classroom-site-upgrade-v0.4.26.zip'
        guide = 'START-HERE-fa.txt'
        expected = hashes((ARTIFACTS / 'SHA256SUMS-v0.4.26.txt').read_bytes())
        self.assertEqual(set(expected), {plugin, update, full, bundle})
        for name, digest in expected.items():
            self.assertEqual(sha256((ARTIFACTS / name).read_bytes()).hexdigest(), digest, name)
        with ZipFile(ARTIFACTS / bundle) as outer:
            self.assertCountEqual(outer.namelist(), [plugin, update, guide, 'SHA256SUMS.txt'])
            self.assertEqual(hashes(outer.read('SHA256SUMS.txt')), {
                plugin: expected[plugin], update: expected[update], guide: sha256(outer.read(guide)).hexdigest(),
            })
            for name in (plugin, update):
                self.assertEqual(sha256(outer.read(name)).hexdigest(), expected[name], name)
            self.assertEqual(outer.read(guide), (ROOT / 'docs/baci-classroom-site-v0.4.26.fa.txt').read_bytes())
        with ZipFile(ARTIFACTS / update) as updater, ZipFile(ARTIFACTS / full) as complete, \
                ZipFile(ARTIFACTS / 'baci-plugin-manager-update-v0.1.32.zip') as previous:
            self.assertCountEqual(updater.namelist(), previous.namelist())
            self.assertEqual([n for n in updater.namelist() if updater.read(n) != previous.read(n)], [GATEWAY])
            self.assertTrue(all(complete.read(n) == updater.read(n) for n in updater.namelist()))
            self.assertEqual(complete.read('README-fa.md'), (ROOT / 'plugin-manager/README-fa.md').read_bytes())
            gateway = updater.read(GATEWAY)
            self.assertIn(b"const MPM_VERSION = '0.1.33';", gateway)
        with ZipFile(ARTIFACTS / plugin) as new, ZipFile(ARTIFACTS / 'school-classroom-plugin-v0.4.25.zip') as old:
            raw = new.read('plugin.json')
            current, prior = json.loads(raw), json.loads(old.read('plugin.json'))
            self.assertIn(f"'0.4.26' => '{sha256(raw).hexdigest()}'".encode('ascii'), gateway)
            self.assertEqual(current['version'], '0.4.26')
            self.assertEqual(current['settings'], prior['settings'])
            self.assertEqual(current['routes'], prior['routes'])
            self.assertEqual(current['preserve_settings_from'], prior['preserve_settings_from'] + ['0.4.25'])
            self.assertCountEqual(new.namelist(), ['plugin.json', *current['files']])
            for name, digest in current['files'].items():
                data = new.read(name)
                self.assertEqual(sha256(data).hexdigest(), digest, name)
            for name in ('recording-api.php', 'assets/board.js', 'assets/livekit-client.umd.js', 'assets/pdf-resources.zip'):
                self.assertEqual(new.read(name), old.read(name), name)
            source = new.read('recording.php')
            self.assertIn(b'CL_REC_PROTOCOL = 2;', source)
            self.assertIn(b"['archiveCamera']", source)
            self.assertIn(b"CL_REC_RELEASE = '0.4.23';", source)


class Site27ReleaseTest(unittest.TestCase):
    def test_upgrade_keeps_settings_routes_and_stock_media_assets(self):
        plugin = 'school-classroom-plugin-v0.4.27.zip'
        update = 'baci-plugin-manager-update-v0.1.34.zip'
        full = 'baci-plugin-manager-v0.1.34.zip'
        bundle = 'baci-classroom-site-upgrade-v0.4.27.zip'
        guide = 'START-HERE-fa.txt'
        expected = hashes((ARTIFACTS / 'SHA256SUMS-v0.4.27.txt').read_bytes())
        self.assertEqual(set(expected), {plugin, update, full, bundle})
        for name, digest in expected.items():
            self.assertEqual(sha256((ARTIFACTS / name).read_bytes()).hexdigest(), digest, name)
        with ZipFile(ARTIFACTS / bundle) as outer:
            self.assertCountEqual(outer.namelist(), [plugin, update, guide, 'SHA256SUMS.txt'])
            self.assertEqual(hashes(outer.read('SHA256SUMS.txt')), {
                plugin: expected[plugin], update: expected[update], guide: sha256(outer.read(guide)).hexdigest(),
            })
            for name in (plugin, update):
                self.assertEqual(sha256(outer.read(name)).hexdigest(), expected[name], name)
            self.assertEqual(outer.read(guide), (ROOT / 'docs/baci-classroom-site-v0.4.27.fa.txt').read_bytes())
        with ZipFile(ARTIFACTS / update) as updater, ZipFile(ARTIFACTS / full) as complete, \
                ZipFile(ARTIFACTS / 'baci-plugin-manager-update-v0.1.33.zip') as previous:
            self.assertCountEqual(updater.namelist(), previous.namelist())
            self.assertEqual([n for n in updater.namelist() if updater.read(n) != previous.read(n)], [GATEWAY])
            self.assertTrue(all(updater.read(n) == complete.read(n) for n in updater.namelist()))
            gateway = updater.read(GATEWAY)
            self.assertIn(b"const MPM_VERSION = '0.1.34';", gateway)
        with ZipFile(ARTIFACTS / plugin) as new, ZipFile(ARTIFACTS / 'school-classroom-plugin-v0.4.26.zip') as old:
            raw = new.read('plugin.json')
            current, prior = json.loads(raw), json.loads(old.read('plugin.json'))
            self.assertIn(f"'0.4.27' => '{sha256(raw).hexdigest()}'".encode('ascii'), gateway)
            self.assertEqual(current['version'], '0.4.27')
            self.assertEqual(current['settings'], prior['settings'])
            self.assertEqual(current['routes'], prior['routes'])
            self.assertEqual(current['preserve_settings_from'], prior['preserve_settings_from'] + ['0.4.26'])
            self.assertCountEqual(new.namelist(), ['plugin.json', *current['files']])
            self.assertEqual({n for n in current['files'] if new.read(n) != old.read(n)}, {'recording.php','store.php','deactivate.php'})
            for name, digest in current['files'].items():
                data = new.read(name)
                self.assertEqual(sha256(data).hexdigest(), digest, name)


class Site28ReleaseTest(unittest.TestCase):
    def test_upgrade_keeps_settings_routes_and_stock_media_assets(self):
        plugin = 'school-classroom-plugin-v0.4.28.zip'
        update = 'baci-plugin-manager-update-v0.1.35.zip'
        full = 'baci-plugin-manager-v0.1.35.zip'
        bundle = 'baci-classroom-site-upgrade-v0.4.28.zip'
        guide = 'START-HERE-fa.txt'
        expected = hashes((ARTIFACTS / 'SHA256SUMS-v0.4.28.txt').read_bytes())
        self.assertEqual(set(expected), {plugin, update, full, bundle})
        for name, digest in expected.items():
            self.assertEqual(sha256((ARTIFACTS / name).read_bytes()).hexdigest(), digest, name)
        with ZipFile(ARTIFACTS / bundle) as outer:
            self.assertCountEqual(outer.namelist(), [plugin, update, guide, 'SHA256SUMS.txt'])
            self.assertEqual(hashes(outer.read('SHA256SUMS.txt')), {
                plugin: expected[plugin], update: expected[update], guide: sha256(outer.read(guide)).hexdigest(),
            })
            for name in (plugin, update):
                self.assertEqual(sha256(outer.read(name)).hexdigest(), expected[name], name)
            self.assertEqual(outer.read(guide), (ROOT / 'docs/baci-classroom-site-v0.4.28.fa.txt').read_bytes())
        with ZipFile(ARTIFACTS / update) as updater, ZipFile(ARTIFACTS / full) as complete, \
                ZipFile(ARTIFACTS / 'baci-plugin-manager-update-v0.1.34.zip') as previous:
            self.assertCountEqual(updater.namelist(), previous.namelist())
            self.assertEqual([n for n in updater.namelist() if updater.read(n) != previous.read(n)], [GATEWAY])
            self.assertTrue(all(updater.read(n) == complete.read(n) for n in updater.namelist()))
            gateway = updater.read(GATEWAY)
            self.assertEqual(gateway, (ROOT / 'plugin-manager' / GATEWAY).read_bytes())
            self.assertIn(b"const MPM_VERSION = '0.1.35';", gateway)
        with ZipFile(ARTIFACTS / plugin) as new, ZipFile(ARTIFACTS / 'school-classroom-plugin-v0.4.27.zip') as old:
            raw = new.read('plugin.json')
            current, prior = json.loads(raw), json.loads(old.read('plugin.json'))
            self.assertIn(f"'0.4.28' => '{sha256(raw).hexdigest()}'".encode('ascii'), gateway)
            self.assertEqual(current['version'], '0.4.28')
            self.assertEqual(current['settings'], prior['settings'])
            self.assertEqual(current['routes'], prior['routes'])
            self.assertEqual(current['preserve_settings_from'], prior['preserve_settings_from'] + ['0.4.27'])
            self.assertCountEqual(new.namelist(), ['plugin.json', *current['files']])
            self.assertEqual({n for n in current['files'] if new.read(n) != old.read(n)}, {'activity.php','lesson.php','assets/classroom.js'})
            for name, digest in current['files'].items():
                data = new.read(name)
                self.assertEqual(sha256(data).hexdigest(), digest, name)
                source = ROOT / 'school-addon/upload/online-classroom' / name
                if source.is_file():
                    self.assertEqual(data, source.read_bytes(), name)


if __name__ == '__main__':
    unittest.main()
