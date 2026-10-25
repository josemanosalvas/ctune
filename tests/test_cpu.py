#!/usr/bin/env python3
"""Offline tests for the cpu domain: SMU argument encoding, guardrails, mailbox byte layout, pm_table parsing,
BIOS Curve Optimizer decoding, per-CPU machine-check deltas, stress-ng parsing and the crash marker. No root, no SMU.

Run from the repo root:  python3 -B -m unittest discover -s tests
"""
import json
import os
import struct
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
_DATA = tempfile.mkdtemp(prefix="ctune-test-data-")
os.environ["CTUNE_DATA"] = _DATA

from ctune.core import errors, stress  # noqa: E402
from ctune.cpu import api, smu  # noqa: E402


class Encoding(unittest.TestCase):
    def test_set_word_matches_sources(self):
        # ZenStates-Core SetPsmMarginSingleCore / MakePsmMarginArg and ruv.py L114 build the same words
        self.assertEqual(smu.set_arg(0, -20), 0x0000FFEC)
        self.assertEqual(smu.set_arg(1, -30), 0x0010FFE2)
        self.assertEqual(smu.set_arg(7, 5), 0x00700005)
        self.assertEqual(smu.set_arg(3, 0), 0x00300000)
        self.assertEqual(smu.set_arg(4, 30), 0x0040001E)

    def test_same_as_zenstates_formula(self):
        for core in range(8):
            for m in range(-30, 31):
                zsc = (core << 20) | (((0x100000 + m) if m < 0 else m) & 0xFFFF)
                ruv = ((core & 8) << 5 | core & 7) << 20 | m & 0xFFFF
                self.assertEqual(smu.set_arg(core, m), zsc)
                self.assertEqual(smu.set_arg(core, m), ruv)

    def test_get_word(self):
        self.assertEqual(smu.get_arg(0), 0)
        self.assertEqual(smu.get_arg(5), 0x00500000)

    def test_bits_19_16_stay_clear(self):
        for m in range(-30, 31):
            self.assertEqual(smu.set_arg(2, m) & 0x000F0000, 0)

    def test_rejects_out_of_range(self):
        for bad in (-31, 31, 100, -100):
            with self.assertRaises(ValueError):
                smu.set_arg(0, bad)
        for bad in (-1, 8, 16):
            with self.assertRaises(ValueError):
                smu.set_arg(bad, 0)
        for bad in (1.0, "1", True, None):
            with self.assertRaises(ValueError):
                smu.set_arg(0, bad)
            with self.assertRaises(ValueError):
                smu.set_arg(bad, 0)

    def test_decode_readback(self):
        self.assertEqual(smu.decode_margin(0xFFFFFFE2), -30)
        self.assertEqual(smu.decode_margin(0xFFFFFFEC), -20)
        self.assertEqual(smu.decode_margin(0x00000014), 20)
        self.assertEqual(smu.decode_margin(0), 0)
        # a 16-bit form is not what ruv.py documents: decode literally, then the range check rejects it
        self.assertEqual(smu.decode_margin(0x0000FFE2), 0xFFE2)
        with self.assertRaises(smu.SmuError):
            smu.check_readback(0x0000FFE2)
        self.assertEqual(smu.check_readback(0xFFFFFFE2), -30)


class Guardrails(unittest.TestCase):
    def test_step_limit(self):
        self.assertEqual(smu.validate_change(0, -20, -25), [])
        self.assertEqual(smu.validate_change(0, -20, -15), [])
        self.assertEqual(smu.validate_change(1, -30, -30), [])
        self.assertTrue(smu.validate_change(0, -20, -26))
        self.assertTrue(smu.validate_change(0, -20, -14))

    def test_range(self):
        self.assertTrue(smu.validate_change(1, -30, -31))
        self.assertTrue(smu.validate_change(1, 28, 31))
        self.assertTrue(smu.validate_change(8, 0, 0))

    def test_bad_current(self):
        self.assertTrue(smu.validate_change(0, None, -20))
        self.assertTrue(smu.validate_change(0, 65506, -20))


