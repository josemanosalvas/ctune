"""CPU (Curve Optimizer) API: status, probe/apply/reset (root helper via pkexec), per-core stability test with telemetry.

Runtime margins last until reboot, so a crash or reboot puts the BIOS values back. A test that the machine did not
survive leaves the shared crash marker (ctune.core.stress); the next ctune call in a new boot records it as red.
"""
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time

from ctune.core import errors, journal, stress
from ctune.core.paths import DATA
from ctune.cpu import mprime, smu

STATE = os.path.join(DATA, "cpu_state.json")
HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apply.py")

# The default per-core load is mprime (Prime95) in CoreCycler's modes (ctune/cpu/mprime.py). The "stress-ng" workload
# is the older alternative: one stress-ng stressor at a time, one thread, pinned to the first logical CPU. Every
# stressor here checks its own results with --verify (`stress-ng --verifiable`). The mix covers integer, FP, wide
# vector, FMA and hash work. "bursty" runs at 25 % load in random busy slices of 0 to 0.5 s, so the core keeps
# waking from idle and ramping into boost, which is where Curve Optimizer errors show up (fixed 10 to 50 ms slices
# never got past 3.7 GHz in the pm_table here; random slices reached 4.2).
PHASES = [
    ("fft", ["--cpu", "1", "--cpu-method", "fft"]),
    ("int64", ["--cpu", "1", "--cpu-method", "int64"]),
    ("matrix", ["--matrix", "1"]),
    ("vecwide", ["--vecwide", "1"]),
    ("fma", ["--fma", "1"]),
    ("hash", ["--hash", "1"]),
    ("bursty", ["--cpu", "1", "--cpu-method", "int64", "--cpu-load", "25", "--cpu-load-slice", "0"]),
]
IDLE_GAP_S = 3          # idle between phases: a load-to-idle transition
BETWEEN_CORES_S = 15    # idle between cores (CoreCycler delayBetweenCores)
WORKLOADS = sorted(mprime.MODES) + ["stress-ng"]
NCORES = 8


def _boot_id():
    return _rd("/proc/sys/kernel/random/boot_id")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _rd(p):
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def _json(p):
    with open(p) as f:
        return json.load(f)


def applied():
    """What ctune read and wrote through the SMU since this boot (probe, boot values, applied margins, history)."""
    try:
        s = _json(STATE)
        return s if s.get("boot_id") == _boot_id() else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- BIOS Curve Optimizer (from a mem snapshot)
def decode_bios_co(settings, ncores=NCORES):
    """Signed per-core margins from the decoded BIOS settings (AMD Overclocking > Curve Optimizer)."""
    val = {}
    for e in settings:
        for loc in e.get("locations") or []:
            if loc.startswith("AMD Overclocking Option > Curve Optimizer :: "):
                val[loc.split(" :: ", 1)[1]] = e.get("value_text") if e.get("value_text") is not None else e.get("value")

    def signed(prefix):
        mag, sign = val.get(f"{prefix} Curve Optimizer Magnitude"), val.get(f"{prefix} Curve Optimizer Sign")
        if mag is None:
            return None
        return -int(mag) if str(sign) == "Negative" else int(mag)

    mode = val.get("Curve Optimizer")
    if mode == "Per Core":
        cores = [signed(f"Core {i}") for i in range(ncores)]
    elif mode == "All Cores":
        cores = [signed("All Core")] * ncores
    elif mode == "Disabled":
        cores = [0] * ncores
    else:
        cores = [None] * ncores
    return {"mode": mode, "cores": cores}


def bios_co(name="latest"):
    from ctune.mem import api as mem
    d = mem.resolve(name)
    with open(os.path.join(d, "decoded", "bios_settings.json")) as f:
        r = decode_bios_co(json.load(f)["settings"])
    meta = _json(os.path.join(d, "snapshot.json")).get("meta", {})
    r.update(snapshot=os.path.basename(d), snapshot_bios=meta.get("bios_version"), captured=meta.get("captured"))
    r["running_bios"] = _rd("/sys/class/dmi/id/bios_version")
    if r["snapshot_bios"] != r["running_bios"]:
        r["warning"] = (f"snapshot is from BIOS {r['snapshot_bios']}, running {r['running_bios']}: take a mem snapshot "
                        "after re-entering the BIOS settings")
    return r


def current():
    """Effective margin per core: what ctune applied this boot, otherwise the BIOS value from the latest snapshot."""
    try:
        b = bios_co()
    except (OSError, ValueError, KeyError) as e:
        b = {"cores": [None] * NCORES, "error": f"{type(e).__name__}: {e}"}
    cores, source = list(b["cores"]), ["bios"] * len(b["cores"])
    for k, v in applied().get("applied", {}).items():
        cores[int(k)], source[int(k)] = v, "ctune"
    return {"cores": cores, "source": source, "bios": b}


