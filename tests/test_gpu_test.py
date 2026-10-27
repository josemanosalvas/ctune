#!/usr/bin/env python3
"""Offline tests for the GPU stability test: default parts, the load-transient pauser, the sign-off's gpu phase.

Run from the repo root:  python3 -B -m unittest discover -s tests
"""
import inspect
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402,F401  (offline tests may not start podman, root helpers or stress tools)
os.environ.setdefault("CTUNE_DATA", tempfile.mkdtemp(prefix="ctune-test-data-"))

from ctune.gpu import api as gpu, burn  # noqa: E402

DEFAULTS = {k: p.default for k, p in inspect.signature(gpu.test).parameters.items()}


class Defaults(unittest.TestCase):
    """The screen runs every part unless told otherwise, and the defaults live in one place (gpu.test)."""

    def test_screen_runs_tensor_cores_transients_and_graphics_by_default(self):
        from ctune.gpu import q2rtx
        self.assertGreaterEqual(DEFAULTS["tc_seconds"], burn.MIN_SECONDS["tc"])
        self.assertGreaterEqual(DEFAULTS["transient_seconds"], burn.MIN_SECONDS["transient"])
        self.assertGreaterEqual(DEFAULTS["graphics_seconds"], q2rtx.MIN_SECONDS)

    def test_the_heat_and_noise_phase_runs_long_enough_to_settle(self):
        """FP64 is the only load at the lock below the 400 W cap, so it is where an undervolt shows as less heat and
        fan noise; temp_c_end averages its last minute, which needs several minutes of warm-up first."""
        self.assertGreaterEqual(DEFAULTS["fp64_seconds"], 600)

    def test_mcp_passes_only_what_the_caller_gave(self):
        from ctune import mcp_server
        with mock.patch.object(mcp_server, "_run") as run:
            mcp_server.TOOLS["gpu_test"][2]({"tc_seconds": 300})
        self.assertEqual(run.call_args.args[:3], ("gpu_test", gpu.test, True))
        self.assertEqual(run.call_args.kwargs, {"tc_seconds": 300})

    def test_cli_passes_only_what_the_caller_gave(self):
        from ctune import cli
        with mock.patch.object(cli.gpu, "test", return_value={}) as test, mock.patch.object(cli, "_out"):
            cli.main(["gpu", "test", "core", "--tc-seconds", "300"])
        self.assertEqual(test.call_args.kwargs, {"kind": "core", "tc_seconds": 300})


class FakeLoad:
    def __init__(self):
        self.stopped, self.dead = False, False

    def alive(self):
        return not self.dead


