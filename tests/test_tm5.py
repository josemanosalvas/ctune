#!/usr/bin/env python3
"""Offline tests for TestMem5: the log parser, the window that fits the free RAM, the profile rewrite, phase ending.

Run from the repo root:  python3 -B -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402,F401
os.environ.setdefault("CTUNE_DATA", tempfile.mkdtemp(prefix="ctune-test-data-"))

from ctune.core import stress  # noqa: E402
from ctune.mem import tm5  # noqa: E402

HEAD = ("========= TestMem5 Log File =========\r\nCustomize: Extreme1 @anta777\r\n"
        "Start testing at 20:17, 1.3Gb x16\r\n")
PROFILE = ("[Global Memory Setup]\nChannels=2\n"
           "Testing Window Size (Mb)=1408 ; еще лучше 1536\n"
           "Lock Memory Granularity (Mb)=16\n")


class Log(unittest.TestCase):
    def test_clean_run(self):
        r = tm5.parse_log(HEAD + "The testing is completed, of errors is not detected.\r\n")
        self.assertEqual((r["completed"], r["errors_reported"], r["threads"], r["window"]), (True, 0, 16, "1.3Gb"))
        self.assertEqual(r["errors"], [])

    def test_errors(self):
        r = tm5.parse_log(HEAD + "The testing is completed, is revealed 3 of errors!\r\n")
        self.assertEqual((r["completed"], r["errors_reported"]), (True, 3))

    def test_running(self):
        r = tm5.parse_log(HEAD)
        self.assertEqual((r["started"], r["completed"], r["errors_reported"]), (True, False, None))

    def test_fatal(self):
        r = tm5.parse_log(HEAD + "Memory Manager #3 not started.\r\nFatal error, programm stopped!\r\n")
        self.assertEqual(len(r["fatal"]), 2)


class Window(unittest.TestCase):
    def test_keeps_the_profile_window_when_it_fits(self):
        self.assertEqual(tm5.window_mb(32000, 16, 1408), 1408)

    def test_lowers_it_to_fit(self):
        w = tm5.window_mb(21000, 16, 1408)
        self.assertEqual(w % 16, 0)
        self.assertLessEqual(16 * (w + tm5.PER_PROCESS_MB), 21000)

    def test_too_little_ram(self):
        self.assertEqual(tm5.window_mb(2000, 16, 1408), 0)

    def test_rewrite_sets_only_the_window_in_crlf(self):
        out = tm5.write_cfg(PROFILE, 1280)
        self.assertIn("Testing Window Size (Mb)=1280 ; ", out)
        self.assertEqual(tm5.profile_window_mb(PROFILE), 1408)
        self.assertNotIn("\n", out.replace("\r\n", ""))
        self.assertIn("Lock Memory Granularity (Mb)=16\r\n", out)


def _ok(rc, out, stopped):
    return {"ok": rc == 0 or stopped, "rc": rc}


class EndsPhase(unittest.TestCase):
    def test_exit_ends_the_phase_without_counting_as_early(self):
        main = stress.Load("tm5", ["sleep", "1"], _ok)
        main.ends_phase = True
        heat = stress.Load("heat", ["sleep", "60"], _ok, optional=True)
        r = stress.run_loads([main, heat], 30, check_every=0.5)
        self.assertIsNone(r["early_exit"])
        self.assertTrue(all(l["ok"] for l in r["loads"]))
        self.assertLess(r["seconds"], 20)
        self.assertTrue(heat.stopped)


class LoadResult(unittest.TestCase):
    def _load(self, log, stopped=False, rc=0, unit=None):
        with tempfile.TemporaryDirectory() as d:
            prof = os.path.join(d, "p.cfg")
            with open(prof, "w") as f:
                f.write(PROFILE)
            with mock.patch.object(tm5, "PROFILE", prof):
                ld = tm5.Load(32000, 60, threads=16)
        ld.proc = mock.Mock(returncode=rc)
        ld._reader = mock.Mock()
        ld.stopped, ld.out = stopped, "wine: something\n"
        with mock.patch.object(tm5, "_read", return_value=log), \
                mock.patch.object(stress, "unit_state", return_value=unit or {}):
            return ld.result()

    def test_green(self):
        r = self._load(HEAD + "The testing is completed, of errors is not detected.\r\n")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["tested_mb"], 16 * 1408)

    def test_red_on_errors(self):
        r = self._load(HEAD + "The testing is completed, is revealed 2 of errors!\r\n")
        self.assertFalse(r["ok"])
        self.assertFalse(r.get("unavailable"))
        self.assertIn("2 errors", r["errors"][0])

    def test_never_started_is_unavailable(self):
        r = self._load("", rc=1)
        self.assertTrue(r["unavailable"])
        self.assertFalse(r["ok"])

    def test_out_of_memory_is_unavailable(self):
        r = self._load(HEAD, rc=-9, unit={"Result": "oom-kill", "ActiveState": "inactive"})
        self.assertTrue(r["unavailable"])

    def test_stopped_midway_is_red(self):
        r = self._load(HEAD, stopped=True)
        self.assertFalse(r["ok"])
        self.assertFalse(r.get("unavailable"))


if __name__ == "__main__":
    unittest.main()