class FakeSysfs:
    def __init__(self, status=1, response=(0, 0, 0, 0, 0, 0)):
        self.dir = tempfile.mkdtemp(prefix="ctune-fake-smu-")
        self.status, self.response = status, response
        for n in ("smu_args", "mp1_smu_cmd"):
            open(os.path.join(self.dir, n), "wb").close()
        self.writes = []

    def write(self, name, data):
        self.writes.append((name, bytes(data)))
        return len(data)

    def read(self, name, n):
        if name == "mp1_smu_cmd":
            return struct.pack("<I", self.status)
        return struct.pack("<6I", *self.response)


class Mailbox(unittest.TestCase):
    def mb(self, fake):
        return smu.Mailbox(io=fake)

    def test_byte_layout(self):
        fake = FakeSysfs()
        self.mb(fake).send(smu.MP1_SET_CORE, smu.set_arg(1, -30))
        self.assertEqual(fake.writes[0], ("smu_args", struct.pack("<6I", 0x0010FFE2, 0, 0, 0, 0, 0)))
        self.assertEqual(fake.writes[1], ("mp1_smu_cmd", struct.pack("<I", 0x35)))
        self.assertEqual(len(fake.writes), 2)

    def test_only_whitelisted_ids(self):
        fake = FakeSysfs()
        for op in (0x36, 0x34, 0x7C, 0x0A, 0x00, 0x49):
            with self.assertRaises(smu.SmuError):
                self.mb(fake).send(op, 0)
        self.assertEqual(fake.writes, [])

    def test_status_not_ok(self):
        with self.assertRaises(smu.SmuError):
            self.mb(FakeSysfs(status=0xFB)).send(smu.MP1_GET_CORE, 0)

    def test_read_margin(self):
        fake = FakeSysfs(response=(0xFFFFFFE2, 0, 0, 0, 0, 0))
        self.assertEqual(self.mb(fake).read_margin(1), -30)
        self.assertEqual(fake.writes[0], ("smu_args", struct.pack("<6I", 0x00100000, 0, 0, 0, 0, 0)))
        self.assertEqual(fake.writes[1], ("mp1_smu_cmd", struct.pack("<I", 0x48)))

    def test_write_margin_validates_first(self):
        fake = FakeSysfs()
        with self.assertRaises(ValueError):
            self.mb(fake).write_margin(0, -31)
        self.assertEqual(fake.writes, [])


class FakeSmuIO:
    """Simulates the SMU behind ryzen_smu's sysfs files: 0x48 returns a sign-extended margin, 0x35 sets one
    (clamped to +-30, as ruv.py's README reports)."""

    def __init__(self, margins):
        self.margins, self.args, self.resp, self.sent = list(margins), None, (0,) * 6, []

    def write(self, name, data):
        if name == "smu_args":
            self.args = struct.unpack("<6I", data)
        else:
            op = struct.unpack("<I", data)[0]
            a0 = self.args[0]
            core = (a0 >> 20) & 0xF
            self.sent.append((op, a0))
            if op == 0x48:
                self.resp = (self.margins[core] & 0xFFFFFFFF, 0, 0, 0, 0, 0)
            elif op == 0x35:
                m = a0 & 0xFFFF
                m = m - 0x10000 if m >= 0x8000 else m
                self.margins[core] = max(-30, min(30, m))
                self.resp = self.args
        return len(data)

    def read(self, name, n):
        return struct.pack("<I", 1) if name == "mp1_smu_cmd" else struct.pack("<6I", *self.resp)