class Pauser(unittest.TestCase):
    """The GPU counterpart of cpu_test's mprime suspend: the container is frozen for pause_s in every cycle."""

    def setUp(self):
        self.calls, self.marks = [], []
        self.rc = {"pause": 0, "unpause": 0}

        def podman(verb, cidfile):
            self.assertEqual(cidfile, "/run/cid")
            self.calls.append((verb, time.monotonic()))
            return mock.Mock(returncode=self.rc[verb], stderr="no such container" if self.rc[verb] else "")
        patcher = mock.patch.object(burn, "_podman", side_effect=podman)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pauser(self, load, seconds=1.6, **kw):
        return burn.Pauser(load, seconds, "/run/cid", run_s=0.25, pause_s=0.15, tail_s=0.3, mark=self.marks.append,
                           **kw)

    def test_freezes_in_cycles_and_always_thaws(self):
        p = self.pauser(FakeLoad())
        p.start()
        p.join(5)
        verbs = [v for v, _ in self.calls]
        self.assertGreaterEqual(p.pauses, 2)
        self.assertEqual(verbs, ["pause", "unpause"] * p.pauses)
        self.assertEqual(self.marks, ["paused", "load"] * p.pauses)
        for (_, t_pause), (_, t_thaw) in zip(self.calls[::2], self.calls[1::2]):
            self.assertAlmostEqual(t_thaw - t_pause, 0.15, delta=0.1)
        self.assertFalse(p.paused)

    def test_never_frozen_when_gpu_burns_own_timer_ends(self):
        t0 = time.monotonic()
        p = self.pauser(FakeLoad())
        p.start()
        p.join(5)
        self.assertLess(self.calls[-1][1] - t0, 1.6 - 0.3 + 0.05)

    def test_a_stop_thaws_at_once(self):
        load = FakeLoad()
        p = self.pauser(load, seconds=60)
        p.start()
        while not p.paused:
            time.sleep(0.01)
        load.stopped = True
        t = time.monotonic()
        p.join(5)
        self.assertFalse(p.is_alive())
        self.assertEqual(self.calls[-1][0], "unpause")
        self.assertLess(self.calls[-1][1] - t, 0.1)

    def test_ends_when_the_load_dies(self):
        load = FakeLoad()
        p = self.pauser(load, seconds=60)
        p.start()
        time.sleep(0.4)
        load.dead = True
        p.join(2)
        self.assertFalse(p.is_alive())
        self.assertFalse(p.paused)

    def test_a_failed_pause_is_counted_not_thawed(self):
        self.rc["pause"] = 125
        p = self.pauser(FakeLoad())
        p.start()
        p.join(5)
        self.assertEqual(p.pauses, 0)
        self.assertTrue(p.failures)
        self.assertNotIn("unpause", [v for v, _ in self.calls])

    def test_a_thaw_that_keeps_failing_marks_the_run_stuck(self):
        """A container left frozen ends gpu-burn without its verdict line: that is the tool, not the GPU."""
        self.rc["unpause"] = 125
        p = self.pauser(FakeLoad())
        p.start()
        p.join(5)
        self.assertTrue(p.stuck)
        self.assertEqual(p.pauses, 0)
        self.assertTrue(any("unpause" in f for f in p.failures))

    def test_transient_runs_fp32_on_short_batches(self):
        cmd = burn.cmd("transient", 180, burn.TRANSIENT_MB, "/run/cid")
        self.assertEqual(cmd[-3:], ["-m", str(burn.TRANSIENT_MB), "180"])
        self.assertFalse({"-d", "-tc"} & set(cmd))
        # 8192 x 8192 FP32 results of 256 MiB, two of them for A and B: two compared slots per batch
        self.assertEqual((burn.TRANSIENT_MB - 2 * 256) // 256, 2)


class SharedGpu(unittest.TestCase):
    """A unit-test run with a scratch CTUNE_DATA must never call gpu.test for real: a `podman run --replace` would
    kill a campaign's running gpu-burn container and turn its step red."""

    def test_offline_tests_cannot_start_podman_or_a_tool(self):
        import glob
        import subprocess
        for cmd in (["podman", "run", "--name", burn.NAME], f"podman pause {burn.NAME}", [gpu.MEMTEST, "1"],
                    ["timeout", "5", gpu.VKPEAK, "0"], ["pkexec", "true"], ["curl", "-fL", "https://example.org"],
                    ["systemctl", "--user", "stop", "ctune-run-x.service"],
                    ["systemctl", "--user", "reset-failed", "ctune-run-x.service"]):
            with self.assertRaises(AssertionError, msg=cmd):
                subprocess.run(cmd, capture_output=True)
        with self.assertRaises(AssertionError):
            subprocess.run(["true"], executable="/usr/bin/podman", capture_output=True)
        subprocess.run(["systemctl", "--user", "show", "ctune-run-x.service", "-p", "Id"], capture_output=True)
        here = os.path.dirname(os.path.abspath(__file__))
        unguarded = [os.path.basename(p) for p in glob.glob(os.path.join(here, "test_*.py"))
                     if "\nimport guard  # noqa" not in open(p).read()]
        self.assertEqual(unguarded, [], "every test module imports guard, so it is guarded when run alone too")

    def test_invalid_durations_never_reach_a_load(self):
        from ctune.core import stress
        with mock.patch.object(stress, "check_interrupted", side_effect=AssertionError("reached a real run")):
            for kw in ({"burn_seconds": 0, "fp64_seconds": 0, "tc_seconds": 0, "transient_seconds": 0},
                       {"fp64_seconds": 30}, {"transient_seconds": 20}, {"burn_seconds": -5}):
                with self.assertRaises(ValueError, msg=kw):
                    gpu.test("core", **kw)

    def test_a_run_touches_only_its_own_container(self):
        cmd = burn.cmd("fp32", 600, 8192, "/run/cid")
        self.assertNotIn("--replace", cmd)
        self.assertIn("--cidfile=/run/cid", cmd)
        with mock.patch.object(burn.subprocess, "run") as run, mock.patch.object(burn.os.path, "exists",
                                                                                return_value=True):
            burn.cleanup("/run/cid")
        args = run.call_args_list[0].args[0]
        self.assertIn("--cidfile=/run/cid", args)
        self.assertNotIn(burn.NAME, args)

    def test_a_stale_container_is_cleared_but_a_running_one_refuses(self):
        def podman(state):
            def run(args, **kw):
                if args[:3] == ["podman", "container", "inspect"]:
                    return mock.Mock(returncode=0 if state else 125, stdout=state)
                return mock.Mock(returncode=0)
            return run
        with mock.patch.object(burn.subprocess, "run", side_effect=podman("running\n")) as run:
            self.assertIn("another gpu-burn", burn.busy())
        self.assertEqual(len(run.call_args_list), 1)        # nothing removed
        with mock.patch.object(burn.subprocess, "run", side_effect=podman("exited\n")) as run:
            self.assertIsNone(burn.busy())
        self.assertEqual(run.call_args_list[1].args[0], ["podman", "rm", "--ignore", burn.NAME])   # never --force
        with mock.patch.object(burn.subprocess, "run", side_effect=podman("")):
            self.assertIsNone(burn.busy())

    def test_gpu_test_refuses_beside_another_gpu_burn(self):
        from ctune.core import stress
        with mock.patch.object(stress, "check_interrupted"), mock.patch.object(burn, "image_present", return_value=True), \
                mock.patch.object(burn, "busy", return_value="another gpu-burn container is running"), \
                mock.patch.object(gpu, "_Sampler", side_effect=AssertionError("started")):
            with self.assertRaisesRegex(RuntimeError, "another gpu-burn"):
                gpu.test("core")

    def test_a_podman_timeout_is_a_failure_not_a_dead_pause_thread(self):
        import subprocess
        with mock.patch.object(burn.subprocess, "run", side_effect=subprocess.TimeoutExpired("podman", 30)):
            self.assertNotEqual(burn._podman("pause", "/run/cid").returncode, 0)
            self.assertIn("could not be asked", burn.busy())

    def test_a_name_collision_is_inconclusive(self):
        r = burn.parse(125, 'Error: creating container storage: the container name "ctune-gpu-burn" is already in use '
                            'by abc. You have to remove that container to be able to reuse that name\n', False)
        self.assertTrue(r["unavailable"])
        self.assertIn("another gpu-burn", r["errors"][-1])

    def test_an_inline_test_refuses_beside_a_detached_one(self):
        from ctune.core import stress
        with mock.patch.object(stress, "active_units", return_value=["ctune-run-gpu-test-1.service"]), \
                mock.patch.dict(os.environ, {"CTUNE_UNIT": ""}):
            with self.assertRaisesRegex(RuntimeError, "ctune-run-gpu-test-1"):
                with stress.run("gpu", "gpu-core"):
                    self.fail("ran beside another test")


class Telemetry(unittest.TestCase):
    def test_heat_and_noise_per_part(self):
        """The undervolt's goal is less heat and fan noise: report temperature and fan as avg, max and settled."""
        s = gpu._Sampler.__new__(gpu._Sampler)
        s.rows = [(1905, 150.0, 40 + i // 6, (), "fp64", 30 + i // 3) for i in range(120)] + \
                 [(1905, 150.0, 70, (), "fp32", 60)]
        t = s.summary("fp64", 1905)
        self.assertEqual((t["temp_c_max"], t["temp_c_avg"], t["fan_pct_max"], t["fan_pct_avg"]), (59, 49.5, 69, 49.5))
        self.assertEqual((t["temp_c_end"], t["fan_pct_end"]), (54.5, 59.5))   # the last 60 s, once it has settled
        for k in ("core_mhz_avg", "power_w_avg", "limiter_share", "at_lock_share"):   # old fields still compare
            self.assertIn(k, t)

    def test_no_fan_reading_leaves_the_fan_fields_out(self):
        s = gpu._Sampler.__new__(gpu._Sampler)
        s.rows = [(1905, 150.0, 50, (), "fp64", None)] * 4
        t = s.summary("fp64")
        self.assertNotIn("fan_pct_avg", t)
        self.assertEqual(t["temp_c_avg"], 50)

    def test_paused_samples_are_kept_apart(self):
        s = gpu._Sampler.__new__(gpu._Sampler)
        s.rows = [(1905, 390.0, 70, ("power cap",), "transient", 60)] * 9 + \
                 [(1905, 120.0, 68, ("idle",), "transient:paused", 60)]
        t = gpu._part_telemetry(s, "transient", 1905)
        self.assertEqual(t["samples"], 9)
        self.assertEqual(t["at_lock_share"], 1.0)
        self.assertEqual(t["paused"]["core_mhz_avg"], 1905)
        self.assertNotIn("power_w_avg", t["paused"])   # NVML's 1 s average would show the load before the pause
        self.assertNotIn("paused", gpu._part_telemetry(s, "fp32", 1905))


FAKE_MEMTEST = """#!/bin/sh
# memtest_vulkan stand-in: exits 68 with no output when the buffer ($2 bytes) is over the limit, as the real one does
[ "$2" -gt "$LIMIT" ] && exit 68
[ -n "$NODEV" ] && { echo "Runtime error: No device at given index"; exit 68; }
for i in 1 2 3 4; do echo "$i iteration. Passed 1.2 seconds  written: 9.5GB 800.0GB/sec  checked: 19.0GB 799.2GB/sec"; done
"""


class VramAllocation(unittest.TestCase):
    """memtest_vulkan exits 68 with no output when it cannot allocate its buffer: free VRAM had fallen after ctune
    read it (for example right after a gpu-burn container exited, or at a mem_test fabric start)."""

    def setUp(self):
        d = tempfile.mkdtemp(prefix="ctune-fake-memtest-")
        fake = os.path.join(d, "memtest_vulkan")
        with open(fake, "w") as f:
            f.write(FAKE_MEMTEST)
        os.chmod(fake, 0o755)
        for name, value in (("MEMTEST", fake), ("ALLOC_PROBE_S", 2)):
            patcher = mock.patch.object(gpu, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def load(self, limit_mb, budget_mb=10369, **env):
        with mock.patch.dict(os.environ, {"LIMIT": str(limit_mb << 20), **env}):
            ld = gpu.VramLoad("vram", 60, budget_mb << 20)
            ld.start()
            ld.proc.wait(10)
            return ld.result()

    def test_a_failed_allocation_retries_1_gib_smaller_and_records_the_budget(self):
        r = self.load(limit_mb=8500)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["budget_mb"], 10369 - 2048)
        self.assertEqual(r["alloc_retries"], [{"budget_mb": 10369, "rc": 68}, {"budget_mb": 10369 - 1024, "rc": 68}])
        self.assertEqual(r["checked_gbps_avg"], 799.2)

    def test_retries_go_down_to_the_minimum_then_it_is_inconclusive(self):
        r = self.load(limit_mb=1000)
        self.assertFalse(r["ok"])
        self.assertTrue(r["unavailable"])
        budgets = [a["budget_mb"] for a in r["alloc_retries"]]
        self.assertEqual(budgets[0], 10369)
        self.assertGreaterEqual(budgets[-1], gpu.VRAM_MIN_MB)
        self.assertLess(budgets[-1] - gpu.VRAM_STEP_MB, gpu.VRAM_MIN_MB)
        self.assertIn("could not allocate", r["errors"][0])

    def test_a_fragmented_7_gb_block_is_found_from_10_gb(self):
        r = self.load(limit_mb=7300)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["budget_mb"], 10369 - 3 * 1024)
        self.assertEqual(len(r["alloc_retries"]), 3)

    def test_another_exit_68_is_not_retried(self):
        r = self.load(limit_mb=99999, NODEV="1")
        self.assertEqual(r["alloc_retries"], [])
        self.assertIn("No device at given index", " ".join(r["errors"]))

    def test_parse_names_the_allocation_failure(self):
        r = gpu.parse_vram(68, "")
        self.assertTrue(r["unavailable"])
        self.assertIn("could not allocate", r["errors"][0])

    def test_the_mem_heat_load_retries_too(self):
        from ctune.mem import test as memtest
        with mock.patch.object(gpu, "vram_free_check", return_value=None), \
                mock.patch.object(gpu, "vram_budget_bytes", return_value=4 << 30):
            ld, why = memtest._gpu_heat(600)
        self.assertIsNone(why)
        self.assertIsInstance(ld, gpu.VramLoad)
        self.assertTrue(ld.optional)


def _result(ts, offset, lock, fp64, fp32, verdict="green"):
    part = lambda g, mhz: {"ok": True, "gflops_median": g, "telemetry": {"core_mhz_avg": mhz}}
    return {"ts": ts, "kind": "result", "domain": "gpu", "verdict": verdict,
            "settings": {"core_offset": offset, "max_clock": lock} if lock else "defaults",
            "core": {"fp64": part(*fp64), "fp32": part(*fp32), "ok": True}}


class ClockStretch(unittest.TestCase):
    """Too little voltage makes Ampere stretch its clock: NVML still shows the requested MHz, but less work gets done
    per MHz. In these fixtures step 3 (+70 MHz at 1905 MHz) has no error, yet FP64 falls from 519 to 497 GFLOPS at the
    same 1905 MHz; nothing but the work per MHz shows it."""

    STOCK = _result("2026-10-27T17:40:05+0100", None, None, (542, 1988), (26438, 1682))
    STEP1 = _result("2026-10-27T18:12:56+0100", 0, 1905, (519, 1905), (26435, 1682))

    def test_step_3_is_flagged_against_the_last_green_result_at_the_same_lock(self):
        now = _result("2026-10-27T19:12:00+0100", 70, 1905, (497, 1905), (25826, 1743), verdict=None)
        r = gpu.clock_stretch(now, [self.STOCK, self.STEP1])
        self.assertEqual(r["baseline_ts"], self.STEP1["ts"])
        self.assertEqual(r["stretched"], ["fp64", "fp32"])
        self.assertEqual((r["parts"]["fp64"]["change_pct"], r["parts"]["fp32"]["change_pct"]), (-4.2, -5.7))

    def test_a_drop_within_1_pct_is_not_flagged(self):
        now = _result("t", 30, 1905, (516, 1905), (26600, 1695), verdict=None)
        self.assertEqual(gpu.clock_stretch(now, [self.STEP1])["stretched"], [])

    def test_a_stretched_green_result_is_never_a_baseline(self):
        """Otherwise step 3 (stretched, recorded green) would be step 4's baseline and the drift would go unseen."""
        step3 = dict(_result("2026-10-27T19:12:00+0100", 70, 1905, (497, 1905), (25826, 1743)),
                     clock_stretch={"stretched": ["fp64", "fp32"]})
        now = _result("t", 85, 1905, (497, 1905), (25826, 1743), verdict=None)
        r = gpu.clock_stretch(now, [self.STEP1, step3])
        self.assertEqual(r["baseline_ts"], self.STEP1["ts"])
        self.assertEqual(r["stretched"], ["fp64", "fp32"])

    def test_only_green_results_at_the_same_lock_are_baselines(self):
        red = _result("2026-10-27T18:34:21+0100", 100, 1905, (450, 1905), (25000, 1830), verdict="red")
        now = _result("t", 70, 1905, (497, 1905), (25826, 1743), verdict=None)
        self.assertEqual(gpu.clock_stretch(now, [self.STEP1, red])["baseline_ts"], self.STEP1["ts"])
        other_lock = _result("t", 70, 1860, (480, 1860), (25826, 1743), verdict=None)
        r = gpu.clock_stretch(other_lock, [self.STOCK, self.STEP1])
        self.assertIsNone(r["baseline_ts"])
        self.assertEqual(r["stretched"], [])


class Graphics(unittest.TestCase):
    """Quake II RTX as the graphics and RT part: it checks nothing it renders, so it is red only on a crash, a lost
    device, a hang or an Xid error, and inconclusive when it cannot run."""

    def test_durations_are_checked_before_anything_runs(self):
        from ctune.core import stress
        with mock.patch.object(stress, "check_interrupted", side_effect=AssertionError("reached a real run")):
            for kind, kw in (("graphics", {"graphics_seconds": 0}), ("graphics", {"graphics_seconds": 20}),
                             ("screen", {"graphics_seconds": -1}), ("vram", {"vram_seconds": 0}),
                             ("vkpeak", {"core_runs": 0})):
                with self.assertRaises(ValueError, msg=(kind, kw)):
                    gpu.test(kind, **kw)

    def test_a_missing_game_is_inconclusive_not_red(self):
        from ctune.gpu import q2rtx
        with mock.patch.object(q2rtx, "unavailable", return_value="tools/q2rtx missing: run `ctune gpu q2rtx-setup`"), \
                mock.patch.object(q2rtx, "run", side_effect=AssertionError("started")):
            r = gpu._graphics(600, None)
        self.assertEqual((r["ok"], r["unavailable"]), (False, True))
        res = {"core": {"fp32": {"ok": True}, "ok": True}, "graphics": r, "vram": {"ok": True}}
        verdict, blocked = gpu._verdict(res, [])
        self.assertEqual(verdict, "inconclusive")
        self.assertIn("q2rtx-setup", blocked[0])

    def test_a_part_that_could_not_run_never_hides_a_red_one(self):
        res = {"core": {"fp32": {"ok": True}, "ok": True},
               "graphics": {"ok": False, "unavailable": True, "errors": ["tools/q2rtx missing"]},
               "vram": {"ok": False, "unavailable": False, "errors": ["mismatch at 0x1000"]}}
        self.assertEqual(gpu._verdict(res, []), ("red", ["tools/q2rtx missing"]))
        res["core"] = {"fp32": {"ok": False, "unavailable": False, "errors": ["compute errors: 3"]},
                       "transient": {"ok": False, "unavailable": True, "errors": ["never paused"]}, "ok": False}
        del res["vram"]
        self.assertEqual(gpu._verdict(res, [])[0], "red")

    def test_a_crash_in_the_game_is_red_and_an_xid_beats_inconclusive(self):
        res = {"graphics": {"ok": False, "unavailable": False, "errors": ["pass 3: exit 1: Device lost!"]}}
        self.assertEqual(gpu._verdict(res, [])[0], "red")
        res = {"graphics": {"ok": False, "unavailable": True, "errors": ["screen locked"]}}
        self.assertEqual(gpu._verdict(res, ["NVRM: Xid (PCI:0000:0a:00): 13, Graphics Exception"])[0], "red")
        self.assertEqual(gpu._verdict({"graphics": {"ok": True}}, [])[0], "green")


class SignoffGpuPhase(unittest.TestCase):
    def test_every_part_runs_and_they_fill_the_phase(self):
        from ctune.core import signoff, stress
        with mock.patch.object(gpu, "test", return_value={"verdict": "green"}) as test, \
                mock.patch.object(stress, "error_snapshot"), \
                mock.patch.object(stress, "error_delta", return_value={"clean": True}):
            r = signoff._phase("gpu", signoff.MINUTES["gpu"])
        self.assertTrue(r["ok"])
        kw = test.call_args.kwargs
        parts = {k: v for k, v in kw.items() if k.endswith("_seconds")}
        self.assertTrue(all(v > 0 for v in parts.values()), parts)
        for k in ("burn_seconds", "transient_seconds", "tc_seconds", "fp64_seconds", "graphics_seconds",
                  "vram_seconds"):
            self.assertIn(k, parts)
        self.assertAlmostEqual(sum(parts.values()), signoff.MINUTES["gpu"] * 60, delta=60)


if __name__ == "__main__":
    unittest.main()
