"""GPU (NVIDIA) tuning API: status, apply/reset (root helper via pkexec), tests with telemetry, Xid errors."""
import json
import os
import re
import subprocess
import sys
import threading
import time

from ctune.core import journal, stress
from ctune.core.paths import DATA, TOOLS
from ctune.gpu import nvml

STATE = os.path.join(DATA, "gpu_state.json")
HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apply.py")
MEMTEST = os.path.join(TOOLS, "memtest_vulkan", "memtest_vulkan")
VKPEAK = os.path.join(TOOLS, "vkpeak", "vkpeak-20260527-linux", "vkpeak")


def _boot_id():
    return open("/proc/sys/kernel/random/boot_id").read().strip()


def applied():
    """What ctune applied since this boot (NVML has no getter for locked clocks)."""
    try:
        s = json.load(open(STATE))
        return s if s.get("boot_id") == _boot_id() else {}
    except (OSError, ValueError):
        return {}


def xid_errors(since="boot"):
    args = ["journalctl", "-k", "--no-pager", "-o", "short-iso", "-g", "NVRM: Xid"]
    args += ["-b"] if since == "boot" else ["--since", since]
    r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    return [l for l in r.stdout.splitlines() if "Xid" in l]


def status():
    h = nvml.handle(0)
    return {
        "name": nvml.name(h), "pstate": f"P{nvml.pstate(h)}",
        "clocks_mhz": {"core": nvml.clock(h, nvml.GRAPHICS), "mem": nvml.clock(h, nvml.MEM),
                       "core_max": nvml.max_clock(h, nvml.GRAPHICS), "mem_max": nvml.max_clock(h, nvml.MEM)},
        "offsets_mhz": {"core": nvml.core_offset(h), "core_range": nvml.core_offset_range(h),
                        "mem": nvml.mem_offset(h), "mem_range": nvml.mem_offset_range(h)},
        "power_w": {"draw": nvml.power_w(h), "limit": nvml.power_limit_w(h),
                    "default": nvml.default_power_limit_w(h), "range": nvml.power_constraints_w(h)},
        "temp_c": nvml.temperature(h), "fan_pct": nvml.fan_percent(h),
        "vram_mb": dict(zip(("total", "free", "used"), nvml.memory_mb(h))),
        "clock_limiters": nvml.event_reasons(h),
        "applied_by_ctune": applied(),
        "xid_errors_this_boot": len(xid_errors()),
    }


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
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError(f"helper failed ({r.returncode}): {r.stderr.strip()[-600:]}")
    if not out.get("ok"):
        raise ValueError("; ".join(out.get("errors", [])))
    return out


def apply(core_offset=None, mem_offset=None, max_clock=None, power_limit=None):
    args = []
    for flag, v in (("--core-offset", core_offset), ("--mem-offset", mem_offset),
                    ("--max-clock", max_clock), ("--power-limit", power_limit)):
        if v is not None:
            args += [flag, str(v)]
    if not args:
        raise ValueError("nothing to apply")
    out = _helper(args)
    journal.add(DATA, "applied", domain="gpu", settings=out["applied"], readback=out["readback"])
    return out


def reset():
    out = _helper(["--reset"])
    journal.add(DATA, "applied", domain="gpu", settings="reset to defaults", readback=out["readback"])
    return out


# ---------------------------------------------------------------- tests
class _Sampler(threading.Thread):
    """1 Hz NVML telemetry during a test."""

    def __init__(self):
        super().__init__(daemon=True)
        self.h, self.rows, self.stop = nvml.handle(0), [], threading.Event()

    def run(self):
        while not self.stop.wait(1.0):
            try:
                self.rows.append((nvml.clock(self.h), nvml.power_w(self.h), nvml.temperature(self.h),
                                  tuple(nvml.event_reasons(self.h))))
            except nvml.NvmlError:
                pass

    def summary(self):
        busy = [r for r in self.rows if "idle" not in r[3]] or self.rows
        if not busy:
            return {}
        clocks, powers, temps = [r[0] for r in busy], [r[1] for r in busy], [r[2] for r in busy]
        limiters = {}
        for r in busy:
            for x in r[3]:
                limiters[x] = limiters.get(x, 0) + 1
        return {"samples": len(busy), "core_mhz_avg": round(sum(clocks) / len(clocks)), "core_mhz_min": min(clocks),
                "core_mhz_max": max(clocks), "power_w_avg": round(sum(powers) / len(powers), 1),
                "power_w_max": round(max(powers), 1), "temp_c_max": max(temps),
                "limiter_share": {k: round(v / len(busy), 2) for k, v in limiters.items()}}


