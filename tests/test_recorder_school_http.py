"""Compatibility with a school ingress that requires an honest HTTP client identity."""
from contextlib import ExitStack, redirect_stdout
import hashlib
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import socket
import tempfile
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / 'server/recorder'
STOCK = ROOT / 'artifacts/baci-classroom-recorder-v0.4.23.zip'
spec = importlib.util.spec_from_file_location('repair_school_http', REC / 'baci-recorder-http-fix.py')
fix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class TransportTest(unittest.TestCase):
    def test_pins_and_repair_match_the_source_without_rewriting_the_release(self):
        # The repair's setup pin refers to v1.3.1, not the latest installer source.
        self.assertEqual(sha((ROOT / 'server/install.py').read_bytes()), fix.VPS_MEDIA)
        with ZipFile(STOCK) as archive:
            raw = archive.read('transport.py')
            self.assertEqual(sha(archive.read('recording.py')), fix.STOCK_ENTRY)
            self.assertEqual(sha(archive.read('school-classroom-recorder.service')), fix.STOCK_UNIT)
            for name, digest in fix.STOCK_DROPS.items():
                self.assertEqual(sha(archive.read('school-classroom-recorder.service.d/' + name)), digest)
        self.assertEqual(fix.fixed_transport(raw), (REC / 'transport.py').read_bytes())
        with self.assertRaisesRegex(fix.Refused, 'Not the published'):
            fix.fixed_transport(raw + b'\n')

    def test_header_is_sent_on_the_wire_and_does_not_change_the_authenticated_payload(self):
        requests = []

        def exchange(client):
            with client.makefile('rb') as wire:
                first = wire.readline().decode().rstrip('\r\n')
                headers = {}
                while (line := wire.readline()) != b'\r\n':
                    if not line:
                        raise EOFError('incomplete request')
                    name, _, value = line.decode().partition(':')
                    headers[name.lower()] = value.strip()
                data = wire.read(int(headers['content-length']))
                requests.append((first, headers, json.loads(data)))
                ok = headers.get('user-agent') == fix.IDENTITY
                body = json.dumps({'ok': ok, 'release': '0.4.23'}).encode()
                status = b'200 OK' if ok else b'403 Forbidden'
                client.sendall(b'HTTP/1.1 ' + status + b'\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)

        def call(mod, fn):
            front, back = socket.socketpair()
            with front, back:
                front.settimeout(2)
                back.settimeout(2)
                thread = Thread(target=exchange, args=(back,))
                thread.start()
                try:
                    with patch.object(mod.http.client.HTTPSConnection, 'connect', lambda conn: setattr(conn, 'sock', front)):
                        school = mod.School('https://school.test/branch', 'fixture', 'a'*32)
                        return fn(school)
                finally:
                    thread.join(timeout=3)

        with ZipFile(STOCK) as archive:
            raw = archive.read('transport.py')
        old = fix.module('stock', Path('transport.py'), raw)
        new = fix.module('fixed', Path('transport.py'), fix.fixed_transport(raw))
        with self.assertRaises(old.ProtocolError) as denied:
            call(old, lambda school: school('maintenance'))
        self.assertEqual(denied.exception.status, 403)
        self.assertTrue(call(new, lambda school: school('maintenance'))['ok'])
        self.assertEqual(call(new, lambda school: school.document('b'*32, 'room', 'doc', 'bytes=0-8'))[0], 200)
        first, before, payload = requests[0]
        newer, after, unchanged = requests[1]
        self.assertEqual(first, 'POST /branch/plugins/school-plugins.php?plugin=school-classroom&route=recording HTTP/1.1')
        self.assertEqual(newer, first)
        self.assertEqual(unchanged, payload)
        self.assertEqual(payload, {'action':'maintenance','protocol':2,'worker':'a'*32,'id':None})
        self.assertEqual(after, before | {'user-agent': fix.IDENTITY})
        self.assertEqual(after['authorization'], 'Bearer fixture')
        self.assertEqual(requests[2][1]['user-agent'], fix.IDENTITY)
        self.assertEqual(requests[2][1]['range'], 'bytes=0-8')


class RepairTest(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        root = Path(stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        tmp = root / 'recorder'
        cfg = root / 'config'
        sysd = root / 'systemd'
        spool = root / 'spool'
        runtime = root / 'run/config.json'
        prepared = tmp / 'releases/0.4.23'
        for path in (prepared, cfg, sysd / 'recorder.service.d', spool, runtime.parent):
            path.mkdir(mode=0o700, parents=True)
        self.uid = os.geteuid() or 1001
        self.expected = {'site':'https://school.test/branch', 'secret':'x'*32}
        self.config = {'version':'1.3.1', 'profile':'compact', 'school': self.expected['site']}
        self.vps = SimpleNamespace(REC=tmp, REC_PROGRAM=prepared, CFG=cfg, SYSTEMD=sysd, RECORDER='recorder.service',
                                   LIVE_UNITS=('sfu.service', 'admission.service', 'https.service'),
                                   STATE=cfg/'offline-vps.json', PAIR=cfg/'site-pairing.json', REC_CFG=cfg/'recorder.json',
                                   SPOOL=spool, Refused=fix.Refused, json_file=lambda p: json.loads(p.read_text()),
                                   system_preflight=lambda:None, local_checks=lambda conf: self.assertEqual(conf, self.config),
                                   info=lambda *_:'e'*32, run=self.service, write=self.write)
        self.rooms = []
        self.vps.media = SimpleNamespace(local_rooms=lambda _: self.rooms[:], pilot_deployment=lambda:{})
        self.repair = fix.Repair(self.vps)
        self.files = (tmp, cfg, sysd, spool, runtime.parent)
        with ZipFile(STOCK) as archive:
            for name in ('transport.py', 'recording.py'):
                self.write(prepared/name, archive.read(name))
            rec = 'school-classroom-recorder.service'
            self.write(self.repair.unit_path, archive.read(rec))
            for name in fix.STOCK_DROPS:
                self.write(self.repair.drop.parent / name, archive.read(f'{rec}.d/{name}'))
        self.write(self.repair.drop.parent/'80-compact.conf', fix.COMPACT_DROP)
        for path, obj in ((self.vps.STATE, self.config), (self.vps.PAIR, {'site':self.expected['site'], 'recording_secret':self.expected['secret']}),
                          (self.vps.REC_CFG, self.expected), (runtime, self.expected), (spool/'worker.json', {'id':'a'*32})):
            self.write(path, json.dumps(obj), mode=0o600)
        for path in (spool, spool/'worker.json', runtime.parent):
            if not os.geteuid():
                os.chown(path, self.uid, self.uid)
        self.events, self.serial, self.http = [], 1, []
        self.remote = {'ok':True, 'release':'0.4.23', 'idle':True, 'worker_ok':True, 'native_ready':False}
        self.old_code, self.named_code = 403, 200
        self.ready_after_restart = True
        self.busy_after_restart = False
        self.runtime = runtime
        directory = fix.directory
        stack.enter_context(patch.object(fix, 'reviewed', self.reviewed))
        stack.enter_context(patch.object(fix, 'directory', lambda path, uid=os.geteuid(), **kw: directory(path, uid=uid, **kw)))
        stack.enter_context(patch.object(fix, 'vps_helpers', return_value=self.vps))
        stack.enter_context(patch.object(fix, 'RUNTIME', runtime))
        stack.enter_context(patch.object(fix.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=self.uid)))
        self.waited = stack.enter_context(patch.object(fix.time, 'sleep'))
        load = fix.module

        def module(*args):
            compiled = load(*args)
            compiled.json_post = self.school
            return compiled

        stack.enter_context(patch.object(fix, 'module', side_effect=module))
        self.baseline = self.snapshot()

    @staticmethod
    def write(path, data, mode=0o644):
        with path.open('xb') as out:
            out.write(data.encode() if isinstance(data, str) else data)
        path.chmod(mode)

    def reviewed(self, path, digest):
        data = path.read_bytes()
        fix.need(sha(data) == digest, f'Altered fixture {path.name}.')
        return data

    def snapshot(self):
        return {str(p):p.read_bytes() for root in self.files for p in root.rglob('*') if p.is_file()}

    def service(self, program, action, *args, **kwargs):
        self.assertEqual(program, 'systemctl')
        if action == 'show':
            self.assertEqual(args[-1], self.vps.RECORDER)
            names = sorted(self.repair.drop.parent.glob('*.conf'))
            props = {'FragmentPath': str(self.repair.unit_path), 'DropInPaths':' '.join(map(str,names)), 'NeedDaemonReload':'no',
                     'SubState':'running', 'User':'school-recorder', 'InvocationID':f'{self.serial:032x}'}
            return '\n'.join(f'{name}={value}' for name,value in props.items()).encode()
        self.events.append((action, *args))
        if action == 'restart':
            self.assertEqual(args, (self.vps.RECORDER,))
            self.serial += 1
        else:
            self.assertEqual((action,args), ('daemon-reload', ()))

    def school(self, url, data, headers, **kwargs):
        self.assertEqual(url, 'https://school.test/branch/plugins/school-plugins.php?plugin=school-classroom&route=recording')
        self.assertEqual(data, {'action':'maintenance','protocol':2,'worker':'a'*32,'id':None})
        self.assertEqual(headers['Authorization'], 'Bearer ' + self.expected['secret'])
        ua = headers.get('User-Agent')
        self.http.append(ua)
        result = self.remote.copy()
        if self.serial > 1 and self.repair.drop.is_file():
            result.update(native_ready=self.ready_after_restart, idle=not self.busy_after_restart)
        return self.named_code if ua == fix.IDENTITY else self.old_code, {}, json.dumps(result).encode()

    def execute(self, apply=False):
        with redirect_stdout(out := StringIO()):
            fix.run(apply)
        data = out.getvalue()
        self.assertNotIn(self.expected['secret'], data)
        return data

    def test_check_reproduces_the_403_without_writes_or_restart(self):
        self.assertIn('check_only=yes no_changes=yes', self.execute())
        self.assertEqual(self.http, [fix.IDENTITY, None])
        self.assertEqual(self.events, [])
        self.assertEqual(self.snapshot(), self.baseline)

    def test_apply_keeps_installed_bytes_and_verifies_a_new_heartbeat(self):
        result = self.execute(True)
        self.assertIn('school_recorder=ready live_services=unchanged', result)
        self.assertEqual(self.events, [('daemon-reload',), ('restart', self.vps.RECORDER)])
        self.waited.assert_called_once_with(19)
        snapshot = self.snapshot()
        self.assertEqual(set(snapshot) - set(self.baseline), {str(self.repair.payload), str(self.repair.drop)})
        self.assertEqual({name:snapshot[name] for name in self.baseline}, self.baseline)
        self.assertEqual(self.repair.drop.read_text(), f'[Service]\nBindReadOnlyPaths={self.repair.payload}:{self.repair.target}\n')
        self.assertEqual(self.repair.payload.read_bytes(), (REC/'transport.py').read_bytes())
        with self.assertRaisesRegex(fix.Refused, 'Do not reapply'):
            self.execute(True)

    def test_refuse_without_confirmed_idle_identity_and_matching_config(self):
        self.rooms.append({'name':'some-fixture-room'})
        with self.assertRaisesRegex(fix.Refused, 'open rooms'):
            self.execute(True)
        self.rooms.clear()
        self.remote['idle'] = False
        with self.assertRaisesRegex(fix.Refused, 'pending recording'):
            self.execute(True)
        self.remote.update(idle=True, worker_ok=False)
        with self.assertRaisesRegex(fix.Refused, 'different active Recorder'):
            self.execute(True)
        self.remote['worker_ok'] = True
        (job := self.vps.SPOOL / ('b'*32)).mkdir()
        with self.assertRaisesRegex(fix.Refused, 'spool is not empty'):
            self.execute(True)
        job.rmdir()
        self.runtime.write_text(json.dumps(self.expected | {'secret':'mismatch'}))
        with self.assertRaisesRegex(fix.Refused, 'credential does not match'):
            self.execute(True)
        self.assertEqual(self.events, [])

    def test_refuse_when_header_does_not_fix_the_failure_or_is_not_needed(self):
        self.named_code = 403
        with self.assertRaisesRegex(fix.Refused, 'identified Recorder request'):
            self.execute(True)
        self.old_code = self.named_code = 200
        with self.assertRaisesRegex(fix.Refused, 'original request is accepted'):
            self.execute(True)
        self.assertEqual(self.events, [])
        self.assertEqual(self.snapshot(), self.baseline)

    def test_roll_back_unconfirmed_cutover_only_if_still_idle(self):
        self.ready_after_restart = False
        with self.assertRaisesRegex(fix.Refused, 'Original configuration restored'):
            self.execute(True)
        self.assertEqual(self.events, [('daemon-reload',), ('restart', self.vps.RECORDER)] * 2)
        self.assertEqual(self.snapshot(), self.baseline)

    def test_never_restart_again_if_a_new_job_appears(self):
        self.ready_after_restart = False
        self.busy_after_restart = True
        with self.assertRaisesRegex(fix.Refused, 'safe rollback could not be confirmed'):
            self.execute(True)
        self.assertEqual(self.events, [('daemon-reload',), ('restart', self.vps.RECORDER)])
        self.assertTrue(self.repair.drop.is_file())


if __name__ == '__main__':
    unittest.main()