# ---------------------------------------------------------------- telemetry
def telemetry(samples=3, interval=0.3):
    """Median of a few pm_table reads: per-core clocks, voltage, power, temperature, C0 residency."""
    reads = []
    for i in range(samples):
        if i:
            time.sleep(interval)
        reads.append(smu.read_pm())
    out = {}
    for k in reads[0]:
        if isinstance(reads[0][k], list):
            out[k] = [round(statistics.median(r[k][c] for r in reads), 3) for c in range(len(reads[0][k]))]
        else:
            out[k] = round(statistics.median(r[k] for r in reads), 2)
    return out


def _cpufreq():
    p = "/sys/devices/system/cpu/cpu0/cpufreq"
    return {"driver": _rd(f"{p}/scaling_driver"), "governor": _rd(f"{p}/scaling_governor"),
            "epp": _rd(f"{p}/energy_performance_preference")}


def status():
    interrupted = check_interrupted()
    info, problems = smu.platform()
    cur = current()
    st = applied()
    try:
        t = telemetry()
        rows = [{"core": c, "co": cur["cores"][c], "co_source": cur["source"][c],
                 "freq_ghz": t["core_freq_ghz"][c], "eff_ghz": t["core_eff_ghz"][c],
                 "voltage_v": t["core_voltage_v"][c], "power_w": t["core_power_w"][c],
                 "temp_c": t["core_temp_c"][c], "c0_pct": t["core_c0_pct"][c]} for c in range(len(cur["cores"]))]
        package = {k: t[k] for k in smu.PM_SCALAR}
    except (smu.SmuError, OSError) as e:
        rows, package = [{"core": c, "co": cur["cores"][c], "co_source": cur["source"][c]}
                         for c in range(len(cur["cores"]))], {"error": str(e)}
    core_mce = {cpu: {b: n for b, n in banks.items() if n} for cpu, banks in errors.counts_by_cpu().items()}
    return {
        "platform": {**info, "cores": {c: ",".join(map(str, cpus)) for c, cpus in info.get("cores", {}).items()}},
        "platform_problems": problems,
        "bios_co": cur["bios"],
        "applied_by_ctune": {k: st.get(k) for k in ("probe", "boot_values", "applied", "smu_fw", "ts") if k in st},
        "cores": rows, "package": package, "cpufreq": _cpufreq(),
        "core_mce_since_boot": {cpu: b for cpu, b in core_mce.items() if b},
        "test_running": stress.running(),
        "interrupted_test_recorded": interrupted,
    }


# ---------------------------------------------------------------- root helper
def _helper(args):
    cmd = [sys.executable, "-I", "-B", HELPER, "--state", STATE, "--owner", f"{os.getuid()}:{os.getgid()}"] + args
    if os.geteuid() != 0:
        cmd = ["pkexec"] + cmd
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise RuntimeError("the root password prompt was not answered within 10 minutes; nothing was changed")
    if r.returncode in (126, 127) and os.geteuid() != 0:
        raise RuntimeError("the root password prompt was dismissed; nothing was changed")
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError(f"helper failed ({r.returncode}): {r.stderr.strip()[-600:]}")


def probe():
    """Read every core's margin through the SMU (MP1 0x48, read-only) and compare with the BIOS values. A match is
    what allows writes in this boot."""
    check_interrupted()
    b = bios_co()
    if None in b["cores"]:
        raise ValueError(f"the latest snapshot has no complete Curve Optimizer setting ({b['mode']}): take a mem snapshot")
    out = _helper(["--probe", "--expect", ",".join(str(v) for v in b["cores"])])
    journal.add(DATA, "note", domain="cpu", text="cpu probe (SMU getter vs BIOS)", ok=out["ok"],
                probe=out.get("probe"), errors=out.get("errors"), bios_snapshot=b["snapshot"],
                smu_fw=(out.get("platform") or {}).get("smu_fw"))
    out["bios_co"] = b
    return out


def plan(core, margin, effective=None):
    """Dry run: guardrails against the effective margins and the exact mailbox word. Sends nothing."""
    cur = effective or current()
    before = cur["cores"][core] if isinstance(core, int) and 0 <= core < len(cur["cores"]) else None
    errs = smu.validate_change(core, before, margin)
    p = {"ok": not errs, "errors": errs, "core": core, "from": before, "to": margin}
    if not errs:
        p["mailbox"] = {"op": hex(smu.MP1_SET_CORE), "arg0": f"{smu.set_arg(core, margin):#010x}"}
        p["readback"] = {"op": hex(smu.MP1_GET_CORE), "arg0": f"{smu.get_arg(core):#010x}"}
    return p