def parse_vram(rc, out, stopped=True):
    """memtest_vulkan output: passes, VRAM errors, checked bandwidth (the first two passes are warm-up)."""
    lines = out.splitlines()
    passes = [l for l in lines if "iteration" in l and "Passed" in l]
    errs = [l for l in lines if re.search(r"error|mismatch|fail", l, re.I) and "Passed" not in l]
    unavailable = not passes and not errs
    if unavailable:
        errs.append(f"memtest_vulkan exited {rc} without a pass: it could not allocate its VRAM buffer "
                    "(another app holding VRAM?)")
    bw = [float(m.group(1)) for l in passes[2:] if (m := re.search(r"checked:.*?([\d.]+)GB/sec", l))]
    return {"passes_reported": len(passes), "errors": errs[:20], "unavailable": unavailable,
            "checked_gbps_avg": round(sum(bw) / len(bw), 1) if bw else None, "ok": bool(passes) and not errs}


VRAM_MIN_MB = 2048   # below this, memtest_vulkan cannot allocate a useful buffer


def vram_holders(min_mb=128):
    """Compute processes holding VRAM (an LLM server, a game), largest first."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[-1].isdigit() and int(parts[-1]) >= min_mb:
            out.append({"pid": int(parts[0]), "name": os.path.basename(",".join(parts[1:-1]).split()[0]),
                        "mb": int(parts[-1])})
    return sorted(out, key=lambda x: -x["mb"])


def vram_free_check():
    """None if enough VRAM is free for a VRAM test, else the reason (with the processes holding it)."""
    _, free, _ = nvml.memory_mb(nvml.handle(0))
    if free >= VRAM_MIN_MB:
        return None
    who = ", ".join(f"{h['name']} (pid {h['pid']}, {h['mb']} MiB)" for h in vram_holders()) or "unknown processes"
    return f"only {free} MiB of VRAM free (held by {who}); memtest_vulkan needs {VRAM_MIN_MB} MiB"


def vram_budget_bytes(reserve_mb=768, cap_mb=None):
    """VRAM memtest_vulkan can take: free VRAM minus a reserve for the desktop, at least 512 MiB."""
    try:
        _, free, _ = nvml.memory_mb(nvml.handle(0))
    except nvml.NvmlError:
        free = 6000
    mb = max(512, free - reserve_mb)
    return (min(mb, cap_mb) if cap_mb else mb) << 20


def vram_cmd(budget_bytes=None):
    return [MEMTEST, "1", str(budget_bytes or vram_budget_bytes())]


def _vram(seconds, budget_bytes=None):
    budget = budget_bytes or vram_budget_bytes()
    total = nvml.memory_mb(nvml.handle(0))[0]
    r = subprocess.run(["timeout", "-s", "INT", str(seconds)] + vram_cmd(budget), capture_output=True, text=True,
                       cwd=os.path.dirname(MEMTEST))   # it writes memtest_vulkan.log into the working directory
    return {"seconds": seconds, "budget_mb": budget >> 20, "coverage_pct": round(100 * (budget >> 20) / total, 1),
            **parse_vram(r.returncode, r.stdout)}


def _core(runs):
    scores = []
    for _ in range(runs):
        r = subprocess.run([VKPEAK, "0"], capture_output=True, text=True, timeout=600)
        m = re.search(r"fp32-scalar\s*=\s*([\d.]+)", r.stdout)
        scores.append(float(m.group(1)) if m and r.returncode == 0 else None)
    good = [s for s in scores if s]
    return {"runs": runs, "fp32_gflops": scores,
            "fp32_gflops_avg": round(sum(good) / len(good), 1) if good else None,
            "ok": len(good) == runs}


def test(kind="screen", vram_seconds=300, core_runs=3, budget_bytes=None, note="", record=True):
    """kind: 'core' (vkpeak, score + load), 'vram' (memtest_vulkan), or 'screen' (both).
    Green = every part ok, no new Xid errors, no crash."""
    stress.check_interrupted()
    if kind in ("vram", "screen"):
        short = vram_free_check()
        if short:
            raise RuntimeError(short + ": close them, then run the test")
    h = nvml.handle(0)
    xid_before = len(xid_errors())
    e0, t0 = nvml.energy_j(h), time.time()
    sampler = _Sampler()
    sampler.start()
    res = {"test": kind, "settings": applied() or "defaults"}
    try:
        with stress.run("gpu", f"gpu-{kind}", res["settings"]) as run:
            if kind in ("core", "screen"):
                run.update(phase="core")
                res["core"] = _core(core_runs)
            if kind in ("vram", "screen"):
                run.update(phase="vram")
                res["vram"] = _vram(vram_seconds, budget_bytes)
    finally:
        sampler.stop.set()
        sampler.join()
    res["telemetry"] = sampler.summary()
    res["energy_j"] = round(nvml.energy_j(h) - e0)
    res["duration_s"] = round(time.time() - t0)
    new_xid = xid_errors()[xid_before:]
    res["new_xid_errors"] = new_xid
    parts = [res[k]["ok"] for k in ("core", "vram") if k in res]
    res["verdict"] = "green" if all(parts) and not new_xid else "red"
    if res.get("core", {}).get("fp32_gflops_avg") and res["telemetry"].get("power_w_avg"):
        res["gflops_per_w"] = round(res["core"]["fp32_gflops_avg"] / res["telemetry"]["power_w_avg"], 1)
    if record:
        journal.add(DATA, "result", domain="gpu", text=note or f"gpu {kind} test", **res)
    return res
