"""Shared pieces of every ctune stability test: the crash marker, background loads, telemetry and error deltas.

The crash marker is one file for all domains. A test writes it before each phase. If the machine freezes or reboots,
the next ctune call in a new boot finds a marker from another boot and records the test as red. Tests can nest
(signoff runs the cpu test): the inner test writes its phase into the outer test's marker.
"""
import json
import os
import re
import signal
import statistics
import subprocess
import threading
import time
from contextlib import contextmanager

from ctune.core import errors, journal, sensors
from ctune.core.paths import DATA, TOOLS

MARKER = os.path.join(DATA, "test_running.json")
STRESSAPPTEST = os.path.join(TOOLS, "stressapptest", "stressapptest")
YCRUNCHER = os.path.join(TOOLS, "y-cruncher", "y-cruncher")


def _rd(p):
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def boot_id():
    return _rd("/proc/sys/kernel/random/boot_id")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ---------------------------------------------------------------- crash marker
def marker_read():
    try:
        with open(MARKER) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def marker_write(d):
    os.makedirs(DATA, exist_ok=True)
    tmp = MARKER + ".tmp"
    with open(tmp, "w") as f:
        json.dump({**d, "updated": now()}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, MARKER)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def running():
    """The marker of a test running right now in this boot, or None."""
    m = marker_read()
    if m and m.get("boot_id") == boot_id() and _alive(m.get("pid")):
        return m
    return None


def check_interrupted(boot=None):
    """Record a test that did not finish. Another boot: the machine crashed or rebooted (red). Same boot with the
    process gone: it was killed (an OOM kill or the user), which says nothing about stability (a note)."""
    m = marker_read()
    if not m:
        return None
    this_boot = boot or boot_id()
    if m.get("boot_id") == this_boot:
        if _alive(m.get("pid")):
            return None
        rec = journal.add(DATA, "note", domain=m.get("domain"), text=f"{m.get('test')} test stopped without a result "
                          "(process gone in the same boot: killed or out of memory)", marker=m)
    else:
        try:
            r = subprocess.run(["journalctl", "-k", "-b", "0", "--no-pager", "-o", "short-iso", "-g", "Hardware Error"],
                               capture_output=True, text=True, timeout=30)
            boot_mce = [l for l in r.stdout.splitlines() if l and not l.startswith("-- ")][:20]
        except (OSError, subprocess.SubprocessError):
            boot_mce = []
        rec = journal.add(DATA, "result", domain=m.get("domain"),
                          text=f"{m.get('test')} test interrupted by a crash or reboot", verdict="red",
                          test=m.get("test"), phase=m.get("phase"), sub=m.get("sub"), core=m.get("core"),
                          settings=m.get("settings"), started=m.get("started"), last_update=m.get("updated"),
                          crashed_boot_id=m.get("boot_id"), hardware_errors_this_boot=boot_mce,
                          next_step="read `journalctl -k -b -1`; runtime settings are back to the BIOS values")
    os.remove(MARKER)
    return rec


class _Run:
    def __init__(self, domain, test, settings, parent=None):
        self.parent, self.domain, self.test, self.phase = parent, domain, test, None
        self.state = {"boot_id": boot_id(), "pid": os.getpid(), "domain": domain, "test": test, "started": now(),
                      "settings": settings}

    def update(self, phase=None, **kw):
        if phase is not None:
            self.phase = phase
        if self.parent:
            self.parent._nested(f"{self.test}:{self.phase}", **kw)
        else:
            self.state.update(kw, phase=self.phase)
            self.state.pop("sub", None)
            marker_write(self.state)

    def _nested(self, path, **kw):
        """A nested test's phase, e.g. sub = "co-cycle:fft" under the signoff phase "cpu"."""
        if self.parent:
            self.parent._nested(f"{self.test}:{self.phase} > {path}", **kw)
        else:
            self.state.update(kw, sub=path)
            marker_write(self.state)


_ACTIVE = []


@contextmanager
def run(domain, test, settings=None):
    """Marker for one test. Nested inside another test, it reports into the outer marker."""
    if not _ACTIVE:
        busy = running()
        if busy and busy.get("pid") != os.getpid():
            raise RuntimeError(f"a {busy.get('test')} test is already running (pid {busy.get('pid')})")
    r = _Run(domain, test, settings, parent=_ACTIVE[-1] if _ACTIVE else None)
    _ACTIVE.append(r)
    try:
        r.update(phase="start")
        yield r
    finally:
        _ACTIVE.pop()
        if r.parent is None:
            try:
                os.remove(MARKER)
            except OSError:
                pass


# ---------------------------------------------------------------- errors
def error_snapshot():
    snap = {"t0": time.time(), "counts": errors.counts()}
    try:
        from ctune.gpu import api as gpu
        snap["xid"] = len(gpu.xid_errors())
    except Exception:
        snap["xid"] = None
    return snap


