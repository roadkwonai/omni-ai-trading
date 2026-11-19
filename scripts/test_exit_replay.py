import unittest

import exit_replay as r


class WalkTests(unittest.TestCase):
    def test_each_rule_on_one_path(self):
        up = [(101.2, 100.0, 101.0), (102.5, 101.0, 102.0), (103.0, 101.0, 101.5)]
        self.assertEqual(r.walk(up, 100, 'split'), 1.5)                  # +1% half, +2% rest
        self.assertEqual(r.walk(up, 100, 'target', target=1.2), 1.2)     # full exit at the data target
        self.assertEqual(r.walk(up, 100, 'trend'), 1.728)                # +2% half, rest at 103 x 0.985 = 101.455
        down = [(100.5, 97.9, 98.0)]
        self.assertEqual(r.walk(down, 100, 'split'), -2.0)               # stop
        self.assertEqual(r.walk([(100.4, 99.8, 100.2)], 100, 'target', target=1.0), 0.2)  # flat at the last close


if __name__ == '__main__':
    unittest.main()
