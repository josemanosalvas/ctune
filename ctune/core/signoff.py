"""Sign-off: the multi-hour suite that ends a campaign, run against the configuration of every domain at once.

Phases, in order (minutes are defaults):
  ram       60  stressapptest -W over the free RAM, memtest_vulkan heating the case
  ycruncher 45  y-cruncher stress, all tests on all threads with most of the free RAM (AVX2, IMC, VRM load)
  cpu       ~50 one self-checking stress-ng thread per core in random order, idle gaps (Curve Optimizer)
  gpu       20  vkpeak runs, then memtest_vulkan (core and VRAM)
  combined  30  stressapptest + memtest_vulkan + NVMe reads at once (fabric and PCIe)
  idle      30  nothing running; an unstable fabric or Curve Optimizer often freezes or reboots here

Each phase is red on its own load errors or any new corrected machine-check, kernel hardware-error or Xid line.
The run stops at the first red phase unless keep_going. A crash is recorded as red on the next boot, naming the phase.
"""
import os
import time

from ctune.core import journal, stress
from ctune.core.paths import DATA

PHASES = ("ram", "ycruncher", "cpu", "gpu", "combined", "idle")
MINUTES = {"ram": 60, "ycruncher": 45, "cpu": 50, "gpu": 20, "combined": 30, "idle": 30}


def _settings():
    out = {}
    try:
        from ctune.mem import test as memtest
        out["mem"] = memtest._settings()
    except Exception as e:
        out["mem"] = f"unavailable: {e}"
    try:
        from ctune.cpu import api as cpu
        c = cpu.current()
        out["cpu"] = {"margins": c["cores"], "source": c["source"]}
    except Exception as e:
        out["cpu"] = f"unavailable: {e}"
    try:
        from ctune.gpu import api as gpu
        out["gpu"] = gpu.applied() or "defaults"
    except Exception as e:
        out["gpu"] = f"unavailable: {e}"
    return out


def _loads_phase(loads, seconds, notes=()):
    snap = stress.error_snapshot()
    with stress.telemetry() as tel:
        r = stress.run_loads(loads, seconds)
    r["notes"] = list(notes) + [f"{l['name']} did not run: {l['errors'][0]}" for l in r["loads"] if l.get("skipped")]
    r["errors"] = stress.error_delta(snap)
    r["telemetry"] = tel.summary()
    r["ok"] = all(l["ok"] for l in r["loads"]) and r["errors"]["clean"]
    return r


def _phase(name, minutes, mb_cap=None):
    seconds = int(minutes * 60)
    cap = (lambda mb: min(mb, mb_cap)) if mb_cap else (lambda mb: mb)
    if name == "ram":
        from ctune.mem import test as memtest
        mb = cap(stress.test_size_mb())
        loads, notes = memtest.ram_loads(seconds, mb)
        r = _loads_phase(loads, seconds, notes)
        r["tested_mb"] = mb
        return r
    if name == "ycruncher":
        if not os.access(stress.YCRUNCHER, os.X_OK):
            return {"ok": False, "errors": [f"{stress.YCRUNCHER} is missing (README)"]}
        mb = cap(stress.test_size_mb(reserve_mb=2048))
        r = _loads_phase([stress.Load("y-cruncher", stress.ycruncher_cmd(mb, seconds), stress.parse_ycruncher,
                                      cwd=os.path.dirname(stress.YCRUNCHER))], seconds)
        r["tested_mb"] = mb
        return r
    if name == "cpu":
        from ctune.cpu import api as cpu
        ncores = len(cpu.smu.topology()[0])
        per_core = max(60, int(seconds / ncores))
        snap = stress.error_snapshot()
        r = cpu.test(None, per_core, order="random", record=False)
        r["errors"] = stress.error_delta(snap)
        r["ok"] = r["verdict"] == "green" and r["errors"]["clean"]
        return r
    if name == "gpu":
        from ctune.gpu import api as gpu
        snap = stress.error_snapshot()
        r = gpu.test("screen", vram_seconds=max(60, seconds - 6 * 60), core_runs=3, record=False)
        r["errors"] = stress.error_delta(snap)
        r["ok"] = r["verdict"] == "green" and r["errors"]["clean"]
        return r
    if name == "combined":
        from ctune.mem import test as memtest
        mb = cap(stress.test_size_mb(reserve_mb=2048, share=0.9))
        loads, notes = memtest.ram_loads(seconds, mb, gpu_heat=True, disk=True)
        r = _loads_phase(loads, seconds, notes)
        r["tested_mb"] = mb
        return r
    if name == "idle":
        snap = stress.error_snapshot()
        with stress.telemetry(interval=30) as tel:
            time.sleep(seconds)
        r = {"errors": stress.error_delta(snap), "telemetry": tel.summary()}
        r["ok"] = r["errors"]["clean"]
        return r
    raise ValueError(f"unknown phase {name}")


def run(phases=None, minutes=None, keep_going=False, note="", mb=None):
    """Run the sign-off phases in order and record one journal result. minutes overrides per phase, e.g. {"ram": 120};
    mb caps the RAM the memory phases take."""
    phases = list(phases or PHASES)
    bad = [p for p in phases if p not in PHASES]
    if bad:
        raise ValueError(f"unknown phases {bad}; choose from {list(PHASES)}")
    mins = {**MINUTES, **(minutes or {})}
    stress.check_interrupted()
    settings = _settings()
    res = {"test": "signoff", "mb_cap": mb, "phases": phases, "minutes": {p: mins[p] for p in phases}, "settings": settings,
           "started": stress.now(), "results": {}}
    t0 = time.time()
    with stress.run("signoff", "signoff", settings) as r:
        for p in phases:
            r.update(phase=p, minutes=mins[p], phase_started=stress.now())
            try:
                res["results"][p] = _phase(p, mins[p], mb)
            except Exception as e:
                res["results"][p] = {"ok": False, "errors": [f"{type(e).__name__}: {e}"]}
            if not res["results"][p]["ok"] and not keep_going:
                break
    res["duration_min"] = round((time.time() - t0) / 60, 1)
    res["red_phases"] = [p for p, v in res["results"].items() if not v["ok"]]
    res["skipped_phases"] = [p for p in phases if p not in res["results"]]
    res["verdict"] = "green" if not res["red_phases"] and not res["skipped_phases"] else "red"
    journal.add(DATA, "result", domain="signoff", text=note or "sign-off", **res)
    return res
