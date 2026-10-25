#!/usr/bin/env python3
"""Offline tests for the shared test runner: stressapptest and y-cruncher parsers, the load runner, the CLI glue.

Run from the repo root:  python3 -B -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("CTUNE_DATA", tempfile.mkdtemp(prefix="ctune-test-data-"))

from ctune.core import stress  # noqa: E402

SAT_PASS = """2026/10/25-10:21:31(CET) Stats: Found 0 hardware incidents
2026/10/25-10:21:31(CET) Stats: Completed: 895390.00M in 20.01s 44754.95MB/s, with 0 hardware incidents, 0 errors
2026/10/25-10:21:31(CET) Status: PASS - please verify no corrected errors
"""
SAT_FAIL = """2026/10/25-10:21:20(CET) Report Error: miscompare : DIMM Unknown : 1 : 571s
2026/10/25-10:21:20(CET) Hardware Error: miscompare on CPU 3(0x8) at 0x7f00 (0x0:DIMM Unknown): read:0x5a5a, reread:0x5a5a expected:0x5a4a
2026/10/25-10:21:31(CET) Stats: Completed: 895390.00M in 20.01s 44754.95MB/s, with 1 hardware incidents, 1 errors
2026/10/25-10:21:31(CET) Status: FAIL - test discovered HW problems
"""
YC_PASS = ("Running BKT:                \rRunning BKT: Passed  Test Speed:  7.35 * 10^09  bits / sec   \r"
           "Running BKT: Passed  Test Speed:  7.35 * 10^09  bits / sec\n"
           "Running FFTv4: Passed  Test Speed:  2.17 * 10^09  bits / sec\n"
           "Insufficient permissions to set thread priority. Please retry as root.\n"
           "  6   Stop on Error:      Enabled\n")
YC_FAIL = YC_PASS + "Running N63: Failed  Error Detected\n"


class Parsers(unittest.TestCase):
    def test_sat_pass(self):
        r = stress.parse_sat(0, SAT_PASS, False)
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["mb_s"], 44754.95)

    def test_sat_fail(self):
        r = stress.parse_sat(1, SAT_FAIL, False)
        self.assertFalse(r["ok"])
        self.assertEqual(r["hardware_incidents"], 1)
        self.assertTrue(any("miscompare" in e for e in r["errors"]))

    def test_sat_stopped_early(self):
        self.assertFalse(stress.parse_sat(-2, "Log: Seconds remaining: 10\n", True)["ok"])

    def test_ycruncher_pass(self):
        r = stress.parse_ycruncher(-2, YC_PASS, True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["passed"], {"BKT": 1, "FFTv4": 1})

    def test_ycruncher_fail(self):
        r = stress.parse_ycruncher(1, YC_FAIL, False)
        self.assertFalse(r["ok"])
        self.assertTrue(r["errors"])

    def test_ycruncher_commands(self):
        cmd = stress.ycruncher_cmd(12000, 2700)
        self.assertEqual(cmd[1:], ["stress", "-M:12000M", "-D:337", "-TL:2700"])


def _ok(rc, out, stopped):
    return {"ok": rc == 0 or stopped, "rc": rc}


class Loads(unittest.TestCase):
    def test_runs_and_stops(self):
        r = stress.run_loads([stress.Load("sleeper", ["sleep", "60"], _ok)], 2, check_every=0.5)
        self.assertIsNone(r["early_exit"])
        self.assertTrue(r["loads"][0]["ok"])
        self.assertLess(r["seconds"], 40)

    def test_early_exit_is_red(self):
        r = stress.run_loads([stress.Load("quitter", ["false"], _ok), stress.Load("sleeper", ["sleep", "60"], _ok)],
                             10, check_every=0.5)
        self.assertEqual(r["early_exit"], "quitter")
        self.assertFalse(r["loads"][0]["ok"])
        self.assertLess(r["seconds"], 40)

    def test_self_timed_load_finishes_on_its_own(self):
        ld = stress.Load("timed", ["sleep", "3"], _ok, self_timed=True)
        r = stress.run_loads([ld], 2, check_every=0.5)
        self.assertIsNone(r["early_exit"])
        self.assertFalse(ld.stopped)
        self.assertTrue(r["loads"][0]["ok"])


class OptionalLoad(unittest.TestCase):
    def test_optional_helper_that_cannot_start_is_skipped(self):
        def unavailable(rc, out, stopped):
            return {"ok": False, "unavailable": True, "errors": ["could not allocate"]}
        r = stress.run_loads([stress.Load("heat", ["false"], unavailable, optional=True),
                              stress.Load("main", ["sleep", "60"], _ok)], 2, check_every=0.5)
        self.assertIsNone(r["early_exit"])
        heat = r["loads"][0]
        self.assertTrue(heat["ok"] and heat["skipped"])

    def test_optional_helper_with_real_errors_stays_red(self):
        def broken(rc, out, stopped):
            return {"ok": False, "unavailable": False, "errors": ["mismatch at 0x10"]}
        r = stress.run_loads([stress.Load("heat", ["false"], broken, optional=True)], 1, check_every=0.5)
        self.assertFalse(r["loads"][0]["ok"])


class Sizing(unittest.TestCase):
    def test_size_leaves_reserve(self):
        real = stress.meminfo
        stress.meminfo = lambda: {"MemAvailable": 17000, "MemTotal": 31000}
        try:
            self.assertEqual(stress.test_size_mb(reserve_mb=1536), 15464)
            self.assertEqual(stress.test_size_mb(reserve_mb=2048, share=0.9), 13456)
            stress.meminfo = lambda: {"MemAvailable": 1000}
            self.assertEqual(stress.test_size_mb(), 256)
        finally:
            stress.meminfo = real


if __name__ == "__main__":
    unittest.main()
