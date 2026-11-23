"""Read-only diagnostics: no live firewall commands or network probes."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('vps_report', Path(__file__).resolve().parents[1]/'scripts/vps-classroom-report.py')
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


class VPSReportTest(unittest.TestCase):
    def test_udp_and_softnet_fields_are_independent_and_missing_is_unknown(self):
        source = ('Tcp: InErrors\nTcp: 100000\n'
                  'Udp: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors SndbufErrors\n'
                  'Udp: 99 1 2 101 3 4\nUdpLite: InErrors\nUdpLite: 999')
        self.assertEqual(report.udp4_fields(source),{'NoPorts':1,'InErrors':2,'RcvbufErrors':3,'SndbufErrors':4})
        self.assertEqual(report.udp4_fields(source.replace('3 4','3')), {})
        self.assertEqual(report.softnet_fields('be 0a 00 00\n0f 02 12 00'),{'drop':12,'squeeze':18})
        self.assertEqual(report.softnet_fields(''),{})
        self.assertEqual(report.counter_delta(42,50),8)
        self.assertEqual(report.counter_delta(50,1),'na')
        self.assertEqual(report.counter_delta(None,5),'na')

    def test_local_listener_summary_does_not_expose_addresses(self):
        text = ('udp UNCONN 0 0 0.0.0.0:7882 0.0.0.0:*\n'
                'tcp LISTEN 0 32 [::1]:7880 [::]:*\n'
                'tcp LISTEN 0 32 127.0.0.1:7883 0.0.0.0:*\n'
                'tcp LISTEN 0 32 192.0.2.9:7881 0.0.0.0:*\n'
                'udp UNCONN 0 0 [::%eth0]:443 [::]:*\n')
        self.assertEqual(report.listener_scope(text,'udp',7882),'wildcard')
        self.assertEqual(report.listener_scope(text,'tcp',7880),'loopback-only')
        self.assertEqual(report.listener_scope(text,'tcp',7883),'loopback-only')
        self.assertEqual(report.listener_scope(text,'tcp',7881),'non-loopback')
        self.assertEqual(report.listener_scope(text,'udp',443),'wildcard')
        self.assertEqual(report.listener_scope(text,'udp',3478),'absent')
        self.assertEqual(report.listener_scope(None,'udp',7882),'unknown')
