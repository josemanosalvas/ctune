#!/usr/bin/env python3
"""Offline tests for the mprime workload: prime.txt per mode and the verdict parser. Fixtures are real mprime 30.19b20
output captured on this machine (2026-10-25), plus failure lines quoted from the mprime source (commonb.c).

Run from the repo root:  python3 -B -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CTUNE_DATA", tempfile.mkdtemp(prefix="ctune-test-data-"))

from ctune.cpu import mprime  # noqa: E402

STDOUT_OK = """[Main thread Oct 25 11:46] Starting worker.
[Worker Oct 25 11:46] Worker starting
[Worker Oct 25 11:46] Beginning a continuous torture test on your computer.
[Worker Oct 25 11:46] Test 1, 200 Lucas-Lehmer in-place iterations of M280000001 using FFT length 17920K, Pass1=3584, Pass2=5K, clm=4.
[Worker Oct 25 11:46] Self-test 17920K passed!
[Worker Oct 25 11:46] Test 1, 400 Lucas-Lehmer in-place iterations of M140000001 using FFT length 8960K, Pass1=896, Pass2=10K, clm=4.
[Main thread Oct 25 11:47] Stopping all worker windows.
[Worker Oct 25 11:47] Torture Test completed 1 tests in 0 minutes - 0 errors, 0 warnings.
[Worker Oct 25 11:47] Worker stopped.
[Main thread Oct 25 11:47] Execution halted.
"""
RESULTS_OK = "[Sun Oct 25 11:46:39 2026]\nSelf-test 17920K passed!\n"
RESULTS_FAIL = ("[Sun Oct 25 11:46:39 2026]\nSelf-test 17920K passed!\n"
                "FATAL ERROR: Rounding was 0.4999999999, expected less than 0.4\n"
                "Hardware failure detected running 8960K FFT size, consult stress.txt file.\n")
STDOUT_FAIL = STDOUT_OK.replace("0 errors, 0 warnings", "1 errors, 0 warnings").replace(
    "Worker stopped.", "TORTURE TEST FAILED on worker #1.")


class PrimeTxt(unittest.TestCase):
    def test_sse_huge_matches_corecycler_default(self):
        keys = dict(l.split("=") for l in mprime.prime_txt("sse-huge").splitlines())
        self.assertEqual(keys["EnableSetAffinity"], "0")
        self.assertEqual(keys["TortureHyperthreading"], "0")
        self.assertEqual((keys["MinTortureFFT"], keys["MaxTortureFFT"]), ("8960", "32768"))
        self.assertEqual((keys["CpuSupportsAVX"], keys["CpuSupportsFMA3"], keys["TortureWeak"]), ("0", "0", "1097728"))
        self.assertEqual((keys["UsePrimenet"], keys["TortureTime"], keys["NumWorkers"]), ("0", "1", "1"))

    def test_avx2_mode(self):
        keys = dict(l.split("=") for l in mprime.prime_txt("avx2-1344k").splitlines())
        self.assertEqual((keys["CpuSupportsAVX2"], keys["CpuSupportsFMA3"], keys["TortureWeak"]), ("1", "1", "1048576"))
        self.assertEqual((keys["MinTortureFFT"], keys["MaxTortureFFT"]), ("1344", "1344"))


class Parse(unittest.TestCase):
    def test_pass(self):
        r = mprime.parse(RESULTS_OK, STDOUT_OK, "sse-huge", stopped=True, rc=0)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["passed"], r["passed_sizes"], r["tests_completed"], r["isa_ok"]), (1, [17920], 1, True))

    def test_fatal_error_is_red_and_names_the_size(self):
        r = mprime.parse(RESULTS_FAIL, STDOUT_FAIL, "sse-huge", stopped=False, rc=0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["failed_size_k"], 8960)
        self.assertTrue(any("FATAL ERROR" in e for e in r["errors"]))
        self.assertTrue(any("exited on its own" in e for e in r["errors"]))

    def test_exit_zero_alone_proves_nothing(self):
        self.assertFalse(mprime.parse("", "", "sse-huge", stopped=False, rc=0)["ok"])

    def test_wrong_isa_is_red(self):
        out = STDOUT_OK.replace("using FFT length", "using FMA3 FFT length")
        r = mprime.parse(RESULTS_OK, out, "sse-huge", stopped=True, rc=0)
        self.assertFalse(r["ok"])
        self.assertFalse(r["isa_ok"])

    def test_avx2_requires_fma3(self):
        self.assertFalse(mprime.parse(RESULTS_OK, STDOUT_OK, "avx2-1344k", stopped=True, rc=0)["ok"])
        out = STDOUT_OK.replace("using FFT length", "using FMA3 FFT length")
        self.assertTrue(mprime.parse(RESULTS_OK, out, "avx2-1344k", stopped=True, rc=0)["ok"])

    def test_warning_in_summary_is_red(self):
        out = STDOUT_OK.replace("0 errors, 0 warnings", "0 errors, 1 warnings")
        res = RESULTS_OK + "Possible hardware failure, consult readme.txt file, restarting test.\n"
        self.assertFalse(mprime.parse(res, out, "sse-huge", stopped=True, rc=0)["ok"])

    def test_stdout_errors_word_does_not_trip_catch_all(self):
        self.assertTrue(mprime.parse(RESULTS_OK, STDOUT_OK, "sse-huge", stopped=True, rc=0)["ok"])

    def test_threaded_pass_line(self):
        self.assertEqual(mprime.passed_sizes("Self-test 1344K (thread 1 of 2) passed!\n"), [1344])


if __name__ == "__main__":
    unittest.main()
