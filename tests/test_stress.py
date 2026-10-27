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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402,F401  (offline tests may not start podman, root helpers or stress tools)
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


BURN_OK = ("Using compare file: compare.fatbin\nBurning for 20 seconds.\n"
           "Initialized device 0 with 12542 MB of memory (9000 MB available, using 8100 MB of it), using FLOATS\n"
           "Results are 268435456 bytes each, thus performing 29 iterations\n"
           "10.0%  proc'd: 290 (20100 Gflop/s)   errors: 0   temps: 60 C \r"
           "55.0%  proc'd: 3190 (21800 Gflop/s)   errors: 0   temps: 70 C \r"
           "100.0%  proc'd: 5800 (21700 Gflop/s)   errors: 0   temps: 74 C \n"
           "Killing processes with SIGTERM (soft kill)\nFreed memory for dev 0\nUninitted cublas\ndone\n\n"
           "Tested 1 GPUs:\n\tGPU 0: OK\n")
BURN_OOM = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "gpu_burn_oom.txt")).read() \
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "gpu_burn_oom.txt")) else ""


class GpuBurnParse(unittest.TestCase):
    def setUp(self):
        from ctune.gpu import burn
        self.burn = burn

    def test_ok(self):
        r = self.burn.parse(0, BURN_OK, False)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["iterations"], r["final"], r["gflops_median"]), (29, "OK", 21750.0))

    def test_faulty(self):
        bad = BURN_OK.replace("errors: 0   temps: 70", "errors: 12  (WARNING!)  temps: 70").replace("GPU 0: OK",
                                                                                                    "GPU 0: FAULTY")
        r = self.burn.parse(0, bad, False)
        self.assertFalse(r["ok"])
        self.assertFalse(r["unavailable"])

    def test_one_slot_compares_nothing(self):
        r = self.burn.parse(0, BURN_OK.replace("performing 29 iterations", "performing 1 iterations"), False)
        self.assertFalse(r["ok"])

    @unittest.skipUnless(BURN_OOM, "fixture missing")
    def test_out_of_vram_is_unavailable(self):
        r = self.burn.parse(123, BURN_OOM, False)
        self.assertFalse(r["ok"])
        self.assertTrue(r["unavailable"])


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


