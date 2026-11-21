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
        self.assertAlmostEqual(tx, 6.48 + .18 + .11 + 1.2)
        self.assertAlmostEqual(rx, .57 + .09 + .11 + .75)
        self.assertEqual(client, 162)

    def test_room_and_single_picture_limits(self):
        for value in ('3', '150', '50:51', '50:1:49:2', '50:1:50', '50:1:-1'):
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
