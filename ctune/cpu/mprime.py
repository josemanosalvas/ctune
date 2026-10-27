"""Prime95 for Linux (mprime) as the per-core Curve Optimizer load, set up the way CoreCycler runs it.

One mprime process per core: one worker, one thread, pinned with taskset to the first logical CPU of the core, and
suspended for 1 s every 10 s so the core keeps dropping to idle and boosting back. The modes are CoreCycler's presets
(sp00n/corecycler a95b523, script-corecycler.ps1: FFT ranges L7316-7361, TortureWeak L6135-6194, prime.txt keys
L7530-7639; the default is SSE with Huge FFTs in configs/default.config.ini L247, L265, which makes the least heat
and so the highest boost). CoreCycler is CC BY-NC-SA, so this file copies nothing from it and writes the same
settings from the mprime source.

From the mprime 30.19 source (p95v3019b21.source.zip; research notes in docs/research/reuse-tools.md):
- EnableSetAffinity=0 is required. Otherwise mprime binds its torture worker to both threads of hwloc core 0 and
  ignores the taskset mask (commonb.c L414-423, L601-633). With it, a live check found every mprime thread on the
  chosen CPU.
- -W<dir> must be absolute and joined to the flag, because mprime first changes to its own directory (prime.c
  L191-200).
- TortureTime=1 runs one test per FFT length, so each length passes once per cycle (commonb.c L8066-8133).
- mprime exits 0 even when the torture test fails, so the verdict comes from results.txt and stdout only.
"""
import os
import re
import signal
import subprocess
import time

from ctune.core.paths import TOOLS

MPRIME_DIR = os.path.join(TOOLS, "mprime")
MPRIME = os.path.join(MPRIME_DIR, "mprime")
VERSION = "30.19b20"

# isa, FFT range in K
MODES = {
    "sse-huge": ("sse", 8960, 32768),        # CoreCycler's default
    "sse-heavyshort": ("sse", 4, 160),       # in-cache, from CoreCycler's Ryzen 5000 Curve Optimizer presets
    "sse-all": ("sse", 4, 32768),            # every SSE length, CoreCycler's long final test
    "avx2-1344k": ("avx2", 1344, 1344),      # heavy load, CoreCycler's Prime95.1344K.AVX2 config
    "avx2-moderate": ("avx2", 1344, 4096),
}
ISA_KEYS = {
    "sse": {"CpuSupportsAVX": 0, "CpuSupportsAVX2": 0, "CpuSupportsFMA3": 0, "CpuSupportsAVX512F": 0,
            "TortureWeak": 1097728},    # CPU_AVX, CPU_FMA3 and CPU_AVX512F masked off (gwnum/cpuid.h L49-61)
    "avx2": {"CpuSupportsAVX": 1, "CpuSupportsAVX2": 1, "CpuSupportsFMA3": 1, "CpuSupportsAVX512F": 0,
             "TortureWeak": 1048576},
}
RUN_S, SUSPEND_S = 9, 1      # CoreCycler suspendPeriodically: about 9 s running, then 1 s suspended
FULL_CYCLE_CAP_S = 40 * 60
NO_PROGRESS_S = 180          # no passed self-test after this long is red


def prime_txt(mode):
    isa, lo, hi = MODES[mode]
    keys = {"StressTester": 1, "UsePrimenet": 0, "V30OptionsConverted": 1, "NumWorkers": 1, "NumCores": 1,
            "TortureCores": 1, "CoresPerTest": 1, "TortureHyperthreading": 0, "EnableSetAffinity": 0,
            "EnableSetPriority": 0, "CpuSupportsSSE": 1, "CpuSupportsSSE2": 1, **ISA_KEYS[isa],
            "MinTortureFFT": lo, "MaxTortureFFT": hi, "TortureMem": 0, "TortureTime": 1,
            "ResultsFileTimestampInterval": 60, "ErrorCheck": 1}
    return "".join(f"{k}={v}\n" for k, v in keys.items())


_PREFIX = re.compile(r"^\[[^\]]*\] ")
PASS = re.compile(r"^Self-test (\d+)(K?)(?: \(thread \d+ of \d+\))? passed!")
FAIL = [re.compile(p) for p in (r"FATAL ERROR", r"Hardware failure detected running (\d+)(K?) FFT size",
                                r"ERROR: ILLEGAL SUMOUT", r"Possible hardware failure",
                                r"Maximum number of warnings exceeded", r"TORTURE TEST FAILED on worker #\d+",
                                r"Error setting affinity")]
SUMMARY = re.compile(r"Torture Test completed (\d+) tests in (?:(\d+) hours?, )?(\d+) minutes - (\d+) errors, "
                     r"(\d+) warnings\.")
TEST = re.compile(r"Test \d+, \d+ Lucas-Lehmer (?:in-place )?iterations of M\d+ using (.*?)FFT length (\d+)([KM]?)")


def _lines(text):
    return [_PREFIX.sub("", l).strip() for l in text.splitlines()]


def passed_sizes(results):
    return [int(m.group(1)) for l in _lines(results) if (m := PASS.match(l))]