class ReviewRegressions(unittest.TestCase):
    """Defects found by the 2026-10-25 review, kept fixed."""

    def test_unknown_unit_state_keeps_a_live_marker(self):
        real = stress.unit_state
        stress.unit_state = lambda unit: None          # the user manager cannot be asked
        try:
            stress.marker_write({"boot_id": stress.boot_id(), "pid": os.getpid(), "domain": "cpu", "test": "co-cycle",
                                 "unit": "ctune-run-x.service", "invocation_id": "abc"})
            self.assertIsNotNone(stress.running())
            self.assertIsNone(stress.check_interrupted())
            self.assertTrue(os.path.exists(stress.MARKER))
        finally:
            stress.unit_state = real
            os.remove(stress.MARKER)

    def test_marker_is_claimed_once(self):
        stress.marker_write({"boot_id": "other-boot", "pid": 1, "domain": "mem", "test": "screen"})
        self.assertIsNotNone(stress._claim())
        self.assertIsNone(stress._claim())

    def test_stop_all_reaches_every_load(self):
        loads = [stress.Load(f"s{i}", ["sleep", "60"], _ok) for i in range(3)]
        for ld in loads:
            ld.start()
        stress._stop_all(loads, grace=5)
        self.assertTrue(all(not ld.alive() for ld in loads))

    def test_burn_with_no_compared_batch_is_not_green(self):
        from ctune.gpu import burn
        out = "Results are 268435456 bytes each, thus performing 29 iterations\nTested 1 GPUs:\n\tGPU 0: OK\n"
        self.assertFalse(burn.parse(0, out, False)["ok"])

    def test_burn_name_collision_is_not_called_a_missing_image(self):
        from ctune.gpu import burn
        r = burn.parse(125, "Error: creating container storage: the container name \"ctune-gpu-burn\" is already in "
                            "use by abc\n", False)
        self.assertFalse(any("burn-setup" in e for e in r["errors"]))

    def test_gpu_test_rejects_empty_or_too_short_runs(self):
        from unittest import mock
        from ctune.gpu import api as gpu
        with mock.patch.object(stress, "check_interrupted", side_effect=AssertionError("reached a real run")):
            with self.assertRaises(ValueError):
                gpu.test("core", burn_seconds=0, fp64_seconds=0, tc_seconds=0, transient_seconds=0)
            with self.assertRaises(ValueError):
                gpu.test("core", fp64_seconds=30)
            with self.assertRaises(ValueError):
                gpu.test("screen", burn_seconds=-5)

    def test_mprime_floor(self):
        from ctune.cpu import api as cpu
        with self.assertRaises(ValueError):
            cpu.test([0], seconds_per_core=30, workload="sse-huge")

    def test_detached_start_passes_the_tests_own_kind(self):
        """mem_test and gpu_test take a `kind` that collided with the runner's own (2026-10-27)."""
        import json
        from unittest import mock
        from ctune import mcp_server
        from ctune.core import runner
        failed = mock.Mock(returncode=1, stderr="stubbed")
        with mock.patch.object(stress, "check_interrupted"), mock.patch.object(stress, "running", return_value=None), \
                mock.patch.object(runner, "units", return_value=[]), \
                mock.patch.object(runner.subprocess, "run", return_value=failed) as run:
            with self.assertRaisesRegex(RuntimeError, "systemd-run failed"):
                mcp_server._run("mem_test", None, True, kind="fabric", minutes=None)
        spec = json.loads(run.call_args.args[0][-1])
        self.assertEqual(spec, {"kind": "mem_test", "kwargs": {"kind": "fabric", "minutes": None}})
        self.assertIn(f"--setenv=CTUNE_DATA={stress.DATA}", run.call_args.args[0])   # the unit writes where we read
        inline = mock.Mock(return_value="ran")
        self.assertEqual(mcp_server._run("gpu_test", inline, False, kind="core"), "ran")
        inline.assert_called_once_with(kind="core")

    def test_a_test_blocks_sleep_and_flags_one_that_happens_anyway(self):
        """A 90 min RAM test slept 34 min of it and still reported green (2026-10-27)."""
        import subprocess
        from unittest import mock
        from ctune.core import journal
        holder = subprocess.Popen(["sleep", "60"])
        clock = iter([100.0, 100.0 + 2040])        # BOOTTIME - MONOTONIC at the start, then after a 34 min suspend
        with mock.patch.object(stress, "_inhibit_sleep", return_value=[holder]), \
                mock.patch.object(stress, "active_units", return_value=[]), \
                mock.patch.object(stress, "_slept_s", side_effect=lambda: next(clock)):
            with stress.run("mem", "fabric") as r:
                self.assertEqual(r.state["sleep_blocked_by"], ["sleep"])
        self.assertIsNotNone(holder.poll())      # the lock is released with the test
        note = journal.tail(stress.DATA, 1, "note")[-1]
        self.assertEqual(note["slept_s"], 2040)
        self.assertIn("inconclusive", note["text"])

    def test_gpu_burn_retries_smaller_after_a_startup_out_of_memory(self):
        """NVML's free VRAM is not one allocatable block: a start-up out-of-memory retries 512 MB smaller."""
        from unittest import mock
        from ctune.gpu import api as gpu
        oom = {"ok": False, "errors": ["Couldn't init a GPU test: Error in C alloc (gpu_burn-drv.cpp:200): out of "
                                       "memory"], "gflops_median": None, "name": "gpu-burn fp32"}
        ok = {"ok": True, "errors": [], "gflops_median": 26400.0, "name": "gpu-burn fp32"}
        runs = [{"loads": [oom], "early_exit": "gpu-burn fp32"}, {"loads": [ok], "early_exit": None}]
        with mock.patch.object(gpu.nvml, "memory_mb", return_value=(12288, 10400, 1888)), \
                mock.patch.object(gpu.stress, "run_loads", side_effect=runs) as run, \
                mock.patch.object(gpu.burn, "cleanup"):
            out = gpu._burn("fp32", 600, None)
        self.assertTrue(out["ok"])
        self.assertEqual(out["mb"], 7680)
        self.assertEqual(out["alloc_retries"][0]["mb"], 8192)
        self.assertIn("-m", run.call_args_list[1].args[0][0].cmd)

    def test_vram_failure_keeps_memtest_vulkans_output(self):
        from ctune.gpu import api as gpu
        r = gpu.parse_vram(68, "Standard 5-minute test of 1: RTX 3080 Ti\nvkAllocateMemory: out of host memory\n")
        self.assertTrue(r["unavailable"])
        self.assertIn("out of host memory", r["errors"][0])
