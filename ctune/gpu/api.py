"""GPU (NVIDIA) tuning API: status, apply/reset (root helper via pkexec), tests with telemetry, Xid errors."""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

from ctune.core import journal, stress
from ctune.core.paths import DATA, TOOLS
from ctune.gpu import burn, nvml, q2rtx

STATE = os.path.join(DATA, "gpu_state.json")
HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apply.py")
RUNTIME = "/usr/local/libexec/ctune/gpu-runtime-apply"   # `ctune gpu passwordless`: no password prompt
MEMTEST = os.path.join(TOOLS, "memtest_vulkan", "memtest_vulkan")
VKPEAK = os.path.join(TOOLS, "vkpeak", "vkpeak-20260527-linux", "vkpeak")


def _boot_id():
    with open("/proc/sys/kernel/random/boot_id") as f:
        return f.read().strip()


def applied():
    """What ctune applied this boot (NVML has no getter for locked clocks): the boot unit's persisted settings,
    overlaid by runtime changes from gpu_apply or gpu_reset."""
    from ctune.gpu import persist
    b = persist.boot_result()
    out = dict(b["applied"], source="boot unit") if b and b.get("ok") and b.get("applied") else {}
    try:
        s = json.load(open(STATE))
        # newest wins: a resume re-applies the persisted values over earlier runtime changes
        if s.get("boot_id") == _boot_id() and (not out or os.path.getmtime(STATE) > b.get("epoch", 0)):
            out.update(s, source="gpu_apply" if not out else "boot unit, then gpu_apply")
    except (OSError, ValueError):
        pass
    return out


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
        "persistence": _persist_status(),
        "xid_errors_this_boot": len(xid_errors()),
    }


def _persist_status():
    from ctune.gpu import persist
    try:
        return persist.status()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _merge_state(plan, reset):
    """Record what is applied since boot (NVML cannot read back a clock lock). The passwordless helper writes no
    file, so the caller keeps this state."""
    with open("/proc/sys/kernel/random/boot_id") as f:
        boot = f.read().strip()
    try:
        with open(STATE) as f:
            state = json.load(f)
        if state.get("boot_id") != boot:
            state = {}
    except (OSError, ValueError):
        state = {}
    state = dict(plan) if reset else {**state, **{k: v for k, v in plan.items() if v is not None}}
    state.update(boot_id=boot, ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1)
    return state


def _helper(args):
    if os.geteuid() != 0 and os.path.isfile(RUNTIME):
        return _runtime(args)
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


