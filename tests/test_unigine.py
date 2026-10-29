#!/usr/bin/env python3
"""Offline tests for the Unigine raster loads (Superposition and Heaven): the result parser, the pass loop on a fake
benchmark, the command lines, and unpacking a Makeself archive without running its script.

Run from the repo root:  python3 -B -m unittest discover -s tests
"""
import dataclasses
import hashlib
import io
import os
import sys
import tarfile
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

from ctune.gpu import unigine  # noqa: E402

HEAVEN_OUT = """Loading "heaven/heaven.world" 3790ms
Phoronix running
Phoronix finished
Time:\t260.544
Frames:\t39172
FPS:\t150.347
Unigine~# quit
"""
SUPERPOSITION_SCORE = """Superposition Benchmark v1.1 results:
FPS: 53.6
Score: 7164
Min FPS: 44.3
Max FPS: 61.7
"""

# A fake benchmark: prints what Heaven prints, or writes Superposition's score file under $HOME, then behaves as told.
FAKE = textwrap.dedent('''\
    import os, sys, time
    mode = sys.argv[1]
    if mode == "heaven":
        print(%r)
    elif mode == "superposition":
        d = os.path.join(os.environ["HOME"], ".Superposition", "automation")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "log-20261029_1135.txt"), "w").write(%r)
        print("Benchmark stopped")
    elif mode == "segv":
        print("Benchmark running")
        print("Segmentation fault")
        sys.exit(139)
    elif mode == "nogl":
        print("GLAppWindow::create(): can't create OpenGL context")
        sys.exit(1)
    elif mode == "hang":
        import signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(600)
    elif mode == "silent":
        sys.exit(0)
    time.sleep(0.3)
''') % (HEAVEN_OUT, SUPERPOSITION_SCORE)


class Parse(unittest.TestCase):
    def test_heaven_prints_its_result(self):
        r = unigine.parse(HEAVEN_OUT)
        self.assertEqual((r["fps"], r["score"], r["errors"]), (150.347, None, []))

    def test_superposition_writes_a_score_file(self):
        r = unigine.parse("Benchmark stopped\n" + SUPERPOSITION_SCORE)
        self.assertEqual((r["fps"], r["score"], r["min_fps"], r["max_fps"]), (53.6, 7164, 44.3, 61.7))

    def test_gamescope_noise_is_not_an_error(self):
        noise = ("[gamescope] [Error] vulkan: vkGetPhysicalDeviceFormatProperties2 returned zero modifiers\n"
                 "Errors from xkbcomp are not fatal to the X server\nUnknown command \"config_readonly\"\n")
        self.assertEqual(unigine.parse(noise)["errors"], [])
        self.assertTrue(unigine.parse("Segmentation fault\n")["errors"])
        self.assertTrue(unigine.parse("GLAppWindow::create(): can't create OpenGL context\n")["environment"])


