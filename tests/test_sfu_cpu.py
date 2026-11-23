"""LiveKit CPU diagnostics and isolated maintenance/rollback fixtures."""
from contextlib import redirect_stderr, redirect_stdout
from fractions import Fraction
from io import StringIO
import json
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from test_webhook_setup import DEPLOYMENT, installer


LIVEKIT = 'path\tgithub.com/livekit/livekit-server/cmd/server'


class CpuProbeTest(unittest.TestCase):
    def test_fresh_unit_unsets_the_pin_without_enlarging_its_allocation(self):
        fresh = installer.sfu_unit_config()
        self.assertIn('\nUnsetEnvironment=GOMAXPROCS\n', fresh)
        self.assertNotIn('\nEnvironment=GOMAXPROCS=', fresh)
        self.assertNotRegex(fresh, r'CPU(Quota|Affinity|Weight)=|AllowedCPUs=')
        self.assertEqual(installer.stock_sfu_unit(fresh), (False, False))
        self.assertEqual(installer.stock_sfu_unit(installer.sfu_unit_config(one_core=True)), (False, True))

    def test_reads_build_record_without_a_go_toolchain_or_execution(self):
        def field(data):
            self.assertLess(len(data), 128)
            return bytes([len(data)]) + data

        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'livekit-server'
            metadata = bytes.fromhex('3077af0c9274080241e1c107e6d618e6') + (LIVEKIT + '\n').encode() + bytes.fromhex('f932433186182072008242104116d8f2')
            header = b'\xff Go buildinf:' + bytes([8, 2]) + bytes(16)
            binary.write_bytes(b'\x7fELF' + bytes(12) + header + b'\x00\x00' + bytes(14) + header
                               + field(b'go1.27.3') + field(metadata))
            binary.chmod(0o600)  # not executable
            self.assertEqual(installer.go_build_info(binary), ('go1.27.3', [LIVEKIT]))
            binary.write_bytes(binary.read_bytes()[:-3])
            self.assertEqual(installer.go_build_info(binary), (None, []))

    def test_reads_the_multibyte_build_record_in_a_real_go_elf(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp, ZipFile(root / 'artifacts/baci-classroom-recorder-v0.4.23.zip') as rec:
            binary = Path(rec.extract('baci-native-receiver', tmp))
            version, metadata = installer.go_build_info(binary)
            self.assertEqual(version, 'go1.26.2')
            self.assertIn('path\tbacirat.local/classroom/native', metadata)
            self.assertEqual(installer.go_cpu_policy(version, metadata, ''), (None, None, None))

    def test_go_toolchain_and_runtime_settings_are_both_checked(self):
        policy = installer.go_cpu_policy
        self.assertEqual(policy('go1.24.7', [LIVEKIT], ''), ('go1.24.7', 24, False))
        self.assertEqual(policy('go1.25.1', [LIVEKIT], ''), ('go1.25.1', 25, True))
        self.assertEqual(policy('go1.27.3', [LIVEKIT], 'updatemaxprocs=0'), ('go1.27.3', 27, False))
        legacy_defaults = [LIVEKIT, 'build\tDefaultGODEBUG=containermaxprocs=0,updatemaxprocs=0']
        self.assertEqual(policy('go1.27.3', legacy_defaults, 'containermaxprocs=1'), ('go1.27.3', 27, False))
        self.assertEqual(policy('go1.27.3', legacy_defaults, 'containermaxprocs=0,containermaxprocs=1,updatemaxprocs=1'), ('go1.27.3', 27, True))

    def test_v2_includes_ancestors_and_requires_a_visible_hierarchy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'cpu groups'
            unit = root / 'system.slice' / 'sfu.service'
            unit.mkdir(parents=True)
            (unit / 'cpu.max').write_text('max 100000\n')
            (unit.parent / 'cpu.max').write_text('250000 100000\n')
            mount = f"30 23 0:25 / {root.as_posix().replace(' ', chr(92) + '040')} rw - cgroup2 cgroup2 rw\n"
            member = '0::/system.slice/sfu.service\n'
            self.assertEqual(installer.cgroup_cpu_limits(member, mount), (2, None, Fraction(5, 2)))
            (unit / 'cpu.max').write_text('150000 100000\n')
            self.assertEqual(installer.cgroup_cpu_limits(member, mount), (2, Fraction(3, 2), Fraction(3, 2)))
            with self.assertRaisesRegex(ValueError, 'not visible'):
                installer.cgroup_cpu_limits(member, mount.replace(' / ', ' /system.slice '))
            self.assertEqual(installer.automatic_procs(8, Fraction(5, 2)), 3)
            self.assertEqual(installer.automatic_procs(8, Fraction(1, 2)), 2)
            self.assertEqual(installer.automatic_procs(1, Fraction(5, 2)), 1)

    def test_v1_prefers_the_cpu_controller_in_a_hybrid_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = root / 'sfu.service'
            unit.mkdir()
            for group, quota in ((root, 175000), (unit, -1)):
                (group / 'cpu.cfs_quota_us').write_text(str(quota))
                (group / 'cpu.cfs_period_us').write_text('100000')
            mount = f'30 23 0:25 / {root} rw - cgroup cgroup rw,cpu,cpuacct\n'
            member = '0::/unified/sfu\n3:memory:/other\n2:cpu,cpuacct:/sfu.service\n'
            self.assertEqual(installer.cgroup_cpu_limits(member, mount), (1, None, Fraction(7, 4)))


class CpuMaintenanceTest(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.cfg = root / 'etc'
        systemd = root / 'systemd'
        self.cfg.mkdir()
        systemd.mkdir()
        self.enterContext(patch.multiple(installer, CFG=self.cfg, APP=root / 'app', SYSTEMD=systemd))
        self.enterContext(patch.object(installer, 'require_media_sudo'))
        self.enterContext(patch.object(installer.os, 'fchown'))
        admission = self.cfg / 'admission.json'
        admission.write_text(json.dumps(DEPLOYMENT))
        admission.chmod(0o640)
        self.yaml = installer.engine_config('203.0.113.50', DEPLOYMENT['api_key'], DEPLOYMENT['api_secret'])
        (self.cfg / 'livekit.yaml').write_text(self.yaml)
        self.path = systemd / installer.SFU_SERVICE
        self.original = installer.sfu_unit_config(legacy=True, one_core=True)
        self.wanted = installer.sfu_unit_config(legacy=True)
        self.path.write_text(self.original)
        self.path.chmod(0o644)
        self.report = dict(pid=123, stock_path=True, layout='stock-one-core', version='go1.27.3', minor=27, auto=True,
                           pin=b'1', affinity=4, cgroup=2, leaf=Fraction(3), hard=Fraction(3))
        self.snapshot = self.enterContext(patch.object(installer, 'sfu_cpu_snapshot', side_effect=lambda: self.report.copy()))
        self.props = self.enterContext(patch.object(installer, 'sfu_properties', side_effect=lambda *_: {
            'FragmentPath': str(self.path), 'DropInPaths': self.dropins, 'ActiveState': 'active',
            'NeedDaemonReload': 'no', 'MainPID': str(self.report['pid'])}))
        self.dropins = ''
        self.rpc = self.enterContext(patch.object(installer, 'local_rooms', return_value=[]))
        self.systemctl = self.enterContext(patch.object(installer.subprocess, 'run', side_effect=self.run_command))
        self.output = self.enterContext(redirect_stdout(StringIO()))

    def run_command(self, command, **_options):
        if command == ['systemctl', 'restart', installer.SFU_SERVICE]:
            auto = self.path.read_text() == self.wanted
            self.report.update(pid=self.report['pid'] + 1, pin=None if auto else b'1',
                               layout='stock-auto' if auto else 'stock-one-core')
        else:
            self.assertEqual(command, ['systemctl', 'daemon-reload'])

    def apply(self):
        installer.main(['cpu', '--apply', '--restart'])

    def backups(self):
        return sorted(self.cfg.glob('*.before-cpu-*.bak'))

    def test_inspection_is_read_only_and_masks_nonnumeric_environment(self):
        self.report.update(pin=b'Not_For_Logs', version='go1.24.4', minor=24, auto=False, hard=Fraction(13, 5))
        installer.main(['cpu'])
        out = self.output.getvalue()
        self.assertIn('livekit_go=go1.24.4', out)
        self.assertIn('hierarchy_quota_cpus=2.6', out)
        self.assertIn('legacy_gomaxprocs_start=2', out)
        self.assertNotIn('Not_For_Logs', out)
        self.assertNotIn(DEPLOYMENT['api_secret'], out)
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.path.read_text(), self.original)
        self.systemctl.assert_not_called()
        self.rpc.assert_not_called()
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            installer.main(['cpu', '--apply'])

    def test_only_stock_unit_is_changed_and_apply_is_idempotent(self):
        self.apply()
        self.assertEqual(self.path.read_text(), self.wanted)
        self.assertEqual(self.wanted, self.original.replace('Environment=GOMAXPROCS=1', 'UnsetEnvironment=GOMAXPROCS'))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o644)
        self.assertEqual((self.cfg / 'livekit.yaml').read_text(), self.yaml)
        self.assertIn('  max_participants: 4\n', self.yaml)
        self.assertIn('  auto_create: false\n', self.yaml)
        [backup] = self.backups()
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(backup.read_text(), self.original)
        self.assertIn('GOMAXPROCS=unset', self.output.getvalue())
        self.apply()
        self.assertIn('changed=no restarted=no', self.output.getvalue())
        self.assertEqual(self.systemctl.call_count, 2)
        self.assertEqual(self.rpc.call_count, 3)  # preflight, recheck before restart, health

    def test_busy_custom_build_disabled_defaults_and_hidden_cpu_limit_refuse(self):
        for update in ({'auto':False}, {'stock_path':False}, {'cgroup':None}, {'hard':Fraction(1)}, {'pin':b'4'}):
            with self.subTest(report=update), patch.dict(self.report, update), self.assertRaises(SystemExit):
                self.apply()
        self.dropins = '/else/override.conf'
        with self.assertRaisesRegex(SystemExit, 'without overrides'):
            self.apply()
        self.dropins = ''
        self.rpc.return_value = [{'name': 'Not_For_Logs'}]
        with self.assertRaisesRegex(SystemExit, 'open rooms'):
            self.apply()
        self.assertNotIn('Not_For_Logs', self.output.getvalue())
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.path.read_text(), self.original)
        self.systemctl.assert_not_called()

    def test_room_appearing_at_the_second_check_does_not_restart(self):
        self.rpc.side_effect = [[], [{'name':'synthetic'}]]
        with self.assertRaisesRegex(SystemExit, 'previous unit restored'):
            self.apply()
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual([call.args[0][1] for call in self.systemctl.call_args_list], ['daemon-reload', 'daemon-reload'])

    def test_failed_restart_restores_unit_and_recovers_only_the_sfu(self):
        def fail_first_restart(command, **options):
            if command[1] == 'restart' and self.path.read_text() == self.wanted:
                raise subprocess.CalledProcessError(1, command)
            self.run_command(command, **options)
        self.systemctl.side_effect = fail_first_restart
        with self.assertRaisesRegex(SystemExit, 'previous unit restored'):
            self.apply()
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual([call.args[0][1] for call in self.systemctl.call_args_list],
                         ['daemon-reload', 'restart', 'daemon-reload', 'restart'])

    def test_after_restart_an_unexpected_pin_triggers_rollback(self):
        self.snapshot.side_effect = [self.report.copy(), self.report | {'pid':124, 'pin':b'1'}]
        with self.assertRaisesRegex(SystemExit, 'previous unit restored'):
            self.apply()
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual(self.report['pin'], b'1')


if __name__ == '__main__':
    unittest.main()
