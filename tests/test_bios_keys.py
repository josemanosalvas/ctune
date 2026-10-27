#!/usr/bin/env python3
"""Offline tests for finding BIOS settings by menu location, not offset. A BIOS update moves offsets: F19c's Setup
store grows from 646 to 650 bytes and shifts 418 Tweaker questions by +4 or +5. The fixture holds the questions at
the KEY locations from the F16b runtime HII database and from the F19c image (see its _source).

Run from the repo root:  python3 -B -m unittest discover -s tests
"""
import importlib.util
import json
import os
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402,F401  (offline tests may not start podman, root helpers or stress tools)

from ctune.mem import verify  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "bios_settings_decode", os.path.join(ROOT, "ctune", "mem", "platforms", "zen3_am4", "bios_settings_decode.py"))
dec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dec)
with open(os.path.join(ROOT, "tests", "fixtures", "bios_key_rows.json")) as f:
    FIX = json.load(f)


def resolve(rows):
    res = {"rows": [dict(r) for r in rows]}
    found, problems = {}, {}
    for label, store, path, prompt, *want in dec.KEY:
        r, why = dec.find_row(res, store, path, prompt, **(want[0] if want else {}))
        if why:
            problems[label] = why
        else:
            found[label] = (r["varstore"], r["offset"])
    return found, problems


def q(path, prompt, offset, store="Setup", op="NUMERIC", width=2, visible=True):
    return dict(form_path=path, prompt=prompt, op=op, varstore=store, offset=offset, width=width, visible_now=visible)


class KeyTable(unittest.TestCase):
    def test_f16b_resolves_every_key_to_its_offset(self):
        found, problems = resolve(FIX["F16b"])
        self.assertEqual(problems, {})
        for label, at in {"tCL (Tweaker)": ("Setup", 368), "tRFC": ("Setup", 394), "XMP": ("Setup", 338),
                          "DRAM Voltage (CH A/B)": ("Setup", 508), "Above 4G Decoding": ("Setup", 642),
                          "Channel B timings (Advanced Manual only)": ("Setup", 428),
                          "CBS FCLK Frequency": ("AmdSetup", 338), "AOD VDDP Voltage Control": ("AOD_SETUP", 184),
                          "AOD VDDP [mV]": ("AOD_SETUP", 185), "PBO Scalar (AOD)": ("AOD_SETUP", 164),
                          "CO core 7 magnitude": ("AOD_SETUP", 450)}.items():
            self.assertEqual(found[label], at, label)

    def test_f19c_finds_the_moved_tweaker_settings(self):
        found, problems = resolve(FIX["F19c"])
        # AGESA adds the per-core Curve Optimizer questions at boot, so the image's forms lack them
        self.assertEqual(set(problems), {f"CO core {i} {k}" for i in range(8) for k in ("sign", "magnitude")})
        self.assertTrue(all(why == "not found" for why in problems.values()))
        for label, at in {"tCL (Tweaker)": ("Setup", 373), "tRFC": ("Setup", 399), "XMP": ("Setup", 343),
                          "DRAM Voltage (CH A/B)": ("Setup", 513), "Above 4G Decoding": ("Setup", 646),
                          "CBS FCLK Frequency": ("AmdSetup", 338), "AOD VDDP [mV]": ("AOD_SETUP", 185)}.items():
            self.assertEqual(found[label], at, label)
        old, _ = resolve(FIX["F16b"])
        shifts = {found[k][1] - old[k][1] for k in found if found[k][0] == "Setup"}
        self.assertEqual(shifts, {4, 5})

    def test_gbt_volt_prompts_exist_on_both_layouts(self):
        for name in ("F16b", "F19c"):
            prompts = {r["prompt"] for r in FIX[name] if r["form_path"] == dec.TW_VOLT and r["varstore"] == "Setup"}
            self.assertLessEqual(set(dec.GBT_VOLT), prompts, name)


class FindRow(unittest.TestCase):
    def test_not_found_and_wrong_store(self):
        res = {"rows": [q(dec.TW_MEM, "tRFC", 399)]}
        self.assertEqual(dec.find_row(res, "Setup", dec.TW_MEM, "tRFC2")[1], "not found")
        self.assertEqual(dec.find_row(res, "AmdSetup", dec.TW_MEM, "tRFC")[1], "not found")
        self.assertEqual(dec.find_row(res, "Setup", dec.TW_MEM, "tRFC")[0]["offset"], 399)

    def test_two_storages_at_one_location_are_refused_not_guessed(self):
        res = {"rows": [q(dec.AOD, "VDDP Voltage Control", 184, "AOD_SETUP", "ONE_OF", 1),
                        q(dec.AOD, "VDDP Voltage Control", 185, "AOD_SETUP", "NUMERIC", 4)]}
        r, why = dec.find_row(res, "AOD_SETUP", dec.AOD, "VDDP Voltage Control")
        self.assertIsNone(r)
        self.assertIn("ambiguous", why)
        self.assertEqual(dec.find_row(res, "AOD_SETUP", dec.AOD, "VDDP Voltage Control", op="NUMERIC")[0]["offset"], 185)
        self.assertEqual(dec.find_row(res, "AOD_SETUP", dec.AOD, "VDDP Voltage Control", width=1)[0]["offset"], 184)

    def test_prefers_the_visible_instance_of_one_storage(self):
        res = {"rows": [q(dec.TW_MEM, "tRC", 385, visible=False), q(dec.TW_MEM, "tRC", 385, visible=True)]}
        self.assertTrue(dec.find_row(res, "Setup", dec.TW_MEM, "tRC")[0]["visible_now"])

    def test_volt_text_follows_the_prompt_not_the_offset(self):
        r = q(dec.TW_VOLT, "DRAM Voltage      (CH A/B)", 513) | {"value": 55}
        self.assertTrue(dec.gbt_volt_text(r).startswith("~1.5500 V"))
        self.assertIsNone(dec.gbt_volt_text(q(dec.TW_VOLT, "tRFC", 508) | {"value": 55}))
        self.assertEqual(dec.gbt_volt_text(q(dec.TW_VOLT, "VCORE SOC", 499) | {"value": 0xFFFF}), "Auto")


class Verify(unittest.TestCase):
    def test_channel_b_timings_are_skipped(self):
        settings = [{"locations": [f"{dec.TW_SUB_B} :: CAS Latency"], "value": 65535, "auto_state": "Auto"},
                    {"locations": [f"{dec.TW_SUB_A} :: CAS Latency"], "value": 14, "auto_state": "MANUAL"}]
        self.assertEqual(verify._bios(settings, "CAS Latency"), (14, "MANUAL"))


if __name__ == "__main__":
    unittest.main()