def error_delta(snap):
    """New corrected machine-check errors (by bank and subsystem), kernel hardware-error lines and Xid errors."""
    after = errors.counts()
    new = {b: n - snap["counts"].get(b, 0) for b, n in after.items() if n > snap["counts"].get(b, 0)}
    by_sub = {}
    for b, n in new.items():
        s = errors.SUBSYSTEM.get(errors._family(b), "other")
        by_sub[s] = by_sub.get(s, 0) + n
    out = {"new_corrected_errors": new, "by_subsystem": by_sub,
           "kernel_hw_errors": errors.kernel_hw_errors(since=f"@{int(snap['t0'])}")}
    if snap.get("xid") is not None:
        from ctune.gpu import api as gpu
        out["new_xid_errors"] = gpu.xid_errors()[snap["xid"]:]
    out["clean"] = not new and not out["kernel_hw_errors"] and not out.get("new_xid_errors")
    return out


# ---------------------------------------------------------------- memory
def meminfo():
    out = {}
    for line in (_rd("/proc/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        out[k] = int(v.split()[0]) // 1024   # MiB
    return out


def swap_out_mb():
    for line in (_rd("/proc/vmstat") or "").splitlines():
        if line.startswith("pswpout "):
            return int(line.split()[1]) * 4 // 1024
    return 0


def test_size_mb(reserve_mb=1536, share=1.0):
    """Memory a RAM test can take without pushing the desktop into swap: MemAvailable minus a reserve."""
    return max(256, int((meminfo().get("MemAvailable", 0) - reserve_mb) * share))


# ---------------------------------------------------------------- loads
class Load:
    """A background stress process with an output parser: parse(rc, output, stopped_by_us) -> dict with 'ok'."""

    def __init__(self, name, cmd, parse, cwd=None, stop_signal=signal.SIGINT, self_timed=False, optional=False):
        self.name, self.cmd, self.parse, self.cwd, self.stop_signal = name, cmd, parse, cwd, stop_signal
        self.self_timed = self_timed   # ends by itself at the end of the phase (stressapptest -s)
        self.optional = optional       # a helper load (GPU heat): if it cannot start, the phase goes on without it
        self.proc, self.stopped, self.out = None, False, ""

    def start(self):
        self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     cwd=self.cwd, text=True, errors="replace", start_new_session=True)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        return self

    def _read(self):
        chunks = []
        for line in self.proc.stdout:
            chunks.append(line)
            if len(chunks) > 20000:          # keep the head (setup) and the tail (results)
                del chunks[200:10000]
        self.out = "".join(chunks)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self, grace=30):
        if self.alive():
            self.stopped = True
            try:
                os.killpg(self.proc.pid, self.stop_signal)
                self.proc.wait(grace)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
            except ProcessLookupError:
                pass

    def result(self):
        if self.proc is None:
            return {"ok": False, "errors": ["not started"]}
        self.proc.wait()
        self._reader.join(10)
        r = self.parse(self.proc.returncode, self.out, self.stopped)
        r["name"] = self.name
        return r


def run_loads(loads, seconds, check_every=5, on_tick=None):
    """Start the loads, wait `seconds`, then stop them and return their results. A load that exits early ends the
    phase: its result says why, and the others are stopped."""
    t0 = time.time()
    for ld in loads:
        ld.start()
    t_end = t0 + seconds
    early = None
    while time.time() < t_end and not early:
        time.sleep(min(check_every, max(0, t_end - time.time())))
        if on_tick:
            on_tick()
        for ld in loads:
            margin = 20 if getattr(ld, "self_timed", False) else 0
            if not ld.alive() and time.time() < t_end - margin and not getattr(ld, "optional", False):
                early = early or ld.name
    for ld in loads:   # self-timed loads print their summary after their own timer: give them time to finish
        if getattr(ld, "self_timed", False) and not early:
            deadline = time.time() + 120
            while ld.alive() and time.time() < deadline:
                time.sleep(1)
    for ld in loads:
        ld.stop()
    res = [ld.result() for ld in loads]
    for ld, r in zip(loads, res):
        if getattr(ld, "optional", False) and r.get("unavailable"):
            r["ok"], r["skipped"] = True, True   # it never ran, so it says nothing about stability
    for r in res:
        if early and r["name"] == early:
            r["ok"] = False
            r.setdefault("errors", []).append(f"exited after {round(time.time() - t0)} s, before the end of the phase")
    return {"loads": res, "early_exit": early, "seconds": round(time.time() - t0)}


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def parse_sat(rc, out, stopped):
    """stressapptest: 'Status: PASS' and zero hardware incidents and errors."""
    m = re.search(r"Completed: ([\d.]+)M in ([\d.]+)s ([\d.]+)MB/s, with (\d+) hardware incidents, (\d+) errors", out)
    errs = [l.strip() for l in out.splitlines() if re.search(r"Report Error|miscompare|Error:|FAIL", l)][:10]
    ok = "Status: PASS" in out and m is not None and m.group(4) == "0" and m.group(5) == "0" and not stopped
    if stopped and "Status:" not in out:
        errs.append("stopped before stressapptest finished")
    return {"ok": ok and rc == 0, "rc": rc, "mb_s": float(m.group(3)) if m else None,
            "hardware_incidents": int(m.group(4)) if m else None, "errors_reported": int(m.group(5)) if m else None,
            "errors": errs, "finished_on_its_own": not stopped}