def _runtime(args):
    """The root-owned passwordless helper. It refuses values outside ctune's caps and goes back to stock on a
    failed apply; either way ctune's state follows what it reports."""
    try:
        r = subprocess.run(["pkexec", RUNTIME, *args], capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise RuntimeError("the root password prompt was not answered within 10 minutes; nothing was changed "
                           "(is the polkit rule installed? `ctune gpu passwordless`)")
    if r.returncode in (126, 127):
        raise RuntimeError("pkexec refused or the prompt was dismissed; nothing was changed")
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError(f"gpu-runtime-apply failed ({r.returncode}): {r.stderr.strip()[-600:]}")
    if out.get("rolled_back"):
        _merge_state({"core_offset": 0, "mem_offset": 0, "max_clock": None,
                      "power_limit": (out.get("readback") or {}).get("power_limit")}, reset=True)
    if not out.get("ok"):
        raise ValueError("; ".join(out.get("errors", [])))
    out["applied"] = _merge_state(out["applied"], reset=out.get("reset", False))
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
SETTLED_S = 60   # temp_c_end and fan_pct_end average a part's last 60 samples (1 Hz)


class _Sampler(threading.Thread):
    """1 Hz NVML telemetry during a test, tagged with the running part (fp32, fp64, tc, transient, graphics, vram,
    vkpeak; transient:paused while a transient run is frozen)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.h, self.rows, self.stop, self.part = nvml.handle(0), [], threading.Event(), None

    def run(self):
        while not self.stop.wait(1.0):
            try:
                self.rows.append((nvml.clock(self.h), nvml.power_w(self.h), nvml.temperature(self.h),
                                  tuple(nvml.event_reasons(self.h)), self.part, nvml.fan_percent(self.h)))
            except nvml.NvmlError:
                pass

    def summary(self, part=None, max_clock=None):
        rows = [r for r in self.rows if part is None or r[4] == part]
        busy = [r for r in rows if "idle" not in r[3]] or rows
        if not busy:
            return {}
        clocks, powers, temps = [r[0] for r in busy], [r[1] for r in busy], [r[2] for r in busy]
        limiters = {}
        for r in busy:
            for x in r[3]:
                limiters[x] = limiters.get(x, 0) + 1
        out = {"samples": len(busy), "core_mhz_avg": round(sum(clocks) / len(clocks)), "core_mhz_min": min(clocks),
               "core_mhz_max": max(clocks), "power_w_avg": round(sum(powers) / len(powers), 1),
               "power_w_max": round(max(powers), 1), "temp_c_max": max(temps),
               "limiter_share": {k: round(v / len(busy), 2) for k, v in limiters.items()}}
        # heat and noise, the undervolt's goal: *_end averages the last SETTLED_S, once the cooler has caught up
        fans = [r[5] for r in busy if r[5] is not None]
        for key, vals in (("temp_c", temps), ("fan_pct", fans)):
            if vals:
                out[f"{key}_avg"] = round(sum(vals) / len(vals), 1)
                out[f"{key}_max"] = max(vals)
                out[f"{key}_end"] = round(sum(vals[-SETTLED_S:]) / len(vals[-SETTLED_S:]), 1)
        if max_clock:   # does this load reach the locked top of the V/F curve, or does the power cap hold it below?
            out["at_lock_share"] = round(sum(c >= max_clock - 15 for c in clocks) / len(clocks), 2)
        return out


def _part_telemetry(sampler, part, max_clock=None):
    """One part's telemetry. A transient run's frozen seconds are kept apart: their clock is where the GPU boosted
    to while idle, so each thaw stepped the load back on at that clock. Their power is left out: NVML averages it
    over 1 s, so it mostly shows the load before the pause (5 Hz instant power showed ~120 W in each pause)."""
    out = sampler.summary(part, max_clock)
    paused = sampler.summary(f"{part}:paused", max_clock)
    if paused:
        out["paused"] = {k: paused[k] for k in ("samples", "core_mhz_avg", "core_mhz_max") if k in paused}
    return out


def parse_vram(rc, out, stopped=True):
    """memtest_vulkan output: passes, VRAM errors, checked bandwidth (the first two passes are warm-up)."""
    lines = out.splitlines()
    passes = [l for l in lines if "iteration" in l and "Passed" in l]
    errs = [l for l in lines if re.search(r"error|mismatch|fail", l, re.I) and "Passed" not in l]
    unavailable = not passes and not errs
    if alloc_failed(rc, out):
        errs.append(f"memtest_vulkan exited {rc} with no output: it could not allocate its buffer (free VRAM fell "
                    "after ctune read it, or another app took it)")
    elif unavailable:
        tail = " | ".join(l.strip() for l in lines[-6:] if l.strip()) or "no output"
        errs.append(f"memtest_vulkan exited {rc} without a pass (VRAM held by another app?); its output: {tail}")
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


VRAM_ALLOC_RC = 68          # memtest_vulkan's exit when it cannot allocate the buffer it is given (no output)
VRAM_STEP_MB = 1024
ALLOC_PROBE_S = 5           # a failed allocation exits at once; a run still alive by then has its buffer


def alloc_failed(rc, out):
    return rc == VRAM_ALLOC_RC and not out.strip()


class VramLoad(stress.Load):
    """memtest_vulkan as a load, for the VRAM test and the mem tests' GPU heat. It exits 68 with no output when it
    cannot allocate its buffer: free VRAM fell between ctune reading it and the start, or it is fragmented (after a
    long uptime with desktop apps the largest block can be several GB below the free total). start() then retries
    1 GiB smaller down to VRAM_MIN_MB; a failed try ends within seconds. The result records the budget that ran and
    the failed tries."""

    def __init__(self, name, cap_s, budget_bytes=None, optional=False):
        # it writes memtest_vulkan.log into its working directory
        super().__init__(name, None, parse_vram, cwd=os.path.dirname(MEMTEST), optional=optional)
        self.cap_s, self.budget, self.attempts = cap_s, budget_bytes or vram_budget_bytes(), []

    def start(self):
        for i in range(64):
            budget = self.budget - (i * VRAM_STEP_MB << 20)
            if i and budget < VRAM_MIN_MB << 20:
                break
            self.used = budget
            self.cmd = ["timeout", "-s", "INT", str(self.cap_s)] + vram_cmd(budget)   # ends by itself if ctune dies
            super().start()
            try:
                rc = self.proc.wait(ALLOC_PROBE_S)
            except subprocess.TimeoutExpired:
                return self   # running: the buffer was allocated
            self._reader.join(10)
            if not alloc_failed(rc, "".join(self._chunks)):
                return self   # it ended for another reason: result() says which
            self.attempts.append({"budget_mb": budget >> 20, "rc": rc})
        return self

    def result(self):
        return {**super().result(), "budget_mb": self.used >> 20, "alloc_retries": self.attempts}


def _vram(seconds, budget_bytes=None):
    total = nvml.memory_mb(nvml.handle(0))[0]
    ld = VramLoad("vram (memtest_vulkan)", seconds, budget_bytes)
    try:
        ld.start()
        ld.proc.wait()
    finally:   # a stop mid-run: SIGINT, then SIGKILL
        ld.stop()
    r = ld.result()
    return {"seconds": seconds, "coverage_pct": round(100 * r["budget_mb"] / total, 1), **r}


def _vkpeak(runs):
    """vkpeak FP32 throughput: a score only, it never checks its results."""
    scores = []
    for _ in range(runs):
        r = subprocess.run([VKPEAK, "0"], capture_output=True, text=True, timeout=600)
        m = re.search(r"fp32-scalar\s*=\s*([\d.]+)", r.stdout)
        scores.append(float(m.group(1)) if m and r.returncode == 0 else None)
    good = [s for s in scores if s]
    return {"runs": runs, "fp32_gflops": scores,
            "fp32_gflops_avg": round(sum(good) / len(good), 1) if good else None,
            "ok": len(good) == runs}


TEMP_LIMIT_C = 83   # guardrail (SKILL.md): a gpu-burn run stops at this core temperature


def _temp_guard(h):
    try:
        t = nvml.temperature(h)
    except nvml.NvmlError:
        return None
    return f"GPU reached {t} °C, the {TEMP_LIMIT_C} °C guardrail" if t >= TEMP_LIMIT_C else None


def _graphics(seconds, h):
    """Quake II RTX timedemo passes (RT cores, texture units, a compute denoiser): red on a crash, a lost device or a
    hang, inconclusive when it cannot run here."""
    why = q2rtx.unavailable()
    if why:
        return {"ok": False, "unavailable": True, "errors": [why]}
    return q2rtx.run(seconds, os.path.join(DATA, "q2rtx"), guard=lambda: _temp_guard(h))


def _parts(res):
    """Every part that ran: the gpu-burn modes, then graphics, vram and vkpeak."""
    return [v for k, v in res.get("core", {}).items() if k != "ok"] + \
        [res[k] for k in ("graphics", "vram", "vkpeak") if k in res]


def _verdict(res, new_xid):
    """(green, red or inconclusive; what blocked it). A part that could not run makes the test inconclusive (fix it
    and run again), unless another part failed or an Xid error came: then it is red."""
    parts = _parts(res)
    failed = [p for p in parts if not p["ok"] and not p.get("unavailable")]
    blocked = [p["errors"][0] for p in parts if not p["ok"] and p.get("unavailable")]
    if not parts or failed or new_xid:
        return "red", blocked
    return ("inconclusive" if blocked else "green"), blocked


def _burn(mode, seconds, h, mark=None):
    """One gpu-burn run (fp32, fp64, tc, or transient: fp32 frozen 1 s in every 10) with the temperature guard.
    mark(state) tags the telemetry while a transient run is frozen."""
    _, free, _ = nvml.memory_mb(h)
    mb = min(free - 1024, burn.TRANSIENT_MB if mode == "transient" else 8192)
    if mb < burn.MIN_MB[mode]:   # the test cannot run: inconclusive, not red
        return {"ok": False, "unavailable": True, "mode": mode,
                "errors": [f"only {free} MiB of VRAM free; gpu-burn {mode} needs {burn.MIN_MB[mode] + 1024}"]}
    # NVML's free VRAM is not always one allocatable block, so gpu-burn's start-up allocation can fail with VRAM to
    # spare. A start-up out-of-memory retries 512 MB smaller, down to the mode's minimum; the score does not depend on
    # the buffer size (it sets how many result matrices gpu-burn keeps, not the work per matrix).
    retries = []
    while True:
        cidfile = os.path.join(tempfile.mkdtemp(prefix="ctune-gpu-burn-"), "cid")
        ld = stress.Load(f"gpu-burn {mode}", burn.cmd(mode, seconds, mb, cidfile), burn.parse, self_timed=True)
        pauser = burn.Pauser(ld, seconds, cidfile, mark=mark) if mode == "transient" else None
        try:
            if pauser:
                pauser.start()
            r = stress.run_loads([ld], seconds + 15, on_tick=lambda: _temp_guard(h))   # + gpu-burn's -stts and start-up
        finally:
            if pauser:
                pauser.stop.set()
                pauser.join(100)   # longer than its podman calls can take (3 x 30 s), so it never tags the next part
            burn.cleanup(cidfile)
        out = {**r["loads"][0], "mode": mode, "seconds": seconds, "mb": mb}
        oom = any("out of memory" in e for e in out.get("errors", [])) and not out.get("gflops_median")
        if not oom or mb - 512 < burn.MIN_MB[mode]:
            break
        retries.append({"mb": mb, "error": next(e for e in out["errors"] if "out of memory" in e)})
        mb -= 512
    if retries:
        out["alloc_retries"] = retries
    if r["early_exit"] and r["early_exit"] != ld.name:   # a guardrail stopped it: red, whatever the pauser did
        out["ok"] = False
        out["errors"].append(r["early_exit"])
    elif pauser and (pauser.stuck or not pauser.pauses):   # a tool failure, or plain fp32 that tested no transient
        out["ok"], out["unavailable"] = False, True
        out["errors"].append(("the container stayed frozen: " if pauser.stuck else "the container was never paused: ")
                             + "; ".join(pauser.failures[:2] or ["no cycle"]))
    if pauser:
        out["transient"] = pauser.summary()
    return out


STRETCH_PCT = 1.0   # a fall in GFLOPS per MHz larger than this, against the last green result at the lock, flags it


def _lock(settings):
    return settings.get("max_clock") if isinstance(settings, dict) else None


def _per_mhz(part):
    g, mhz = part.get("gflops_median"), (part.get("telemetry") or {}).get("core_mhz_avg")
    return g / mhz if g and mhz else None


def clock_stretch(res, records):
    """Clock stretching: short of voltage, Ampere keeps the requested clock while its effective clock falls, so NVML
    shows the same MHz and less work gets done per MHz. Compares GFLOPS per MHz of each steady gpu-burn part (fp32,
    fp64, tc) with the last green gpu result at the same max_clock; `stretched` lists the parts that fell more than
    STRETCH_PCT. It does not change the verdict: the step is judged on score (SKILL.md, step 5)."""
    lock = _lock(res.get("settings"))
    # a stretched result stays green (the verdict is on errors), so it is skipped: the baseline never drifts down
    base = next((r for r in reversed(records) if r.get("domain") == "gpu" and r.get("kind") == "result"
                 and r.get("verdict") == "green" and r.get("core") and _lock(r.get("settings")) == lock
                 and not (r.get("clock_stretch") or {}).get("stretched")), None)
    parts = {}
    for m, part in (res.get("core") or {}).items():
        now, then = (_per_mhz(part), _per_mhz(base["core"].get(m) or {})) \
            if base and m in ("fp32", "fp64", "tc") else (None, None)
        if now and then:
            parts[m] = {"gflops_per_mhz": round(now, 3), "baseline": round(then, 3),
                        "change_pct": round(100 * (now / then - 1), 1)}
    return {"baseline_ts": base["ts"] if base else None, "max_clock": lock, "parts": parts,
            "stretched": [m for m, v in parts.items() if v["change_pct"] < -STRETCH_PCT]}


KINDS = ("core", "vram", "screen", "graphics", "vkpeak")


def test(kind="screen", burn_seconds=600, fp64_seconds=600, tc_seconds=120, transient_seconds=180,
         graphics_seconds=300, vram_seconds=300, core_runs=3, budget_bytes=None, note="", record=True):
    """kind: 'core' (gpu-burn, every result checked: FP32 SGEMM, then FP64, tensor cores, and FP32 frozen 1 s in
    every 10 for load transients, each run if its seconds > 0), 'graphics' (Quake II RTX: RT cores and the lock at
    high power; nothing checked but crashes, lost devices and hangs), 'vram' (memtest_vulkan), 'screen' (core, then
    graphics, then vram), or 'vkpeak' (throughput score only, nothing checked).
    Green = every part ok, no new Xid errors, the temperature guardrail not hit, no crash."""
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}")
    durations = {"fp32": burn_seconds, "fp64": fp64_seconds, "tc": tc_seconds, "transient": transient_seconds}
    if kind == "graphics":
        durations = dict.fromkeys(durations, 0)
    if any(v < 0 for v in durations.values()) or vram_seconds < 0 or graphics_seconds < 0:
        raise ValueError("durations cannot be negative")
    if kind == "graphics" and not graphics_seconds:
        raise ValueError("kind graphics needs graphics_seconds above 0")
    if kind == "vram" and not vram_seconds:
        raise ValueError("kind vram needs vram_seconds above 0")
    if kind == "vkpeak" and core_runs < 1:
        raise ValueError("kind vkpeak needs core_runs of 1 or more")
    if 0 < graphics_seconds < q2rtx.MIN_SECONDS:
        raise ValueError(f"graphics_seconds {graphics_seconds} < {q2rtx.MIN_SECONDS}: not one full pass")
    if kind in ("core", "screen"):
        if not any(durations.values()):
            raise ValueError("core and screen need at least one gpu-burn duration above 0")
        short = [f"{m} {v} s < {burn.MIN_SECONDS[m]} s" for m, v in durations.items() if 0 < v < burn.MIN_SECONDS[m]]
        if short:
            raise ValueError(f"gpu-burn runs too short to compare anything: {', '.join(short)}")
    stress.check_interrupted()
    if kind in ("core", "screen") and not burn.image_present():
        raise RuntimeError(f"the gpu-burn image {burn.IMAGE} is missing: run `ctune gpu burn-setup` once")
    taken = burn.busy() if kind in ("core", "screen") else None
    if taken:
        raise RuntimeError(taken)
    if kind != "vkpeak":
        short = vram_free_check()
        if short:
            raise RuntimeError(short + ": close them, then run the test")
    h = nvml.handle(0)
    xid_before = len(xid_errors())
    e0, t0 = nvml.energy_j(h), time.time()
    sampler = _Sampler()
    sampler.start()
    settings = applied() or "defaults"
    max_clock = _lock(settings)
    res = {"test": kind, "settings": settings, "gpu_burn": burn.COMMIT[:7] if kind in ("core", "screen") else None}
    try:
        with stress.run("gpu", f"gpu-{kind}", settings) as run:
            if kind in ("core", "screen"):
                res["core"] = {}
                for mode, secs in durations.items():
                    if secs:
                        run.update(phase=mode)
                        sampler.part = mode
                        res["core"][mode] = _burn(mode, secs, h, lambda state, m=mode: setattr(
                            sampler, "part", m if state == "load" else f"{m}:paused"))
                        res["core"][mode]["telemetry"] = _part_telemetry(sampler, mode, max_clock)
                        if not res["core"][mode]["ok"]:
                            break
                ran = [v for k, v in res["core"].items() if k != "ok"]
                res["core"]["ok"] = bool(ran) and all(v["ok"] for v in ran)
            if graphics_seconds and kind in ("screen", "graphics") and res.get("core", {}).get("ok", True):
                run.update(phase="graphics")
                sampler.part = "graphics"
                res["graphics"] = _graphics(graphics_seconds, h)
                res["graphics"]["telemetry"] = _part_telemetry(sampler, "graphics", max_clock)
            # a red part ends the test; a part that could not run (no game) does not skip the next one
            graphics = res.get("graphics", {"ok": True})
            if kind in ("vram", "screen") and vram_seconds and res.get("core", {}).get("ok", True) and \
                    (graphics["ok"] or graphics.get("unavailable")):
                run.update(phase="vram")
                sampler.part = "vram"
                res["vram"] = _vram(vram_seconds, budget_bytes)
            if kind == "vkpeak":
                run.update(phase="vkpeak")
                sampler.part = "vkpeak"
                res["vkpeak"] = _vkpeak(core_runs)
    finally:
        sampler.stop.set()
        sampler.join()
    res["telemetry"] = sampler.summary(max_clock=max_clock)
    res["energy_j"] = round(nvml.energy_j(h) - e0)
    res["duration_s"] = round(time.time() - t0)
    new_xid = xid_errors()[xid_before:]
    res["new_xid_errors"] = new_xid
    res["verdict"], blocked = _verdict(res, new_xid)
    if res["verdict"] == "inconclusive":
        res["blocked_by"] = blocked
    if res.get("core"):
        res["clock_stretch"] = clock_stretch(res, journal.tail(DATA, 1000, "result"))
    fp32 = res.get("core", {}).get("fp32", {})
    if fp32.get("gflops_median") and fp32.get("telemetry", {}).get("power_w_avg"):
        res["score_gflops"] = fp32["gflops_median"]
        res["gflops_per_w"] = round(fp32["gflops_median"] / fp32["telemetry"]["power_w_avg"], 1)
    if record:
        journal.add(DATA, "result", domain="gpu", text=note or f"gpu {kind} test", **res)
    return res
