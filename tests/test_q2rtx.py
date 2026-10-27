#!/usr/bin/env python3
"""Offline tests for the Quake II RTX graphics load: its console-log parser and the pass loop, run on a fake game.

Run from the repo root:  python3 -B -m unittest discover -s tests
"""
import os
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402,F401  (offline tests may not start podman, root helpers or stress tools)
os.environ.setdefault("CTUNE_DATA", tempfile.mkdtemp(prefix="ctune-test-data-"))

from ctune.gpu import q2rtx  # noqa: E402

LOG_OK = """Using SDL video driver: x11
Using VK_KHR_ray_query
Playing demo from demos/q2demo1.dm2.
1144 frames, 9.53 seconds: 120.041970 fps
"""

# A fake game: writes the console log Q2RTX would write under $XDG_DATA_HOME, then behaves as told.
FAKE = textwrap.dedent('''\
    import os, sys, time
    mode = sys.argv[1]
    d = os.path.join(os.environ["XDG_DATA_HOME"], "quake2rtx", "baseq2", "logs")
    os.makedirs(d, exist_ok=True)
    log = {"ok": %r, "lost": "Using VK_KHR_ray_query\\nDevice lost!\\n", "fatal": "Using VK_KHR_ray_query\\nFATAL: bad\\n",
           "norefresh": "Couldn't initialize refresh: no display\\n", "hang": "Using VK_KHR_ray_query\\n",
           "silent": ""}[mode]
    open(os.path.join(d, "console.log"), "w").write(log)
    if mode == "hang":
        import signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)   # a modal error dialog: SIGTERM does nothing
        time.sleep(600)
    time.sleep(0.3)
    sys.exit(0 if mode == "ok" else 1)
''') % LOG_OK


class Parse(unittest.TestCase):
    def test_a_finished_pass(self):
        r = q2rtx.parse_log(LOG_OK)
        self.assertEqual((r["fps"], r["frames"], r["api"], r["errors"]), (120.04, 1144, "VK_KHR_ray_query", []))

    def test_device_lost_and_fatal_are_errors(self):
        self.assertIn("Device lost!", q2rtx.parse_log("Device lost!\n")["errors"])
        self.assertEqual(q2rtx.parse_log("Using VK_KHR_ray_query\nFATAL: Z_Malloc failed\n")["errors"],
                         ["FATAL: Z_Malloc failed"])

    def test_no_rt_gpu_or_no_refresh_is_the_environment(self):
        for line in ("No ray tracing capable GPU found.", "Couldn't initialize refresh: SDL_Init failed"):
            self.assertTrue(q2rtx.parse_log(line + "\n")["environment"], line)
        self.assertFalse(q2rtx.parse_log("Device lost!\n")["environment"])


class Passes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ctune-fake-q2rtx-")
        self.game = os.path.join(self.dir, "fake_q2rtx.py")
        with open(self.game, "w") as f:
            f.write(FAKE)
        self.home = os.path.join(self.dir, "home")
        for name, value in (("screen_off", False), ("_inhibit_idle", None)):
            patcher = mock.patch.object(q2rtx, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(q2rtx, "TERM_GRACE_S", 0.5)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_fake(self, *modes, seconds=1.5, **kw):
        modes = iter(modes)
        return q2rtx.run(seconds, self.home, make_argv=lambda: [sys.executable, "-I", self.game, next(modes)],
                         min_pass_s=0.1, **kw)

    def test_passes_fill_the_time_and_report_fps(self):
        r = self.run_fake(*["ok"] * 20)
        self.assertTrue(r["ok"], r)
        self.assertGreaterEqual(r["passes"], 2)
        self.assertEqual(r["fps_median"], 120.04)
        self.assertEqual(r["api"], "VK_KHR_ray_query")

    def test_a_lost_device_is_red_and_ends_the_run(self):
        r = self.run_fake("ok", "lost", "ok", seconds=30)
        self.assertFalse(r["ok"])
        self.assertFalse(r["unavailable"])
        self.assertEqual(r["passes"], 2)
        self.assertTrue(any("Device lost!" in e for e in r["errors"]))

    def test_a_fatal_error_is_red(self):
        r = self.run_fake("fatal", seconds=30)
        self.assertFalse(r["ok"] or r["unavailable"])

    def test_a_hang_is_killed_and_red(self):
        r = self.run_fake("hang", seconds=30, pass_timeout_s=1.5)
        self.assertFalse(r["ok"] or r["unavailable"])
        self.assertTrue(any("did not finish" in e for e in r["errors"]), r["errors"])

    def test_a_hang_while_the_screen_is_off_is_inconclusive_on_the_desktop(self):
        with mock.patch.object(q2rtx, "screen_off", return_value=True), mock.patch.object(q2rtx, "MODE", "session"):
            r = self.run_fake("hang", seconds=30, pass_timeout_s=1.5)
        self.assertTrue(r["unavailable"])
        self.assertTrue(any("screen" in e for e in r["errors"]), r["errors"])

    def test_a_hang_in_gamescope_is_red_even_with_the_screen_locked(self):
        with mock.patch.object(q2rtx, "screen_off", return_value=True), mock.patch.object(q2rtx, "MODE", "headless"):
            r = self.run_fake("hang", seconds=30, pass_timeout_s=1.5)
        self.assertFalse(r["ok"] or r["unavailable"])

    def test_failing_to_start_is_inconclusive_but_failing_later_is_red(self):
        self.assertTrue(self.run_fake("norefresh", seconds=30)["unavailable"])
        self.assertTrue(self.run_fake("silent", seconds=30)["unavailable"])
        later = self.run_fake("ok", "norefresh", seconds=30)
        self.assertFalse(later["ok"] or later["unavailable"])

    def test_a_guardrail_stops_the_pass(self):
        r = self.run_fake("hang", seconds=30, pass_timeout_s=20, guard=lambda: "GPU reached 83 °C")
        self.assertFalse(r["ok"])
        self.assertIn("GPU reached 83 °C", r["errors"])


class Command(unittest.TestCase):
    def test_runs_the_binary_in_the_steam_runtime_without_sound_and_quits(self):
        argv = q2rtx.cmd()
        self.assertEqual(argv[argv.index(q2rtx.RUNTIME) + 1:argv.index(q2rtx.RUNTIME) + 3], ["--", q2rtx.BIN])
        joined = " ".join(argv)
        for part in ("+set s_enable 0", "+timedemo 1", "+demo q2demo1", "+set nextserver quit"):
            self.assertIn(part, joined)
        self.assertNotIn("q2rtx.sh", joined)   # its first launch asks a zenity question

    def test_headless_renders_in_gamescope_without_the_desktop(self):
        argv = q2rtx.cmd("headless")
        self.assertEqual(argv[:3], ["gamescope", "--backend", "headless"])
        self.assertEqual(argv[argv.index("--") + 1], q2rtx.RUNTIME)
        self.assertEqual(q2rtx.cmd("session")[0], q2rtx.RUNTIME)
        with mock.patch.object(q2rtx.os.path, "exists", return_value=True), \
                mock.patch.object(q2rtx.shutil, "which", return_value="/usr/bin/gamescope"), \
                mock.patch.dict(os.environ, {"DISPLAY": "", "WAYLAND_DISPLAY": ""}):
            self.assertIsNone(q2rtx.unavailable("headless"))
            self.assertIn("display", q2rtx.unavailable("session"))

    def test_missing_tools_make_it_unavailable(self):
        with mock.patch.object(q2rtx.os.path, "exists", return_value=False):
            self.assertIn("q2rtx-setup", q2rtx.unavailable())


if __name__ == "__main__":
    unittest.main()