def _core_now(core, seconds=3):
    try:
        t = telemetry(samples=max(1, seconds * 2), interval=0.5)
        return {k: t[k][core] for k in smu.PM_CORE}
    except (smu.SmuError, OSError) as e:
        return {"error": str(e)}


def apply(core, margin, dry_run=False):
    """Set one core's margin (root, password prompt). The helper re-checks every guardrail against the hardware."""
    check_interrupted()
    p = plan(core, margin)
    if dry_run or not p["ok"]:
        if not p["ok"]:
            raise ValueError("; ".join(p["errors"]))
        return {"dry_run": True, **p}
    out = _helper(["--core", str(core), "--margin", str(margin)])
    out["telemetry_after"] = _core_now(core)
    journal.add(DATA, "applied", domain="cpu", settings={"core": core, "margin": margin}, ok=out["ok"],
                step=out.get("step"), errors=out.get("errors"), applied=out.get("applied"),
                telemetry_after=out["telemetry_after"])
    if not out["ok"]:
        raise ValueError("; ".join(out.get("errors") or ["helper refused"]))
    return out


def reset():
    """Put every core ctune changed this boot back to its boot (BIOS) value (root, password prompt)."""
    out = _helper(["--reset"])
    journal.add(DATA, "applied", domain="cpu", settings="reset to boot values", ok=out["ok"], reset=out.get("reset"),
                errors=out.get("errors"))
    if not out["ok"]:
        raise ValueError("; ".join(out.get("errors") or ["helper refused"]))
    return out


def check_interrupted():
    return stress.check_interrupted()


# ---------------------------------------------------------------- test
def parse_stress(rc, out):
    errs = [l.split("fail:", 1)[1].strip() for l in out.splitlines() if " fail:" in l or l.startswith("fail:")]
    if re.search(r"failed: [1-9]", out) and not errs:
        errs.append("stress-ng reported a failed stressor")
    if rc < 0:
        errs.append(f"stress-ng killed by signal {-rc}")
    elif rc != 0:
        errs.append(f"stress-ng exit status {rc}")
    m = re.search(r"metrc:\s*\[\d+\]\s+(\S+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", out)
    return {"ok": not errs, "rc": rc, "bogo_ops_s": float(m.group(6)) if m else None, "errors": errs[:10]}


def _run_stress(cpu, args, seconds, tmp):
    cmd = ["stress-ng", *args, "--taskset", str(cpu), "--verify", "--timeout", f"{seconds}s", "--metrics-brief",
           "--temp-path", tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 120, cwd=tmp)
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": None, "bogo_ops_s": None, "errors": [f"stress-ng hung past {seconds + 120} s"]}
    return parse_stress(r.returncode, r.stdout + r.stderr)


class _Sampler(threading.Thread):
    """1 Hz pm_table telemetry, tagged with the core under test and the phase. Stays off the core under test."""

    def __init__(self, all_cpus):
        super().__init__(daemon=True)
        self.rows, self.stop, self.all_cpus = [], threading.Event(), set(all_cpus)
        self.core, self.phase, self.avoid, self._pinned = None, None, set(), None

    def mark(self, core, phase, avoid):
        self.core, self.phase, self.avoid = core, phase, set(avoid)

    def run(self):
        while not self.stop.wait(1.0):
            if self._pinned != self.avoid:
                try:
                    os.sched_setaffinity(0, (self.all_cpus - self.avoid) or self.all_cpus)
                except OSError:
                    pass
                self._pinned = set(self.avoid)
            try:
                self.rows.append((self.core, self.phase, smu.read_pm()))
            except (smu.SmuError, OSError):
                pass

    def summary(self, core):
        out = {}
        for phase in dict.fromkeys(r[1] for r in self.rows if r[0] == core):
            rows = [r[2] for r in self.rows if r[0] == core and r[1] == phase]
            if not rows:
                continue
            g = lambda k: [r[k][core] for r in rows]  # noqa: E731
            out[phase] = {"samples": len(rows), "eff_ghz_avg": round(statistics.mean(g("core_eff_ghz")), 3),
                          "freq_ghz_max": round(max(g("core_freq_ghz")), 3),
                          "voltage_v_avg": round(statistics.mean(g("core_voltage_v")), 4),
                          "voltage_v_max": round(max(g("core_voltage_v")), 4),
                          "power_w_avg": round(statistics.mean(g("core_power_w")), 2),
                          "temp_c_max": round(max(g("core_temp_c")), 1),
                          "c0_pct_avg": round(statistics.mean(g("core_c0_pct")), 1)}
        return out