class Helper(unittest.TestCase):
    BIOS = [-20, -30, -30, -20, -20, -30, -30, -30]

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("cpu_apply", os.path.join(REPO, "ctune", "cpu", "apply.py"))
        self.h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.h)
        self.io = FakeSmuIO(self.BIOS)
        self.mb = smu.Mailbox(io=self.io)
        self.state = {}

    def test_no_write_without_probe(self):
        errs, entry = self.h.set_core(self.mb, self.state, 1, -30)
        self.assertTrue(errs)
        self.assertIsNone(entry)
        self.assertNotIn(0x35, [op for op, _ in self.io.sent])

    def test_probe_mismatch_blocks_writes(self):
        p = self.h.probe(self.mb, self.state, 8, [-20] * 8)
        self.assertFalse(p["matched"])
        errs, _ = self.h.set_core(self.mb, self.state, 1, -30)
        self.assertTrue(errs)
        self.assertNotIn(0x35, [op for op, _ in self.io.sent])

    def test_noop_then_step_then_reset(self):
        self.assertTrue(self.h.probe(self.mb, self.state, 8, self.BIOS)["matched"])
        errs, e = self.h.set_core(self.mb, self.state, 1, -30)          # the no-op
        self.assertEqual(errs, [])
        self.assertEqual((e["before"], e["readback"]), (-30, -30))
        self.assertIn((0x35, 0x0010FFE2), self.io.sent)
        errs, e = self.h.set_core(self.mb, self.state, 0, -25)
        self.assertEqual(errs, [])
        self.assertEqual(self.io.margins[0], -25)
        errs, _ = self.h.set_core(self.mb, self.state, 0, -31)
        self.assertTrue(errs)
        errs, _ = self.h.set_core(self.mb, self.state, 3, -26)          # 6 from -20
        self.assertTrue(errs)
        self.assertEqual(self.io.margins[3], -20)
        # a second probe in the same boot expects ctune's values on the cores it changed
        self.assertTrue(self.h.probe(self.mb, self.state, 8, self.BIOS)["matched"])
        errs, done = self.h.reset(self.mb, self.state)
        self.assertEqual(errs, [])
        self.assertEqual(self.io.margins, self.BIOS)
        self.assertEqual(self.state["applied"], {})

    def test_outside_change_is_refused(self):
        self.h.probe(self.mb, self.state, 8, self.BIOS)
        self.io.margins[4] = -10                                         # another tool wrote core 4
        errs, entry = self.h.set_core(self.mb, self.state, 4, -15)
        self.assertTrue(errs)
        self.assertIsNone(entry)

    def test_readback_mismatch_is_reported(self):
        self.h.probe(self.mb, self.state, 8, self.BIOS)
        real = self.io.write

        def ignore_sets(name, data):          # the SMU acknowledges 0x35 but keeps the old value
            if name == "mp1_smu_cmd" and struct.unpack("<I", data)[0] == 0x35:
                return len(data)
            return real(name, data)
        self.io.write = ignore_sets
        errs, entry = self.h.set_core(self.mb, self.state, 0, -25)
        self.assertTrue(errs)
        self.assertEqual(entry["readback"], -20)
        self.assertEqual(self.state["applied"]["0"], -20)


class PmTable(unittest.TestCase):
    def test_parse(self):
        b = bytearray(1488)
        for i in range(8):
            struct.pack_into("<f", b, 0x2D0 + 4 * i, 1.0 + i / 100)
            struct.pack_into("<f", b, 0x350 + 4 * i, 4.5 - i / 10)
            struct.pack_into("<f", b, 0x390 + 4 * i, 50.0)
        struct.pack_into("<f", b, 0x004, 33.5)
        t = smu.parse_pm(bytes(b))
        self.assertAlmostEqual(t["core_voltage_v"][3], 1.03, places=5)
        self.assertAlmostEqual(t["core_freq_ghz"][0], 4.5, places=5)
        self.assertAlmostEqual(t["core_c0_pct"][7], 50.0, places=5)
        self.assertAlmostEqual(t["ppt_w"], 33.5, places=5)

    def test_short_table(self):
        with self.assertRaises(smu.SmuError):
            smu.parse_pm(b"\0" * 100)


