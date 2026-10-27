"""Shared pieces of every ctune stability test: the crash marker, background loads, telemetry and error deltas.

The crash marker is one file for all domains. A test writes it before each phase. If the machine freezes or reboots,
the next ctune call in a new boot finds a marker from another boot and records the test as red. Tests can nest
(signoff runs the cpu test): the inner test writes its phase into the outer test's marker.
"""
import json
import os
import re
import shutil
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
    dfd = os.open(DATA, os.O_RDONLY)   # make the rename durable too: a reset right after it must still find the marker
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def user_env():
    """Environment for systemctl/systemd-run --user, also when the caller has no session variables."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


UNIT_PROPS = ("Id", "LoadState", "ActiveState", "SubState", "Result", "InvocationID", "ExecMainPID", "ExecMainCode",
              "ExecMainStatus", "ExecMainStartTimestamp", "ExecMainExitTimestamp")
ACTIVE_STATES = ("activating", "active", "deactivating", "reloading", "refreshing")


def unit_state(unit):
    """systemctl --user show for one unit, or None when the user manager cannot be asked (a failed or timed-out
    query, or root, whose --user means root's own manager). LoadState=not-found means the unit is gone: it finished
    cleanly or never existed."""
    if os.geteuid() == 0:
        return None
    try:
        r = subprocess.run(["systemctl", "--user", "show", unit, "--timestamp=unix"] +
                           [f"-p{p}" for p in UNIT_PROPS], capture_output=True, text=True, timeout=15, env=user_env())
    except (OSError, subprocess.SubprocessError):
        return None
    st = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    return st if r.returncode == 0 and "LoadState" in st else None


def running(boot=None):
    """The marker of a test running right now in this boot, or None. A detached run (systemd user unit) is running
    while its unit is active with the same invocation; an inline run while its process is alive."""
    m = marker_read()
    if not m or m.get("boot_id") != (boot or boot_id()):
        return None
    if m.get("unit"):
        st = unit_state(m["unit"])
        if st is None:   # unknown: never call a test gone on a failed query
            return m if m.get("pid") == os.getpid() or _alive(m.get("pid")) or os.geteuid() == 0 else None
        same = not m.get("invocation_id") or st.get("InvocationID") == m["invocation_id"]
        return m if st.get("LoadState") == "loaded" and st.get("ActiveState") in ACTIVE_STATES and same else None
    return m if m.get("pid") == os.getpid() or _alive(m.get("pid")) else None


FORENSIC = r"Hardware Error|Machine check|mce:|reset reason|Xid|AER|DPC|BTRFS.*(csum|corrupt|error|read-only)|" \
           r"watchdog|soft lockup|BUG:|Oops|Kernel panic"


def _kernel_lines(boot, pattern=FORENSIC, limit=200):
    """The newest `limit` kernel lines of a boot matching pattern (pattern None: unfiltered). Callers check that the
    boot is still in the journal first: journalctl exits 1 both for 'no such boot' and for 'no match'."""
    args = ["journalctl", "-k", "-b", boot, "--no-pager", "-o", "short-iso"]
    args += ["-g", pattern] if pattern else ["-n", str(limit)]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return [l for l in r.stdout.splitlines() if l and not l.startswith("-- ")][-limit:]


def journal_boots():
    """Boot ids still in the journal, oldest first (dashes removed)."""
    try:
        r = subprocess.run(["journalctl", "--list-boots", "-o", "json", "--no-pager"], capture_output=True, text=True,
                           timeout=30)
        return [b["boot_id"].replace("-", "") for b in json.loads(r.stdout or "[]")]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return []


def _claim():
    """Take the marker atomically, so two callers never both record the same test. None if it is already gone."""
    claim = f"{MARKER}.claim-{os.getpid()}"
    try:
        os.rename(MARKER, claim)
    except FileNotFoundError:
        return None
    try:
        with open(claim) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}
    finally:
        os.remove(claim)


def check_interrupted(boot=None):
    """Record a test that did not finish.
    Another boot: the machine crashed or rebooted (red). The record keeps the crashed boot's newest error lines and an
    unfiltered tail, plus the next boot's hardware-error and reset-reason lines, while the journal still has them (it
    rotates within hours here; `ctune boot-check --install` saves them at every boot).
    Same boot, process gone: it was stopped, killed or ran out of memory, which says nothing about stability (a note).
    An unknown state (the user manager cannot be asked) records nothing and keeps the marker."""
    m = marker_read()
    if not m:
        return None
    this_boot = boot or boot_id()
    if m.get("boot_id") == this_boot:
        if running(this_boot):
            return None
        st = unit_state(m["unit"]) if m.get("unit") else None
        if m.get("unit") and st is None:
            return None
        m = _claim()
        if not m:
            return None
        how = "process gone in the same boot: stopped, killed or out of memory"
        if st and st.get("ActiveState") == "failed":
            how = (f"its unit {m['unit']} failed: Result={st.get('Result')}, ExecMainCode={st.get('ExecMainCode')}, "
                   f"ExecMainStatus={st.get('ExecMainStatus')}")
            subprocess.run(["systemctl", "--user", "reset-failed", m["unit"]], capture_output=True, env=user_env())
        return journal.add(DATA, "note", domain=m.get("domain"), text=f"{m.get('test')} test stopped without a result "
                           f"({how})", marker=m, log_tail=_log_tail(m.get("unit")))
    m = _claim()
    if not m:
        return None
    crashed = m.get("boot_id", "").replace("-", "")
    boots = journal_boots()
    in_journal = crashed in boots
    nxt = boots[boots.index(crashed) + 1] if in_journal and boots.index(crashed) + 1 < len(boots) else None
    return journal.add(DATA, "result", domain=m.get("domain"),
                       text=f"{m.get('test')} test interrupted by a crash or reboot", verdict="red",
                       test=m.get("test"), phase=m.get("phase"), sub=m.get("sub"), core=m.get("core"),
                       settings=m.get("settings"), started=m.get("started"), last_update=m.get("updated"),
                       crashed_boot_id=crashed, journal_lost=not in_journal,
                       crashed_boot_error_lines=_kernel_lines(crashed) if in_journal else None,
                       crashed_boot_kernel_tail=_kernel_lines(crashed, None, 50) if in_journal else None,
                       next_boot_id=nxt,
                       next_boot_hardware_lines=(_kernel_lines(nxt, r"Hardware Error|Machine check|mce:|reset reason")
                                                 if nxt else None),
                       saved_boot_files=sorted(f for f in os.listdir(os.path.join(DATA, "boots"))
                                               if crashed[:8] in f.replace("-", "")) if os.path.isdir(
                           os.path.join(DATA, "boots")) else [],
                       next_step=(f"read `journalctl -k -b {crashed}` (if journal_lost is false) and the "
                                  "'Previous system reset reason' line, which names sync-flood, watchdog or shutdown "
                                  "resets; runtime settings are back to the BIOS values"))


def _log_tail(unit, n=1500):
    if not unit:
        return None
    try:
        with open(os.path.join(DATA, "runs", unit.removesuffix(".service") + ".log"), errors="replace") as f:
            return f.read()[-n:]
    except OSError:
        return None


class _Run:
    def __init__(self, domain, test, settings, parent=None):
        self.parent, self.domain, self.test, self.phase = parent, domain, test, None
        self.state = {"boot_id": boot_id(), "pid": os.getpid(), "domain": domain, "test": test, "started": now(),
                      "settings": settings, "unit": os.environ.get("CTUNE_UNIT"),
                      "invocation_id": os.environ.get("INVOCATION_ID") if os.environ.get("CTUNE_UNIT") else None}

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


def _slept_s():
    """Seconds this boot has spent suspended: CLOCK_BOOTTIME counts them, CLOCK_MONOTONIC does not."""
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()


def _inhibit_sleep(why):
    """Holders that block suspend while a test runs. A suspend pauses the loads while their timers keep counting: on
    2026-10-27 a 90 min RAM test slept 34 min of it. logind's lock covers sleep requests in general; GNOME's idle
    suspend asks only its own session, so it gets a session lock too."""
    cmds = [["systemd-inhibit", "--what=sleep", "--who=ctune", f"--why={why}", "--mode=block"]]
    if "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        cmds.append(["gnome-session-inhibit", "--inhibit", "suspend", "--app-id", "ctune", "--reason", why])
    return [subprocess.Popen(c + ["sleep", "infinity"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True, env=user_env())
            for c in cmds if shutil.which(c[0])]


def active_units():
    """Detached ctune tests running now, whatever data directory they use. [] when the user manager cannot be asked."""
    if os.geteuid() == 0:
        return []
    try:
        r = subprocess.run(["systemctl", "--user", "list-units", "ctune-run-*", "--plain", "--no-legend",
                            "--state=activating,active,deactivating"], capture_output=True, text=True, timeout=15,
                           env=user_env())
    except (OSError, subprocess.SubprocessError):
        return []
    return [l.split()[0] for l in r.stdout.splitlines() if l.strip()]


@contextmanager
def run(domain, test, settings=None):
    """Marker for one test. Nested inside another test, it reports into the outer marker."""
    if not _ACTIVE:
        busy = running()
        if busy and busy.get("pid") != os.getpid():
            raise RuntimeError(f"a {busy.get('test')} test is already running (pid {busy.get('pid')})")
        # an inline test with another CTUNE_DATA cannot see a detached test's marker (runner.start checks units)
        others = [] if os.environ.get("CTUNE_UNIT") else active_units()
        if others:
            raise RuntimeError(f"a detached test is already running: {', '.join(others)}")
    r = _Run(domain, test, settings, parent=_ACTIVE[-1] if _ACTIVE else None)
    _ACTIVE.append(r)
    holders = _inhibit_sleep(f"{domain} {test} test running") if r.parent is None else []
    slept0 = _slept_s()
    if holders:
        time.sleep(0.3)   # a holder that cannot take its lock exits at once
    try:
        r.update(phase="start", sleep_blocked_by=[p.args[0] for p in holders if p.poll() is None] or None)
        yield r
    finally:
        _ACTIVE.pop()
        if r.parent is None:
            for p in holders:
                p.terminate()
                p.wait(10)
            slept = round(_slept_s() - slept0)
            if slept > 5:
                journal.add(DATA, "note", domain=domain, slept_s=slept, started=r.state["started"],
                            text=f"the machine was suspended for {slept} s during the {domain} {test} test that "
                                 f"started at {r.state['started']}: its result is inconclusive; run it again")
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
        self._chunks = chunks = []           # result() reads them even if a helper still holds the pipe open
        for line in self.proc.stdout:
            chunks.append(line)
            if len(chunks) > 20000:          # keep the head (setup) and the tail (results)
                del chunks[200:10000]
        self.out = "".join(chunks)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def signal_stop(self):
        if self.alive():
            self.stopped = True
            try:
                os.killpg(self.proc.pid, self.stop_signal)
            except ProcessLookupError:
                pass

    def wait_stop(self, deadline):
        if self.proc is None:
            return
        try:
            self.proc.wait(max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait()

    def stop(self, grace=30):
        self.signal_stop()
        self.wait_stop(time.time() + grace)

    def result(self):
        if self.proc is None:
            return {"ok": False, "errors": ["not started"]}
        self.proc.wait()
        self._reader.join(10)
        r = self.parse(self.proc.returncode, self.out or "".join(getattr(self, "_chunks", [])), self.stopped)
        r["name"] = self.name
        return r


def run_loads(loads, seconds, check_every=5, on_tick=None):
    """Start the loads, wait `seconds`, then stop them and return their results. A load that exits early ends the
    phase: its result says why, and the others are stopped."""
    t0 = time.time()
    early = None
    try:
        for ld in loads:
            ld.start()
        t_end = t0 + seconds
        while time.time() < t_end and not early:
            time.sleep(min(check_every, max(0, t_end - time.time())))
            why = on_tick() if on_tick else None   # a guardrail can end the phase, e.g. a temperature limit
            if why:
                early = early or why
            for ld in loads:
                margin = 20 if getattr(ld, "self_timed", False) else 0
                if not ld.alive() and time.time() < t_end - margin and not getattr(ld, "optional", False):
                    early = early or ld.name
        for ld in loads:   # self-timed loads print their summary after their own timer: give them time to finish
            if getattr(ld, "self_timed", False) and not early:
                deadline = time.time() + 120
                while ld.alive() and time.time() < deadline:
                    time.sleep(1)
    finally:   # also on an exception or a stop (KeyboardInterrupt from systemctl stop or Ctrl-C)
        _stop_all(loads)
    res = [ld.result() for ld in loads]
    for ld, r in zip(loads, res):
        if getattr(ld, "optional", False) and r.get("unavailable"):
            r["ok"], r["skipped"] = True, True   # it never ran, so it says nothing about stability
    for r in res:
        if early and r["name"] == early:
            r["ok"] = False
            r.setdefault("errors", []).append(f"exited after {round(time.time() - t0)} s, before the end of the phase")
    return {"loads": res, "early_exit": early, "seconds": round(time.time() - t0)}


def _stop_all(loads, grace=30):
    """Signal every load first, then wait on all against one deadline, with SIGINT blocked so a second Ctrl-C cannot
    leave a load running in its own session."""
    old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    try:
        for ld in loads:
            try:
                ld.signal_stop()
            except BaseException:
                pass
        deadline = time.time() + grace
        for ld in loads:
            try:
                ld.wait_stop(deadline)
            except BaseException:
                pass
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


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

    def __init__(self, path=None, size_mb=4096, seconds=None):
        self.path = path or os.path.join(os.path.expanduser("~"), ".cache", "ctune-fabric-read.bin")
        self.size_mb, self.name, self.stopped, self.seconds = size_mb, "nvme-read", False, seconds
        self.proc = None

    def start(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path) or os.path.getsize(self.path) < self.size_mb << 20:
            subprocess.run(["dd", "if=/dev/urandom", f"of={self.path}", "bs=4M", f"count={self.size_mb // 4}",
                            "status=none"], check=True)
        loop = f'while :; do dd if="{self.path}" of=/dev/null bs=4M iflag=direct status=none || exit 3; done'
        cap = ["timeout", str(self.seconds + 300)] if self.seconds else []   # ends by itself if ctune dies
        self.proc = subprocess.Popen(cap + ["bash", "-c", loop], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     text=True, start_new_session=True)
        return self

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def signal_stop(self):
        if self.alive():
            self.stopped = True
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def wait_stop(self, deadline):
        if self.proc is None:
            return
        try:
            self.proc.wait(max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait()

    def stop(self, grace=10):
        self.signal_stop()
        self.wait_stop(time.time() + grace)

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