class Loop(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ctune-fake-unigine-")
        self.game = os.path.join(self.dir, "fake.py")
        with open(self.game, "w") as f:
            f.write(FAKE)
        self.home = os.path.join(self.dir, "home")
        tools = os.path.join(self.dir, "tools")
        os.makedirs(os.path.join(tools, "heaven", "Unigine_Heaven-4.0", "data"))
        with open(os.path.join(tools, "heaven", "Unigine_Heaven-4.0", "data", unigine.HEAVEN_CFG), "w") as f:
            f.write("<settings/>")
        for name, value in (("TOOLS", tools), ("TERM_GRACE_S", 0.5)):
            patcher = mock.patch.object(unigine, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_fake(self, bench, *modes, seconds=0.1, timeout=60):
        modes = iter(modes)
        return unigine.run(bench, seconds, self.home, make_argv=lambda: [sys.executable, "-I", self.game,
                                                                         next(modes)],
                           pass_timeout_s=timeout, min_pass_s=0.01)

    def test_a_heaven_pass_and_its_fresh_config(self):
        r = self.run_fake(unigine.HEAVEN, "heaven")
        self.assertEqual((r["ok"], r["passes"], r["fps_median"]), (True, 1, 150.35))
        self.assertTrue(os.path.exists(os.path.join(self.home, unigine.HEAVEN_CFG)))

    def test_a_superposition_pass_reads_its_score_file(self):
        r = self.run_fake(unigine.SUPERPOSITION, "superposition")
        self.assertEqual((r["ok"], r["fps_median"], r["score_median"]), (True, 53.6, 7164))

    def test_passes_fill_the_time(self):
        r = self.run_fake(unigine.SUPERPOSITION, *["superposition"] * 20, seconds=1.5)
        self.assertGreaterEqual(r["passes"], 2)
        self.assertTrue(r["ok"])

    def test_an_old_score_file_never_counts_for_a_new_pass(self):
        self.run_fake(unigine.SUPERPOSITION, "superposition")
        r = self.run_fake(unigine.SUPERPOSITION, "silent")
        self.assertFalse(r["ok"])
        self.assertIn("no result", r["errors"][0])

    def test_a_crash_is_red(self):
        r = self.run_fake(unigine.HEAVEN, "heaven", "segv", seconds=5)
        self.assertEqual((r["ok"], r["unavailable"], r["passes"]), (False, False, 2))
        self.assertIn("Segmentation fault", r["errors"][0])

    def test_no_gl_context_is_inconclusive(self):
        r = self.run_fake(unigine.HEAVEN, "nogl")
        self.assertEqual((r["ok"], r["unavailable"]), (False, True))

    def test_a_hang_is_killed_and_red(self):
        r = self.run_fake(unigine.SUPERPOSITION, "hang", timeout=1)
        self.assertEqual((r["ok"], r["unavailable"]), (False, False))
        self.assertIn("did not finish in 1 s", r["errors"][0])


class Commands(unittest.TestCase):
    def test_both_run_headless_in_opengl_without_sound(self):
        for bench in unigine.BENCHES:
            argv = unigine.cmd(bench, "/tmp/h")
            self.assertEqual(argv[:3], ["gamescope", "--backend", "headless"])
            self.assertEqual(argv[argv.index("-video_app") + 1], "opengl")
            self.assertEqual(argv[argv.index("-sound_app") + 1], "null")   # no device: 9500 OpenAL errors per pass

    def test_heaven_runs_its_extreme_preset_on_a_config_outside_tools(self):
        argv = unigine.cmd(unigine.HEAVEN, "/var/tmp/ctune/heaven")
        self.assertIn("TESSELLATION_EXTREME", argv[argv.index("-extern_define") + 1])
        cfg = argv[argv.index("-engine_config") + 1]
        self.assertFalse(os.path.isabs(cfg))   # Unigine prefixes every path with bin/
        self.assertEqual(os.path.normpath(os.path.join(unigine.HEAVEN.bin_dir, cfg)),
                         os.path.join("/var/tmp/ctune/heaven", unigine.HEAVEN_CFG))

    def test_superposition_runs_one_benchmark_pass_read_only(self):
        argv = unigine.cmd(unigine.SUPERPOSITION, "/tmp/h")
        self.assertEqual(argv[argv.index("-mode") + 1], "2")
        self.assertIn("config_readonly 1", argv[argv.index("-console_command") + 1])

    def test_missing_tools_point_at_setup(self):
        with mock.patch.object(unigine.os.path, "exists", return_value=False):
            for bench in unigine.BENCHES:
                self.assertIn("unigine-setup", unigine.unavailable(bench))


class Unpack(unittest.TestCase):
    """A Makeself archive is a shell script with a gzip tar after it: setup reads past the header and never runs it."""

    def make(self, header_lines=5):
        d = tempfile.mkdtemp(prefix="ctune-fake-makeself-")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            data = b"engine"
            info = tarfile.TarInfo("./bin/superposition")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        payload = buf.getvalue()
        header = "".join(f"echo ran line {i} > {d}/ran\n" for i in range(header_lines)).encode()
        path = os.path.join(d, "fake.run")
        with open(path, "wb") as f:
            f.write(header + payload)
        bench = dataclasses.replace(unigine.SUPERPOSITION, header_lines=header_lines, payload_bytes=len(payload),
                                    payload_md5=hashlib.md5(payload).hexdigest())
        return d, path, bench

    def test_the_payload_unpacks_and_the_script_never_runs(self):
        d, path, bench = self.make()
        with mock.patch.object(unigine, "TOOLS", d):
            out = unigine._unpack(path, bench)
        with open(os.path.join(out, "bin", "superposition"), "rb") as f:
            self.assertEqual(f.read(), b"engine")
        self.assertFalse(os.path.exists(os.path.join(d, "ran")))

    def test_a_wrong_payload_is_refused(self):
        d, path, bench = self.make()
        with mock.patch.object(unigine, "TOOLS", d), self.assertRaises(RuntimeError):
            unigine._unpack(path, dataclasses.replace(bench, payload_md5="0" * 32))

    def test_the_payload_is_still_gzip(self):
        d, path, bench = self.make()
        with open(path, "rb") as f:
            f.seek(unigine._payload_offset(path, bench.header_lines))
            self.assertEqual(f.read(2), b"\x1f\x8b")   # the gzip magic sits right after the header


if __name__ == "__main__":
    unittest.main()