def test(cores=None, seconds_per_core=360, order="sequential", note="", record=True, workload="sse-huge",
         full_cycle=False):
    """One thread per core, the given cores in turn (default all). workload: an mprime mode (default sse-huge,
    CoreCycler's default) or "stress-ng". full_cycle (mprime): run each core until every FFT length has passed once,
    capped at 40 min, instead of seconds_per_core. Red = a load error, any new corrected machine-check error, or a
    crash (recorded on the next boot)."""
    if workload not in WORKLOADS:
        raise ValueError(f"workload is one of {WORKLOADS}")
    check_interrupted()
    topo, _ = smu.topology()
    cores = list(range(len(topo))) if cores is None else [int(c) for c in cores]
    for c in cores:
        if c not in topo:
            raise ValueError(f"core {c} does not exist (cores {list(topo)})")
    if order == "random":
        random.shuffle(cores)
    elif order != "sequential":
        raise ValueError("order is 'sequential' or 'random'")
    if seconds_per_core < (30 if workload == "stress-ng" else 60):
        raise ValueError("seconds_per_core must be at least 60 for mprime (one SSE Huge test takes about 30 s) and "
                         "30 for stress-ng")
    phase_s = max(5, int((seconds_per_core - IDLE_GAP_S * len(PHASES)) / len(PHASES)))
    if workload != "stress-ng":
        phase_s = None
    all_cpus = {cpu for cpus in topo.values() for cpu in cpus}
    cur = current()
    settings = {"margins": cur["cores"], "source": cur["source"]}
    res = {"test": "co-cycle", "workload": workload, "cores": cores, "seconds_per_core": seconds_per_core,
           "full_cycle": full_cycle, "phase_s": phase_s,
           "phases": [p for p, _ in PHASES] if workload == "stress-ng" else [workload], "settings": settings,
           "cpufreq": _cpufreq()}
    t0 = time.time()
    mce_all_before = errors.counts()
    own_mask = os.sched_getaffinity(0)
    sampler = _Sampler(all_cpus)
    sampler.start()
    per_core = []
    tmp = tempfile.mkdtemp(prefix="ctune-cpu-test-")
    try:
        with stress.run("cpu", "co-cycle", settings) as run:
            for i, core in enumerate(cores):
                cpu, avoid = topo[core][0], topo[core]
                os.sched_setaffinity(0, (all_cpus - set(avoid)) or all_cpus)
                before = errors.counts_by_cpu()
                phases = {}
                if workload == "stress-ng":
                    for name, args in PHASES:
                        run.update(phase=name, core=core)
                        sampler.mark(core, name, avoid)
                        phases[name] = r = _run_stress(cpu, args, phase_s, tmp)
                        sampler.mark(core, "idle", avoid)
                        time.sleep(IDLE_GAP_S)
                        if not r["ok"]:
                            break   # skip the rest of this core, like CoreCycler's skipCoreOnError
                else:
                    run.update(phase=workload, core=core)
                    wd = tempfile.mkdtemp(prefix=f"mprime-core{core}-", dir=tmp)
                    phases[workload] = mprime.run_core(cpu, workload, seconds_per_core, wd, full_cycle,
                                                       mark=lambda ph, c=core, a=avoid: sampler.mark(c, ph, a))
                    sampler.mark(core, "idle", avoid)
                delta = errors.delta_by_cpu(before, errors.counts_by_cpu())
                ok = all(p["ok"] for p in phases.values()) and not delta
                unsure = not ok and not delta and all(p["ok"] or p.get("inconclusive") for p in phases.values())
                per_core.append({"core": core, "cpu": cpu, "margin": cur["cores"][core],
                                 "verdict": "green" if ok else "inconclusive" if unsure else "red", "phases": phases,
                                 "mce_delta_by_cpu": delta,
                                 "telemetry": sampler.summary(core)})
                if i < len(cores) - 1:
                    sampler.mark(None, None, ())
                    time.sleep(BETWEEN_CORES_S)
    finally:
        sampler.stop.set()
        sampler.join()
        os.sched_setaffinity(0, own_mask)
        shutil.rmtree(tmp, ignore_errors=True)
    after = errors.counts()
    new_mce = {b: n - mce_all_before.get(b, 0) for b, n in after.items() if n > mce_all_before.get(b, 0)}
    res["per_core"] = per_core
    res["new_corrected_errors"] = new_mce
    res["kernel_hw_errors"] = errors.kernel_hw_errors(since=f"@{int(t0)}")
    res["duration_s"] = round(time.time() - t0)
    red = [c["core"] for c in per_core if c["verdict"] == "red"]
    res["red_cores"] = red
    res["verdict"] = "green" if not red and not new_mce and not res["kernel_hw_errors"] else "red"
    if res["verdict"] == "green" and any(c["verdict"] == "inconclusive" for c in per_core):
        res["verdict"] = "inconclusive"
    res["note"] = ("corrected errors are also polled every check_interval; call `errors` again 300 s after the test "
                   "before trusting a zero")
    if record:
        journal.add(DATA, "result", domain="cpu", text=note or f"cpu test cores {cores}", **res)
    return res
