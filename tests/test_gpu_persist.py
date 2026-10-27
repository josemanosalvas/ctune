#!/usr/bin/env python3
"""Offline tests for GPU persistence: which sign-off allows persisting (persist.plan), and the boot applier
(boot_apply) against fake nvidia-smi and nvidia_oc commands: arguments, readback, the driver check and the
crash-loop guard.

Run from the repo root:  python3 -B -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402,F401  (offline tests may not start podman, root helpers or stress tools)
os.environ.setdefault("CTUNE_DATA", tempfile.mkdtemp(prefix="ctune-test-data-"))

from ctune.gpu import boot_apply, install, persist  # noqa: E402
from ctune.core.signoff import MINUTES  # noqa: E402

IDENT = {"gpu_uuid": "GPU-fa9f0f08", "driver": "615.78.08"}
UV = {"core_offset": 150, "mem_offset": 0, "max_clock": 1860, "power_limit": 400.0}


def signoff(gpu, verdict="green", ident=IDENT, ts="2026-11-01T10:00:00+0100", phases=("ram", "gpu"),
            gpu_min=MINUTES["gpu"]):
    return {"kind": "result", "domain": "signoff", "ts": ts, "verdict": verdict, "phases": list(phases),
            "minutes": {p: (gpu_min if p == "gpu" else 60) for p in phases},
            "settings": {"gpu": dict(gpu, boot_id="b", ts=ts), "gpu_identity": ident}}


class Plan(unittest.TestCase):
    def rb(self, s=UV):
        return {"core_offset": s["core_offset"], "mem_offset": s["mem_offset"], "power_limit": s["power_limit"]}

    def test_green_signoff_with_the_same_settings_allows_it(self):
        p = persist.plan(UV, self.rb(), IDENT, [signoff(UV)])
        self.assertTrue(p["ok"], p["reasons"])
        self.assertEqual(p["config"]["settings"], UV)
        self.assertEqual(p["config"]["driver"], "615.78.08")
        self.assertEqual(p["config"]["signoff"]["verdict"], "green")

    def test_the_latest_run_with_these_settings_decides(self):
        recs = [signoff(UV), signoff(UV, "red", ts="2026-11-02T10:00:00+0100")]
        p = persist.plan(UV, self.rb(), IDENT, recs)
        self.assertFalse(p["ok"])
        self.assertIn("is red", p["reasons"][0])

    def test_other_settings_and_other_domains_do_not_count(self):
        other = dict(UV, core_offset=165)
        recs = [signoff(other), {"kind": "result", "domain": "gpu", "verdict": "green", "settings": {"gpu": UV}},
                {"kind": "result", "domain": "signoff", "verdict": "green", "settings": {"gpu": "defaults"}}]
        self.assertIn("no sign-off with a full gpu phase ran with these GPU settings",
                      persist.plan(UV, self.rb(), IDENT, recs)["reasons"])

    def test_a_signoff_without_a_full_gpu_phase_does_not_count(self):
        for rec in (signoff(UV, phases=("idle",)), signoff(UV, gpu_min=10)):
            p = persist.plan(UV, self.rb(), IDENT, [rec])
            self.assertFalse(p["ok"])
            self.assertIn("full gpu phase", p["reasons"][0])

    def test_another_driver_needs_a_new_signoff(self):
        p = persist.plan(UV, self.rb(), IDENT, [signoff(UV, ident={"gpu_uuid": "GPU-fa9f0f08", "driver": "610.1"})])
        self.assertFalse(p["ok"])
        self.assertIn("run it again", p["reasons"][0])

    def test_nothing_applied_or_changed_behind_ctunes_back(self):
        self.assertFalse(persist.plan({}, self.rb(), IDENT, [signoff(UV)])["ok"])
        p = persist.plan(UV, dict(self.rb(), core_offset=0), IDENT, [signoff(UV)])
        self.assertIn("something else changed it", p["reasons"][0])

    def test_unset_offsets_count_as_zero_and_power_within_half_a_watt(self):
        self.assertTrue(persist.same({"power_limit": 320}, {"core_offset": 0, "mem_offset": 0, "power_limit": 320.4}))
        self.assertFalse(persist.same({"power_limit": 320}, {"power_limit": None}))
        self.assertFalse(persist.same(UV, dict(UV, max_clock=None)))


FAKE_SMI = """#!{py}
import sys
a = sys.argv[1:]
open("{d}/calls", "a").write("smi " + " ".join(a) + "\\n")
if "-pl" in a:
    open("{d}/pl", "w").write(a[a.index("-pl") + 1])
elif "-rgc" not in a:
    print("GPU-fa9f0f08, {driver}, " + open("{d}/pl").read().strip() + ", 400.00")
"""
FAKE_OC = """#!{py}
import json, os, sys
st = json.load(open("{d}/oc.json"))
a = sys.argv[1:]
open("{d}/calls", "a").write(" ".join(a) + "\\n")
if a[0] == "get":
    print(f"GPU core clock offset: {{st['core']}} MHz")
    print(f"GPU memory clock offset: {{st['mem']}} MHz")
    sys.exit(0)
