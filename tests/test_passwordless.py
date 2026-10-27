#!/usr/bin/env python3
"""Offline tests for the passwordless root path of the CPU and RAM domains (the GPU's are in test_gpu_persist.py):
the installed CPU helper keeps its own state, and the RAM snapshot writes the collector's output only inside the
snapshot directory.

Run from the repo root:  python3 -B -m unittest discover -s tests
"""
import base64
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("CTUNE_DATA", tempfile.mkdtemp(prefix="ctune-test-data-"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402,F401  (offline tests may not start podman, root helpers or stress tools)


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CpuHelper(unittest.TestCase):
    def setUp(self):
        self.apply = load("ctune/cpu/apply.py", "ctune_cpu_apply_under_test")

    def run_main(self, *argv):
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["apply.py", *argv]), mock.patch.object(self.apply.os, "geteuid",
                                                                                    return_value=0), \
                mock.patch.object(self.apply.os, "umask"), contextlib.redirect_stdout(buf):
            rc = self.apply.main()
        return rc, json.loads(buf.getvalue().strip().splitlines()[-1])

    def test_the_installed_copy_refuses_a_state_file_from_the_caller(self):
        with mock.patch.object(self.apply, "installed", return_value=True):
            for argv in (["--state", "/tmp/forged.json", "--probe"], ["--owner", "1000:1000", "--probe"]):
                rc, out = self.run_main(*argv)
                self.assertFalse(out["ok"], argv)
                self.assertIn(self.apply.RUN_STATE, out["errors"][0])

    def test_the_repo_copy_still_needs_a_state_file(self):
        with mock.patch.object(self.apply, "installed", return_value=False):
            rc, out = self.run_main("--probe")
        self.assertFalse(out["ok"])
        self.assertIn("--state", out["errors"][0])

    def test_smu_comes_from_the_helpers_own_directory(self):
        self.assertEqual(os.path.dirname(self.apply.smu.__file__), os.path.join(REPO, "ctune", "cpu"))

    def test_the_api_calls_the_installed_copy_without_state_arguments(self):
        from ctune.cpu import api
        ok = mock.Mock(returncode=0, stdout=json.dumps({"ok": True}), stderr="")
        with mock.patch.object(api.os.path, "isfile", return_value=True), \
                mock.patch.object(api.os, "geteuid", return_value=1000), \
                mock.patch.object(api.subprocess, "run", return_value=ok) as run:
            api._helper(["--probe"])
        self.assertEqual(run.call_args.args[0], ["pkexec", api.RUNTIME, "--probe"])


class MemCollector(unittest.TestCase):
    def setUp(self):
        self.rs = load("ctune/mem/platforms/zen3_am4/ram_snapshot.py", "ctune_ram_snapshot_under_test")
        self.d = tempfile.mkdtemp(prefix="ctune-snap-")

    def fake_run(self, files):
        out = {"regs": {"0x50200": 1}, "unstable": {}, "dmidecode": "", "dmidecode_t17_dump": "", "bclk": {},
               "aod": "skipped", "fwmem": None,
               "files": {k: base64.b64encode(v).decode() for k, v in files.items()}}
        return lambda cmd, **kw: mock.Mock(returncode=0, stdout=json.dumps(out), stderr="", cmd=cmd)

    def test_dumps_land_inside_the_snapshot(self):
        out = self.rs.collect_passwordless(False, self.d, collector="/x/collect_root.py",
                                           run=self.fake_run({"raw/fwmem/hiidb.bin": b"\x01\x02"}))
        with open(os.path.join(self.d, "raw", "fwmem", "hiidb.bin"), "rb") as f:
            self.assertEqual(f.read(), b"\x01\x02")
        self.assertNotIn("files", out)

    def test_a_path_outside_the_snapshot_is_refused(self):
        for bad in ("../evil", "/etc/evil", "raw/../../evil"):
            with self.assertRaises(ValueError, msg=bad):
                self.rs.collect_passwordless(False, self.d, collector="/x", run=self.fake_run({bad: b"x"}))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.d), "evil")))

    def test_the_collector_takes_one_flag_only(self):
        col = load("ctune/mem/platforms/zen3_am4/collect_root.py", "ctune_collect_root_under_test")
        for argv in (["--out", "/tmp"], ["--dump-dir", "/tmp"], ["--skip"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                col.main(argv)

    def test_the_api_runs_the_snapshot_as_the_user_once_the_collector_is_installed(self):
        from ctune.mem import api
        with mock.patch.object(api.os.path, "isfile", return_value=True), \
                mock.patch.object(api.os, "geteuid", return_value=1000), \
                mock.patch.object(api.subprocess, "run", side_effect=RuntimeError("stop")) as run:
            with self.assertRaises(RuntimeError):
                api.snapshot("t")
        cmd = run.call_args.args[0]
        self.assertNotIn("pkexec", cmd)
        self.assertNotIn("--owner", cmd)


if __name__ == "__main__":
    unittest.main()