def parse_ycruncher(rc, out, stopped):
    """y-cruncher stress: count 'Passed' per test; any 'Failed' or error line is red. ctune stops it with SIGINT."""
    # progress overwrites a line with carriage returns: keep the last state of each line
    lines = [l.split("\r")[-1] for l in _ANSI.sub("", out).split("\n")]
    passed = {}
    for l in lines:
        m = re.search(r"Running (\w+): Passed", l)
        if m:
            passed[m.group(1)] = passed.get(m.group(1), 0) + 1
    bad = [l.strip() for l in lines
           if re.search(r"Failed|Error|error|mismatch|Exception", l)
           and not re.search(r"Stop on Error|Insufficient permissions|Further messages", l)][:10]
    ok = bool(passed) and not bad and (stopped or rc == 0)
    if not stopped and rc not in (0, None):
        bad.append(f"y-cruncher exit status {rc}")
    return {"ok": ok, "rc": rc, "passed": passed, "errors": bad}


def stressapptest_cmd(mb, seconds, threads=None, extra=()):
    cmd = [STRESSAPPTEST, "-M", str(mb), "-s", str(seconds), "-W"]
    if threads:
        cmd += ["-m", str(threads)]
    return cmd + list(extra)


def ycruncher_cmd(mb, seconds, tests=()):
    per = max(10, seconds // 8)
    return [YCRUNCHER, "stress", f"-M:{mb}M", f"-D:{per}", f"-TL:{max(seconds, per)}", *tests]


class DiskReader:
    """Read-only NVMe traffic for fabric tests: one temp file written once, then read with O_DIRECT in a loop."""

    def __init__(self, path=None, size_mb=4096):
        self.path = path or os.path.join(os.path.expanduser("~"), ".cache", "ctune-fabric-read.bin")
        self.size_mb, self.name, self.stopped = size_mb, "nvme-read", False
        self.proc = None

    def start(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path) or os.path.getsize(self.path) < self.size_mb << 20:
            subprocess.run(["dd", "if=/dev/urandom", f"of={self.path}", "bs=4M", f"count={self.size_mb // 4}",
                            "status=none"], check=True)
        loop = f'while :; do dd if="{self.path}" of=/dev/null bs=4M iflag=direct status=none || exit 3; done'
        self.proc = subprocess.Popen(["bash", "-c", loop], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     text=True, start_new_session=True)
        return self

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self, grace=10):
        if self.alive():
            self.stopped = True
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(grace)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)

    def result(self):
        err = self.proc.stderr.read() if self.proc else ""
        ok = self.stopped and not err.strip()
        return {"name": self.name, "ok": ok, "errors": [err.strip()[-300:]] if err.strip() else []}


# ---------------------------------------------------------------- telemetry
class Telemetry(threading.Thread):
    """Samples every `interval` s: board voltages, CPU Tctl, per-core max temperature and package power from the
    pm_table, GPU temperature and power, and swap-out. summary() gives min / avg / max per series."""

    def __init__(self, interval=5.0):
        super().__init__(daemon=True)
        self.interval, self.stop, self.series = interval, threading.Event(), {}
        self.swap0 = swap_out_mb()

    def _add(self, k, v):
        if isinstance(v, (int, float)):
            self.series.setdefault(k, []).append(v)

    def run(self):
        try:
            from ctune.gpu import nvml
            h = nvml.handle(0)
        except Exception:
            nvml = h = None
        try:
            from ctune.cpu import smu
        except Exception:
            smu = None
        while not self.stop.wait(self.interval):
            try:
                s = sensors.read()
                for k in ("VDIMM", "VSOC (VRM)", "DDR VTT (A/B)"):
                    self._add(k, s["voltages"].get(k))
                self._add("cpu_tctl_c", s["temps"].get("CPU Tctl"))
            except Exception:
                pass
            if smu:
                try:
                    pm = smu.read_pm()
                    self._add("core_temp_max_c", max(pm["core_temp_c"]))
                    self._add("ppt_w", pm["ppt_w"])
                except Exception:
                    pass
            if h is not None:
                try:
                    self._add("gpu_temp_c", nvml.temperature(h))
                    self._add("gpu_power_w", nvml.power_w(h))
                except Exception:
                    pass

    def summary(self):
        out = {k: {"min": round(min(v), 3), "avg": round(statistics.mean(v), 3), "max": round(max(v), 3)}
               for k, v in self.series.items() if v}
        out["swapped_out_mb"] = swap_out_mb() - self.swap0
        return out


@contextmanager
def telemetry(interval=5.0):
    t = Telemetry(interval)
    t.start()
    try:
        yield t
    finally:
        t.stop.set()
        t.join(interval + 5)