def parse(results, stdout, mode, stopped, rc):
    """Verdict for one core. results: results.txt, stdout: mprime's screen output."""
    isa = MODES[mode][0]
    res_lines, out_lines = _lines(results), _lines(stdout)
    errors = [l for l in res_lines + out_lines if any(p.search(l) for p in FAIL)]
    # CoreCycler's catch-all: any 'error' line in results.txt (stdout's summary line says 'errors')
    errors += [l for l in res_lines if re.search(r"error", l, re.I) and l not in errors]
    failed = next((int(m.group(1)) for l in errors if (m := FAIL[1].search(l))), None)
    sizes = passed_sizes(results)
    summary = next((m for l in out_lines if (m := SUMMARY.search(l))), None)
    descs = [m.group(1) for l in out_lines if (m := TEST.search(l))]
    isa_ok = bool(descs) and all(("AVX" not in d and "FMA3" not in d) if isa == "sse" else "FMA3 " in d
                                 for d in descs)
    problems = list(errors[:10])
    if not stopped:
        problems.append(f"mprime exited on its own (exit {rc}) before the end of the run")
    if rc is not None and rc < 0 and not stopped:
        problems.append(f"mprime killed by signal {-rc}")
    if not sizes:
        problems.append("no self-test passed")
    if descs and not isa_ok:
        problems.append(f"FFTs ran with the wrong instruction set for {mode}: {descs[0]!r}")
    if summary and (int(summary.group(4)) or int(summary.group(5))):
        problems.append(f"summary reports {summary.group(4)} errors, {summary.group(5)} warnings")
    return {"ok": not problems, "mode": mode, "fft_k": list(MODES[mode][1:]), "passed": len(sizes),
            "passed_sizes": sorted(set(sizes)), "failed_size_k": failed,
            "tests_completed": int(summary.group(1)) if summary else None, "isa_ok": isa_ok, "errors": problems}


def _threads_on(pid, cpu):
    """True if every thread of pid may run only on cpu (EnableSetAffinity=0 keeps the taskset mask)."""
    try:
        for t in os.listdir(f"/proc/{pid}/task"):
            with open(f"/proc/{pid}/task/{t}/status") as f:
                allowed = next(l.split(":", 1)[1].strip() for l in f if l.startswith("Cpus_allowed_list"))
            if allowed != str(cpu):
                return False
        return True
    except (OSError, StopIteration):
        return None


def run_core(cpu, mode, seconds, wd, full_cycle=False, mark=None):
    """Run mprime on one logical CPU for `seconds` (or one full FFT cycle, for at most 40 min). mark(phase) tags
    telemetry as 'p95' or 'suspended'. Returns parse()'s dict and facts about the run."""
    if mode not in MODES:
        raise ValueError(f"mode is one of {sorted(MODES)}")
    if not os.access(MPRIME, os.X_OK):
        raise FileNotFoundError(f"{MPRIME} is missing: download mprime {VERSION} into tools/mprime (README)")
    mark = mark or (lambda phase: None)
    wd = os.path.abspath(wd)
    with open(os.path.join(wd, "prime.txt"), "w") as f:
        f.write(prime_txt(mode))
    out_path, res_path = os.path.join(wd, "stdout.log"), os.path.join(wd, "results.txt")
    env = dict(os.environ, LD_LIBRARY_PATH=MPRIME_DIR)
    # setpriv --pdeathsig KILL: if ctune dies without its finally, the kernel kills mprime instead of leaving it
    # running (or SIGSTOPped) on the core. setpriv and taskset both exec, so p.pid is mprime's pid.
    with open(out_path, "w") as out:
        p = subprocess.Popen(["setpriv", "--pdeathsig", "KILL", "taskset", "-c", str(cpu), MPRIME, "-t", f"-W{wd}"],
                             stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env, cwd=wd)
    t0 = time.time()
    limit = FULL_CYCLE_CAP_S if full_cycle else seconds
    affinity, stopped, cycle_done, no_progress = None, False, False, False
    next_suspend = t0 + RUN_S
    mark("p95")
    try:
        while time.time() - t0 < limit and p.poll() is None:
            time.sleep(0.5)
            if affinity is None and time.time() - t0 > 5:
                affinity = _threads_on(p.pid, cpu)
                if affinity is False:
                    break
            if time.time() >= next_suspend:
                mark("suspended")
                os.kill(p.pid, signal.SIGSTOP)
                time.sleep(SUSPEND_S)
                os.kill(p.pid, signal.SIGCONT)
                mark("p95")
                next_suspend = time.time() + RUN_S
            sizes = passed_sizes(_read(res_path))
            if not sizes and time.time() - t0 > NO_PROGRESS_S:
                no_progress = True
                break
            if full_cycle and len(sizes) != len(set(sizes)):
                cycle_done = True    # the first repeated size: every length of the mode has passed once
                break
    finally:
        if p.poll() is None:
            stopped = True
            for sig in (signal.SIGCONT, signal.SIGTERM):
                try:
                    os.kill(p.pid, sig)
                except ProcessLookupError:
                    pass
            try:
                p.wait(15)   # mprime gives its worker 5 s to stop
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
    r = parse(_read(res_path), _read(out_path), mode, stopped, p.returncode)
    if affinity is False:
        r["ok"] = False
        r["errors"].append(f"mprime threads left CPU {cpu}: the result does not test this core")
    if no_progress:
        r["ok"] = False
        r["errors"].append(f"no self-test passed in {NO_PROGRESS_S} s")
    if stopped and not r["passed"] and not any(p.search(e) for e in r["errors"] for p in FAIL) and not no_progress \
            and affinity is not False and time.time() - t0 < NO_PROGRESS_S:
        r["inconclusive"] = "the run ended before one FFT test could finish; give each core at least 60 s"
    if full_cycle and not cycle_done and r["ok"]:
        r["note"] = f"the {FULL_CYCLE_CAP_S // 60} min cap ended the run before one full FFT cycle"
    r.update(seconds=round(time.time() - t0), affinity_ok=affinity, full_cycle=cycle_done if full_cycle else None,
             mprime=VERSION, log_tail=_lines(_read(out_path))[-6:])
    return r


def _read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""
