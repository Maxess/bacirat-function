import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import install


class SupervisionInstallationTests(unittest.TestCase):
    def test_prepare_is_idempotent_and_preserves_pilot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, config, units = [root / name for name in ('app','config','units')]
            for path in (app, config, units):
                path.mkdir(mode=0o700)
            pilot = b'# older admission fixture\n'
            gate = app / 'admission.py'
            gate.write_bytes(pilot)
            unchanged = {
                app / 'livekit-server': b'fixture binary',
                config / 'livekit.yaml': b'configured SFU',
                config / 'Caddyfile': b'configured reverse proxy',
                config / 'admission.json': json.dumps({'api_key':'a'*24,'api_secret':'a'*64,'school_origin':'https://school.example.test'}).encode(),
                units / 'school-classroom-admission.service': b'pilot admission unit',
                units / 'school-classroom-sfu.service': b'pilot SFU unit',
            }
            for path, data in unchanged.items(): path.write_bytes(data)
            def temporary_trust(path, kind):
                self.assertTrue(kind(path.lstat().st_mode))
            with patch.multiple(install,APP=app,CFG=config,SYSTEMD=units,PILOT_GATE_SHA256=hashlib.sha256(pilot).hexdigest()), \
                    patch.object(install,'trusted',temporary_trust), patch.object(install,'require_systemd'), \
                    patch.object(install.os,'fchown'), patch.object(install.subprocess,'run') as systemctl, patch('builtins.print'):
                prefix='test-'+'e'*20
                install.install_supervision(prefix,'/school-test')
                install.install_supervision(prefix,'/school-test')
                self.assertEqual([c.args[0] for c in systemctl.call_args_list], [['systemctl','daemon-reload']]*2)
                with self.assertRaises(ValueError): install.install_supervision(prefix,'/another-site')
                self.assertEqual((config/'admission-pilot.py.bak').read_bytes(),pilot)
                self.assertEqual((config/'watch.env').read_text(),f'CLASSROOM_PREFIX={prefix}\nCLASSROOM_SITE_PATH=/school-test\n')
                self.assertEqual(stat.S_IMODE((config/'watch.env').stat().st_mode),0o600)
                self.assertNotEqual(gate.read_bytes(),pilot)
                self.assertTrue((app/'classroom-watch.py').exists())
                self.assertTrue((units/'school-classroom-watch.service').exists())
                self.assertEqual({path:path.read_bytes() for path in unchanged},unchanged)

    def test_reject_symlinks_and_paths_with_parent_segments(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / 'admission.py'
            link.symlink_to('/etc/hosts')
            with self.assertRaises(ValueError): install.trusted(link,stat.S_ISREG)
        with patch.object(install,'require_systemd'):
            for path in ('//external', '/school/../elsewhere', '/school/'):
                with self.assertRaises(ValueError): install.install_supervision('test-'+'a'*20,path)
