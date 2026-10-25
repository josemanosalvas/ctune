#!/usr/bin/env python3
"""Offline tests for the mascot sprites and the status banner.

Run from the repo root:  python3 -B -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ctune import cli, mascot  # noqa: E402

SPRITES = {
    "mascot": mascot.mascot(), "icon": mascot.icon(), "wordmark": mascot.wordmark(), "lockup": mascot.lockup(),
}


class Sprites(unittest.TestCase):
    def test_every_pixel_has_a_colour_and_fits(self):
        for name, s in SPRITES.items():
            for (x, y), c in s.px.items():
                self.assertIn(c, mascot.PAL, name)
                self.assertTrue(0 <= x < s.w and 0 <= y < s.h, name)

    def test_body_silhouette_is_symmetric(self):
        # colours may differ (glint, pupils); the silhouette may not, except the eye-stalks above the body
        for name, body_top in (("mascot", 6), ("icon", 0)):
            s = SPRITES[name]
            filled = {(x, y) for x, y in s.px if y >= body_top}
            self.assertEqual(filled, {(s.w - 1 - x, y) for x, y in filled}, name)

    def test_frames_loop(self):
        frames = mascot.mascot_frames()
        self.assertGreater(len(frames), 4)
        self.assertTrue(all(ms > 0 for _, ms in frames))
        self.assertEqual(len({s.ascii() for s, _ in frames}), 7)


class Banner(unittest.TestCase):
    def test_half_blocks(self):
        lines = mascot.ansi(mascot.icon())
        self.assertEqual(len(lines), 8)

    def test_flags_errors(self):
        calm = {"errors_since_boot": 0, "gpu": {"xid_errors_this_boot": 0}, "board": "B", "bios": "F"}
        self.assertIn("all quiet", cli._banner(calm))
        self.assertIn("ctune errors", cli._banner({**calm, "errors_since_boot": 2}))
        self.assertIn("ctune errors", cli._banner({**calm, "gpu": {"xid_errors_this_boot": 1}}))
        self.assertIn("all quiet", cli._banner({**calm, "gpu": "unavailable: no NVML"}))

if __name__ == "__main__":
    unittest.main()
