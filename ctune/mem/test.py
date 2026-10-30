"""RAM stability tests on Linux: stressapptest or TestMem5 over most of the free memory, with a GPU load to heat the
DIMMs.

  screen  (every step, 25 min): stressapptest -W, with memtest_vulkan as the GPU heat load.
  fabric  (FCLK steps and after a BIOS flash, 90 min, then 20 min idle): the screen with NVMe reads, then an idle
          soak, because an unstable fabric often reboots the machine at idle.
  tm5     (the timings pass): TestMem5 with the anta777 Extreme1 profile, its 3 cycles to the end, with the same GPU
          heat load. It runs as long as TM5 needs; minutes only caps it (ctune.mem.tm5).

The test is red on stressapptest or TM5 errors or hardware incidents, any new corrected machine-check error (umc
banks point at the memory, fabric banks at FCLK, VDDG or VSOC), a VRAM or Xid error from the heat load, or a crash
(recorded on the next boot). A TM5 run that cannot start or runs out of memory is inconclusive.
"""
import os
import time

from ctune.core import journal, stress
from ctune.core.paths import DATA
from ctune.mem import tm5

DEFAULT_MINUTES = {"screen": 25, "fabric": 90, "tm5": 240}   # tm5: the cap
IDLE_MINUTES = 20


def _settings():
    from ctune.mem import api as mem
    s = mem.snapshots()
    return {"snapshot": s[-1] if s else None, "bios": stress._rd("/sys/class/dmi/id/bios_version")}


def _gpu_heat(seconds):
    """(load, None) or (None, why the GPU heat load cannot run). It ends by itself 5 min after the phase if ctune dies."""
    from ctune.gpu import api as gpu
    if not os.path.exists(gpu.MEMTEST):
        return None, f"{gpu.MEMTEST} is missing"
    try:
        short = gpu.vram_free_check()
    except Exception as e:
        return None, f"GPU unavailable: {e}"
    if short:
        return None, short
    return gpu.VramLoad("gpu-heat (memtest_vulkan)", seconds + 300, optional=True), None


def ram_loads(seconds, mb, gpu_heat=True, disk=False, tester="stressapptest"):
    """(loads, notes). Without the GPU heat load the DIMMs run cooler than in a game: still a valid test."""
    loads = [tm5.Load(mb, seconds) if tester == "tm5" else
             stress.Load("stressapptest", stress.stressapptest_cmd(mb, seconds), stress.parse_sat, self_timed=True)]
    notes = []
    if gpu_heat:
        g, why = _gpu_heat(seconds)
        if g:
            loads.append(g)
        else:
            notes.append(f"no GPU heat load: {why}; the DIMMs ran cooler than in a game")
    if disk:
        loads.append(stress.DiskReader(seconds=seconds))
    return loads, notes


def test(kind="screen", minutes=None, gpu_heat=True, note="", record=True, mb=None):
    """Run a RAM screen, fabric or TM5 test and record it in the journal. mb caps the RAM it takes (default: all free
    RAM minus 1.5 GB). Returns the verdict (green, red, or inconclusive for a TM5 run that could not run) with its
    sources."""
    if kind not in DEFAULT_MINUTES:
        raise ValueError("kind is 'screen', 'fabric' or 'tm5'")
    if kind == "tm5" and tm5.unavailable():
        raise FileNotFoundError(tm5.unavailable())
    if kind != "tm5" and not os.access(stress.STRESSAPPTEST, os.X_OK):
        raise FileNotFoundError(f"{stress.STRESSAPPTEST} is missing: build stressapptest v1.0.11 into tools/ (README)")
    stress.check_interrupted()
    minutes = minutes or DEFAULT_MINUTES[kind]
    seconds = int(minutes * 60)
    mi = stress.meminfo()
    mb = min(mb, stress.test_size_mb()) if mb else stress.test_size_mb()
    res = {"test": f"mem-{kind}", "minutes": minutes, "tested_mb": mb,
           "coverage_pct": round(100 * mb / max(1, mi.get("MemTotal", 1)), 1),
           "swap_used_mb_at_start": mi.get("SwapTotal", 0) - mi.get("SwapFree", 0), "settings": _settings()}
    with stress.run("mem", kind, res["settings"]) as r, stress.telemetry() as tel:
        snap = stress.error_snapshot()
        r.update(phase="load", minutes=minutes)
        loads, res["notes"] = ram_loads(seconds, mb, gpu_heat, disk=(kind == "fabric"),
                                        tester="tm5" if kind == "tm5" else "stressapptest")
        res.update(stress.run_loads(loads, seconds))
        if kind == "tm5":
            res["tested_mb"] = loads[0].tested_mb
            res["coverage_pct"] = round(100 * res["tested_mb"] / max(1, mi.get("MemTotal", 1)), 1)
        res["notes"] += [f"{l['name']} did not run: {l['errors'][0]}" for l in res["loads"] if l.get("skipped")]
        if kind == "fabric" and not res["early_exit"]:
            r.update(phase="idle", minutes=IDLE_MINUTES)
            time.sleep(IDLE_MINUTES * 60)
        res["errors"] = stress.error_delta(snap)
    res["telemetry"] = tel.summary()
    res["verdict"] = "green" if all(l["ok"] for l in res["loads"]) and res["errors"]["clean"] else "red"
    res["red_sources"] = [l["name"] for l in res["loads"] if not l["ok"]] + list(res["errors"]["by_subsystem"])
    if res["verdict"] == "red" and res["errors"]["clean"] and all(
            l.get("unavailable") for l in res["loads"] if not l["ok"]):
        res["verdict"], res["red_sources"] = "inconclusive", []   # it never tested: the setup, not the RAM
    if res["errors"]["kernel_hw_errors"]:
        res["red_sources"].append("kernel hardware-error lines")
    if res["coverage_pct"] < 70:
        res["coverage_note"] = "less than 70 % of RAM was free to test; close large apps before a sign-off"
    if record:
        journal.add(DATA, "result", domain="mem", text=note or f"mem {kind} test", **res)
    return res
