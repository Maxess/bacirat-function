import importlib.util
from pathlib import Path
import sys
import unittest

spec = importlib.util.spec_from_file_location('classroom_budget', Path(__file__).resolve().parents[1] / 'scripts/classroom-budget.py')
budget = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = budget
spec.loader.exec_module(budget)


class ClassroomBudgetTest(unittest.TestCase):
    def test_one_speaker_per_room_billed_both_directions(self):
        seats, audio_copies, tx, rx, client = budget.traffic([budget.Room(50)] * 3)
        self.assertEqual((seats, audio_copies, tx, rx, client), (150, 147, 6.48, .57, 52))
        self.assertAlmostEqual(budget.volume(tx + rx, 90, 15), 71.38125)
        self.assertAlmostEqual(budget.volume(tx, 90, 15), 65.61)
        self.assertAlmostEqual(budget.volume(tx + rx, 90, 8), 38.07)

    def test_marginal_video_and_colocation(self):
        _, _, tx, rx, client = budget.traffic([budget.room_arg('50:1:2:1'), budget.Room(50), budget.Room(50)], site=True)
        self.assertAlmostEqual(tx, 6.48 + .18 + .04 + 1.2)
        self.assertAlmostEqual(rx, .57 + .09 + .04 + .75)
        self.assertEqual(client, 182)

    def test_room_and_viewer_limits(self):
        for value in ('3', '150', '50:51', '50:1:50', '50:1:-1', '50:1:49:50', '50:1:49:49:0', '50:1:49:49:51', '50:1:49:49:50:0', '50:1:49:49:2:3', '50:1:49:49:2:-1'):
            with self.subTest(value=value), self.assertRaises(budget.argparse.ArgumentTypeError):
                budget.room_arg(value)

    def test_eight_day_screen_fanout(self):
        for viewers, expected in [(10, 54.108), (49, 110.97)]:
            with self.subTest(viewers=viewers):
                _, _, tx, rx, client = budget.traffic([budget.Room(50, screen=viewers)] * 3)
                self.assertAlmostEqual(budget.volume(tx + rx, 90, 8), expected)
                self.assertEqual(client, 142)
        _, _, tx, rx, _ = budget.traffic([budget.Room(50, talkers=2)] * 3)
        self.assertAlmostEqual(budget.volume(tx + rx, 90, 8), 70.47)

    def test_two_webcams_and_screen_are_simultaneous(self):
        rooms = [budget.room_arg('50:1:49:49:2')] * 3
        _, _, tx, rx, client = budget.traffic(rooms)
        self.assertAlmostEqual(tx, 31.47)
        self.assertAlmostEqual(rx, 1.08)
        self.assertAlmostEqual(budget.volume(tx + rx, 90, 8), 175.77)
        self.assertEqual(client, 222)
        _, _, tx, rx, client = budget.traffic([budget.room_arg('50:1:0:0:2')])
        self.assertEqual((tx, rx, client), (2.16, .27, 52), 'unwatched webcams still send to the server')

    def test_staff_camera_does_not_inherit_pupil_bitrate(self):
        rooms = [budget.room_arg('50:1:49:49:2:1')] * 3
        _, _, tx, rx, client = budget.traffic(rooms)
        self.assertAlmostEqual(tx, 35.88)
        self.assertAlmostEqual(rx, 1.17)
        self.assertAlmostEqual(budget.volume(tx + rx, 90, 8), 200.07)
        self.assertEqual(client, 252)
        _, _, tx, rx, client = budget.traffic(rooms, site=True)
        self.assertAlmostEqual(tx, 37.08)
        self.assertAlmostEqual(rx, 1.92)
        self.assertAlmostEqual(budget.volume(tx + rx, 90, 8), 210.60)
        self.assertEqual(client, 252, 'the client has website traffic even when PHP is elsewhere')

    def test_all_cameras_exclude_self_and_include_staff(self):
        rooms = [budget.room_arg('50:1:49:49:50:1')] * 3
        _, _, tx, rx, client = budget.traffic(rooms)
        self.assertAlmostEqual(tx, 318.12)
        self.assertAlmostEqual(rx, 6.93)
        self.assertEqual(client, 2132)