if "--max-clock" in a and os.path.exists("{d}/fail_lock"):
    sys.exit(101)
for flag, key in (("--freq-offset", "core"), ("--mem-offset", "mem")):
    if flag in a:
        v = int(a[a.index(flag) + 1])
        st[key] = v + ({drift} if v else 0)
if "--power-limit" in a:
    open("{d}/pl", "w").write(str(int(a[a.index("--power-limit") + 1]) / 1000))
json.dump(st, open("{d}/oc.json", "w"))
"""
FAKE_DMESG = """#!{py}
import os
p = "{d}/kmsg"
print(open(p).read() if os.path.exists(p) else "[    1.0] Linux version")
"""


class BootApply(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="ctune-bootapply-")
        self.cfg = os.path.join(self.d, "gpu.json")
        with open(self.cfg, "w") as f:
            json.dump({"settings": UV, **IDENT}, f)
        self.fake()

    def fake(self, driver="615.78.08", drift=0):
        with open(os.path.join(self.d, "pl"), "w") as f:
            f.write("400.00")
        with open(os.path.join(self.d, "oc.json"), "w") as f:
            json.dump({"core": 0, "mem": 0}, f)
        for name, text in (("smi", FAKE_SMI), ("oc", FAKE_OC), ("dmesg", FAKE_DMESG)):
            p = os.path.join(self.d, name)
            with open(p, "w") as f:
                f.write(text.format(py=sys.executable, d=self.d, driver=driver, drift=drift))
            os.chmod(p, 0o755)

    def run_mode(self, *mode):
        argv = [*mode, "--config", self.cfg, "--nvidia-oc", os.path.join(self.d, "oc"),
                "--nvidia-smi", os.path.join(self.d, "smi"), "--run-dir", os.path.join(self.d, "run"),
                "--state-dir", os.path.join(self.d, "state"), "--dmesg", os.path.join(self.d, "dmesg")]
        with mock.patch.object(boot_apply, "schedule_mark_good", return_value=(0, "")) as sched, \
                contextlib.redirect_stdout(io.StringIO()):
            rc = boot_apply.main(argv)
        res = None
        if os.path.exists(os.path.join(self.d, "run", "gpu-persist.json")):
            with open(os.path.join(self.d, "run", "gpu-persist.json")) as f:
                res = json.load(f)
        return rc, res, sched

    def calls(self):
        p = os.path.join(self.d, "calls")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return f.read().splitlines()

    def state(self):
        return os.path.join(self.d, "state")

    def mark_good(self):
        with contextlib.redirect_stdout(io.StringIO()):
            boot_apply.main(["--mark-good", "--state-dir", self.state(), "--run-dir", os.path.join(self.d, "run"),
                             "--dmesg", os.path.join(self.d, "dmesg")])
        return boot_apply.read_attempts(self.state())

    def xid(self, n):
        with open(os.path.join(self.d, "kmsg"), "a") as f:
            f.write("".join(f"NVRM: Xid (PCI:0000:0b:00): 13, pid={i}\n" for i in range(n)))

    def test_boot_sets_the_caps_before_the_offsets_and_reads_back(self):
        rc, res, sched = self.run_mode("--boot")
        self.assertEqual(rc, 0, res)
        self.assertTrue(res["ok"])
        self.assertEqual(res["readback"], {"core_offset": 150, "mem_offset": 0, "power_limit": 400.0})
        sets = [c for c in self.calls() if c.startswith("set")]
        self.assertEqual(sets, ["set --index 0 --power-limit 400000 --min-clock 0 --max-clock 1860",
                                "set --index 0 --freq-offset 150 --mem-offset 0"])
        self.assertEqual(boot_apply.read_attempts(self.state()), 1)
        sched.assert_called_once()

    def test_a_failed_lock_never_applies_the_offsets_and_goes_back_to_stock(self):
        open(os.path.join(self.d, "fail_lock"), "w").close()
        rc, res, sched = self.run_mode("--boot")
        self.assertEqual(rc, 1)
        self.assertTrue(res["rolled_back"])
        self.assertNotIn("set --index 0 --freq-offset 150 --mem-offset 0", self.calls())
        self.assertIn("smi -i 0 -rgc", self.calls())
        self.assertEqual(res["readback"]["core_offset"], 0)
        self.assertEqual(boot_apply.read_attempts(self.state()), 0)   # back at stock: no crash risk counted
        sched.assert_not_called()

    def test_a_readback_mismatch_goes_back_to_stock_and_fails_the_unit(self):
        self.fake(drift=15)
        rc, res, sched = self.run_mode("--boot")
        self.assertEqual(rc, 1)
        self.assertIn("core_offset: wanted 150, read 165", res["errors"])
        self.assertEqual(res["readback"], {"core_offset": 0, "mem_offset": 0, "power_limit": 400.0})
        sched.assert_not_called()

    def test_guard_counts_only_failed_boots(self):
        self.xid(2)                       # Xid errors from before the apply (earlier experiments) do not count
        self.run_mode("--boot")
        self.assertEqual(self.mark_good(), 0)
        self.run_mode("--boot")           # attempts 1 until marked; an Xid after the apply makes it a failed boot
        self.xid(1)
        self.assertEqual(self.mark_good(), 1)
        self.run_mode("--boot")
        self.assertEqual(boot_apply.read_attempts(self.state()), 2)   # crashed before any mark
        rc, res, sched = self.run_mode("--boot")
        self.assertIn("crash-loop guard", res["skipped"])
        self.assertFalse([c for c in self.calls()[-3:] if c.startswith("set")])
        sched.assert_not_called()

    def test_resume_follows_the_boot_result(self):
        boot_apply.write_attempts(self.state(), 2)
        _, res, _ = self.run_mode("--boot")
        self.assertIn("crash-loop guard", res["skipped"])
        _, res, _ = self.run_mode("--resume")
        self.assertIn("did not apply", res["skipped"])
        boot_apply.write_attempts(self.state(), 0)
        self.run_mode("--boot")
        _, res, _ = self.run_mode("--resume")
        self.assertTrue(res["ok"])

    def test_new_driver_skips_until_signed_off_again(self):
        self.fake(driver="620.10")
        rc, res, _ = self.run_mode("--boot")
        self.assertEqual(rc, 0)
        self.assertIn("driver 620.10 differs", res["skipped"])
        self.assertEqual(boot_apply.read_attempts(self.state()), 0)
        self.assertFalse([c for c in self.calls() if c.startswith("set")])

    def test_values_outside_the_caps_are_skipped(self):
        with open(self.cfg, "w") as f:
            json.dump({"settings": dict(UV, core_offset=400), **IDENT}, f)
        _, res, _ = self.run_mode("--boot")
        self.assertIn("outside ctune's cap", res["skipped"])

    def test_caps_match_the_runtime_helper(self):
        from ctune.gpu import apply as helper
        self.assertEqual((boot_apply.CAP_CORE, boot_apply.CAP_MEM, boot_apply.CAP_CLOCK),
                         (helper.CAP_CORE, helper.CAP_MEM, helper.CAP_CLOCK))

    def test_once_writes_no_result_file(self):
        argv = ["--once", self.cfg, "--nvidia-oc", os.path.join(self.d, "oc"), "--nvidia-smi",
                os.path.join(self.d, "smi"), "--run-dir", os.path.join(self.d, "run"), "--state-dir",
                self.state(), "--dmesg", os.path.join(self.d, "dmesg")]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(boot_apply.main(argv), 0)
        self.assertFalse(os.path.exists(os.path.join(self.d, "run")))
        self.assertEqual(boot_apply.read_attempts(self.state()), 0)

    def test_set_steps_without_a_clock_lock_or_power_limit(self):
        self.assertEqual(boot_apply.set_steps({"core_offset": -30, "mem_offset": 250, "max_clock": None,
                                               "power_limit": None}), [["--freq-offset", "-30", "--mem-offset", "250"]])


class Install(unittest.TestCase):
    def test_refuses_a_directory_a_user_can_swap(self):
        d = tempfile.mkdtemp(prefix="ctune-install-")
        if os.stat(d).st_uid == 0:
            self.skipTest("running as root")
        with self.assertRaises(RuntimeError):
            install.check_root_owned(os.path.join(d, "nvidia_oc"))

    def test_units_run_only_root_owned_copies(self):
        for text in install.UNIT_TEXT.values():
            exec_lines = [l for l in text.splitlines() if l.startswith("Exec")]
            self.assertTrue(exec_lines)
            for l in exec_lines:
                self.assertIn(install.APPLIER, l)
                self.assertTrue(l.split("=", 1)[1].startswith("/usr/bin/python3 -I -B /usr/local/libexec/ctune/"))
        self.assertIn("ConditionPathExists=/etc/ctune/gpu.json", install.UNIT_TEXT[install.BOOT_UNIT])


class Applied(unittest.TestCase):
    def test_runtime_changes_overlay_the_boot_unit(self):
        from ctune.gpu import api
        boot = {"ok": True, "applied": UV}
        d = tempfile.mkdtemp()
        st = os.path.join(d, "gpu_state.json")
        with open(st, "w") as f:
            json.dump({"boot_id": api._boot_id(), "power_limit": 320.0}, f)
        with mock.patch.object(persist, "boot_result", return_value=boot), mock.patch.object(api, "STATE", st):
            a = api.applied()
        self.assertEqual((a["core_offset"], a["max_clock"], a["power_limit"]), (150, 1860, 320.0))
        self.assertEqual(a["source"], "boot unit, then gpu_apply")
        with mock.patch.object(persist, "boot_result", return_value=dict(boot, epoch=os.path.getmtime(st) + 1)), \
                mock.patch.object(api, "STATE", st):
            self.assertEqual(api.applied()["power_limit"], 400.0)   # a resume re-applied after the runtime change
        with mock.patch.object(persist, "boot_result", return_value=None), mock.patch.object(api, "STATE", st + "x"):
            self.assertEqual(api.applied(), {})


if __name__ == "__main__":
    unittest.main()