def _settings(mode, per_core=None, all_core=None):
    rows = [{"locations": ["AMD Overclocking Option > Curve Optimizer :: Curve Optimizer"], "value_text": mode}]
    if all_core is not None:
        rows += [{"locations": ["AMD Overclocking Option > Curve Optimizer :: All Core Curve Optimizer Sign"],
                  "value_text": "Negative" if all_core < 0 else "Positive"},
                 {"locations": ["AMD Overclocking Option > Curve Optimizer :: All Core Curve Optimizer Magnitude"],
                  "value_text": str(abs(all_core))}]
    for i, v in enumerate(per_core or []):
        rows += [{"locations": [f"AMD Overclocking Option > Curve Optimizer :: Core {i} Curve Optimizer Sign"],
                  "value_text": "Negative" if v < 0 else "Positive"},
                 {"locations": [f"AMD Overclocking Option > Curve Optimizer :: Core {i} Curve Optimizer Magnitude"],
                  "value_text": str(abs(v))}]
    rows.append({"locations": ["AMD Overclocking Option > GFX Curve Optimizer :: GFX Curve Optimizer"],
                 "value_text": "Disabled"})
    return rows


class BiosCo(unittest.TestCase):
    MACHINE = [-20, -30, -30, -20, -20, -30, -30, -30]

    def test_per_core(self):
        r = api.decode_bios_co(_settings("Per Core", self.MACHINE, all_core=-30))
        self.assertEqual(r["mode"], "Per Core")
        self.assertEqual(r["cores"], self.MACHINE)

    def test_all_cores(self):
        r = api.decode_bios_co(_settings("All Cores", self.MACHINE, all_core=-15))
        self.assertEqual(r["cores"], [-15] * 8)

    def test_disabled(self):
        r = api.decode_bios_co(_settings("Disabled", self.MACHINE, all_core=-15))
        self.assertEqual(r["cores"], [0] * 8)

    def test_positive(self):
        r = api.decode_bios_co(_settings("Per Core", [5, 0, -3, 0, 0, 0, 0, 0]))
        self.assertEqual(r["cores"][:3], [5, 0, -3])


def _put(path, text):
    with open(path, "w") as f:
        f.write(text)


class MachineCheckPerCpu(unittest.TestCase):
    def test_counts_and_delta(self):
        root = tempfile.mkdtemp(prefix="ctune-fake-mce-")
        for cpu in (0, 1, 8):
            for bank in ("load_store", "l2_cache", "umc_0", "l3_cache_0"):
                d = os.path.join(root, f"machinecheck{cpu}", bank, bank)
                os.makedirs(d)
                _put(os.path.join(d, "error_count"), "0\n")
        before = errors.counts_by_cpu(root=root)
        self.assertEqual(sorted(before), [0, 1, 8])
        self.assertNotIn("umc_0", before[0])
        _put(os.path.join(root, "machinecheck1", "l2_cache", "l2_cache", "error_count"), "2\n")
        _put(os.path.join(root, "machinecheck0", "l3_cache_0", "l3_cache_0", "error_count"), "1\n")
        delta = errors.delta_by_cpu(before, errors.counts_by_cpu(root=root))
        self.assertEqual(delta, {1: {"l2_cache": 2}, 0: {"l3_cache_0": 1}})


STRESS_OK = """stress-ng: metrc: [1097995] stressor       bogo ops real time  usr time  sys time   bogo ops/s     bogo ops/s
stress-ng: metrc: [1097995]                           (secs)    (secs)    (secs)   (real time) (usr+sys time)
stress-ng: metrc: [1097995] cpu                1944      3.00      0.57      0.03       648.01        3231.03
stress-ng: info:  [1097995] passed: 1: cpu (1)
stress-ng: info:  [1097995] failed: 0
"""
STRESS_FAIL = """stress-ng: fail:  [2000] cpu: fft: verification failed
stress-ng: metrc: [1999] cpu                 100      3.00      2.99      0.00        33.33          33.44
stress-ng: info:  [1999] failed: 1: cpu (1)
"""


class StressParse(unittest.TestCase):
    def test_ok(self):
        r = api.parse_stress(0, STRESS_OK)
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["bogo_ops_s"], 648.01)

    def test_verify_fail(self):
        r = api.parse_stress(2, STRESS_FAIL)
        self.assertFalse(r["ok"])
        self.assertIn("verification failed", r["errors"][0])

    def test_fail_line_with_zero_exit(self):
        self.assertFalse(api.parse_stress(0, STRESS_FAIL)["ok"])

    def test_signal(self):
        self.assertFalse(api.parse_stress(-11, "")["ok"])


class CrashMarker(unittest.TestCase):
    def test_interrupted_test_becomes_red(self):
        stress.marker_write({"boot_id": "old-boot", "pid": 1, "domain": "cpu", "test": "co-cycle", "core": 3,
                             "phase": "fft", "settings": {"applied": {"3": -25}}})
        rec = stress.check_interrupted(boot="new-boot")
        self.assertEqual(rec["verdict"], "red")
        self.assertEqual(rec["core"], 3)
        self.assertFalse(os.path.exists(stress.MARKER))
        with open(os.path.join(_DATA, "journal.jsonl")) as f:
            last = [json.loads(l) for l in f][-1]
        self.assertEqual((last["kind"], last["domain"]), ("result", "cpu"))

    def test_same_boot_running_is_left_alone(self):
        stress.marker_write({"boot_id": "this-boot", "pid": os.getpid(), "domain": "cpu", "test": "co-cycle"})
        self.assertIsNone(stress.check_interrupted(boot="this-boot"))
        self.assertTrue(os.path.exists(stress.MARKER))
        os.remove(stress.MARKER)

    def test_same_boot_dead_process_is_a_note(self):
        stress.marker_write({"boot_id": "this-boot", "pid": 2 ** 22 + 12345, "domain": "mem", "test": "screen"})
        rec = stress.check_interrupted(boot="this-boot")
        self.assertEqual(rec["kind"], "note")
        self.assertNotIn("verdict", rec)

    def test_nested_run_reports_into_outer_marker(self):
        with stress.run("signoff", "signoff") as outer:
            outer.update(phase="cpu")
            with stress.run("cpu", "co-cycle") as inner:
                inner.update(phase="fft", core=2)
                m = stress.marker_read()
                self.assertEqual((m["domain"], m["phase"], m["sub"], m["core"]), ("signoff", "cpu", "co-cycle:fft", 2))
                inner.update(phase="int64", core=3)
                self.assertEqual(stress.marker_read()["sub"], "co-cycle:int64")
            self.assertTrue(os.path.exists(stress.MARKER))   # the inner run leaves the outer marker in place
            outer.update(phase="gpu")
            self.assertNotIn("sub", stress.marker_read())
            with stress.run("gpu", "gpu-screen") as g:
                with stress.run("x", "deep") as d:
                    d.update(phase="p1")
                    self.assertEqual(stress.marker_read()["sub"], "gpu-screen:start > deep:p1")
        self.assertFalse(os.path.exists(stress.MARKER))


class DryRun(unittest.TestCase):
    def test_plan_uses_bios_when_nothing_applied(self):
        p = api.plan(0, -25, effective={"cores": [-20, -30, -30, -20, -20, -30, -30, -30], "source": "bios"})
        self.assertTrue(p["ok"])
        self.assertEqual(p["mailbox"], {"op": "0x35", "arg0": "0x0000ffe7"})

    def test_plan_rejects_big_step(self):
        p = api.plan(0, -30, effective={"cores": [-20] * 8, "source": "bios"})
        self.assertFalse(p["ok"])


if __name__ == "__main__":
    unittest.main()
