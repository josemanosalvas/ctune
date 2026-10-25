"""Detached test runs as systemd user units, so a long test outlives the terminal, the agent session and logout.

Each run is a transient unit `ctune-run-<kind>-<timestamp>.service` started with `systemd-run --user` (lingering is
on). The settings come from the systemd 259 man pages and probe units on this machine (docs/research/reuse-tools.md):
- KillMode=mixed + KillSignal=SIGINT: `systemctl --user stop` raises KeyboardInterrupt in Python, so `finally`
  blocks stop the loads; systemd then SIGKILLs whatever is left in the unit's cgroup. The default SIGTERM skips them.
  A gpu-burn container runs in podman's own scope, outside that cgroup, so podman's --timeout caps it instead.
- TimeoutStopSec=90s + TimeoutStopFailureMode=kill: this machine sets 15 s and Fedora aborts (SIGABRT) by default.
- OOMPolicy=continue: a kernel OOM kill of one stress child must not stop the whole test.
- --expand-environment=no: systemd would expand ${...} and $$ inside the JSON spec. python3 -u: -I ignores
  PYTHONUNBUFFERED, and the log would fill only at exit.
- No --collect and no -r: a failed run stays loaded for inspection; a clean one unloads and its result is in the
  journal. Transient units vanish at reboot, so the fsync'd crash marker (ctune.core.stress) still records crashes.
"""
import json
import os
import subprocess
import sys
import time
import traceback

from ctune.core import journal, stress
from ctune.core.paths import DATA, REPO

RUNS = os.path.join(DATA, "runs")
BIN = os.path.join(REPO, "bin", "ctune")
PREFIX = "ctune-run-"


def _systemctl(*args, timeout=30):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout,
                          env=stress.user_env())


def units():
    """Loaded ctune run units (active ones and failed ones kept for inspection)."""
    r = _systemctl("show", f"{PREFIX}*", "-p", "Id", "-p", "ActiveState", "-p", "SubState", "-p", "Result")
    out, cur = [], {}
    for line in r.stdout.splitlines() + [""]:
        if not line.strip():
            if cur.get("Id"):
                out.append(cur)
            cur = {}
        elif "=" in line:
            k, v = line.split("=", 1)
            cur[k] = v
    return out


def start(kind, **kwargs):
    if os.geteuid() == 0:
        raise RuntimeError("run ctune tests as the user: systemd-run --user would target root's manager")
    stress.check_interrupted()
    busy = stress.running() or next((u for u in units() if u.get("ActiveState") in stress.ACTIVE_STATES), None)
    if busy:
        raise RuntimeError(f"a test is already running: {busy.get('test') or busy.get('Id')} "
                           f"(phase {busy.get('phase', '?')}); stop it with `ctune test-stop`")
    for u in units():   # failed runs were kept for inspection; the journal already holds their record
        if u.get("ActiveState") == "failed":
            _systemctl("reset-failed", u["Id"])
    os.makedirs(RUNS, exist_ok=True)
    stem = f"{PREFIX}{kind.replace('_', '-')}-{time.strftime('%Y%m%d-%H%M%S')}"
    unit, log = f"{stem}.service", os.path.join(RUNS, f"{stem}.log")
    spec = json.dumps({"kind": kind, "kwargs": kwargs})
    cmd = ["systemd-run", "--user", f"--unit={unit}", f"--description=ctune {kind}", "--service-type=exec",
           "--expand-environment=no", f"--working-directory={REPO}", "--json=short",
           "-p", f"StandardOutput=append:{log}", "-p", "StandardError=inherit",
           "-p", "KillMode=mixed", "-p", "KillSignal=SIGINT", "-p", "TimeoutStopSec=90s",
           "-p", "TimeoutStopFailureMode=kill", "-p", "OOMPolicy=continue", f"--setenv=CTUNE_UNIT={unit}",
           sys.executable, "-I", "-B", "-u", BIN, "_run", spec]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=stress.user_env())
    if r.returncode:
        raise RuntimeError(f"systemd-run failed ({r.returncode}): {r.stderr.strip()[-600:]}")
    for _ in range(30):   # wait until the test has written its marker
        time.sleep(0.5)
        m = stress.marker_read()
        if m and m.get("unit") == unit:
            return {"started": kind, "unit": unit, "log": log, "phase": m.get("phase"),
                    "next": "poll test_status; stop with test_stop; the result lands in the journal"}
        st = stress.unit_state(unit)
        if st.get("LoadState") != "loaded" or st.get("ActiveState") == "failed":
            break
    raise RuntimeError(f"the {kind} run did not start ({stress.unit_state(unit).get('Result')}): "
                       f"{stress._log_tail(unit) or ''}")


def status():
    """The running test (phase, elapsed, log tail), failed runs kept by systemd, and the last test result."""
    interrupted = stress.check_interrupted()
    m = stress.running()
    out = {"running": m, "interrupted_recorded": interrupted}
    if m:
        started = time.mktime(time.strptime(m["started"][:19], "%Y-%m-%dT%H:%M:%S"))
        out["elapsed_min"] = round((time.time() - started) / 60, 1)
        out["log_tail"] = stress._log_tail(m.get("unit"), 800)
    failed = [u for u in units() if u.get("ActiveState") == "failed"]
    if failed:
        out["failed_units"] = failed
    last = [r for r in journal.tail(DATA, 50, "result") if r.get("test") or r.get("verdict")]
    if last:
        r = last[-1]
        out["last_result"] = {k: r.get(k) for k in ("ts", "domain", "text", "test", "verdict", "red_phases",
                                                    "red_cores", "red_sources") if r.get(k) is not None}
    return out


def stop():
    """Stop the running detached test: SIGINT to the test, then systemd kills what is left (up to 90 s)."""
    m = stress.running()
    unit = (m or {}).get("unit") or next((u["Id"] for u in units() if u.get("ActiveState") in stress.ACTIVE_STATES),
                                         None)
    if not unit:
        raise RuntimeError("no detached ctune test is running" + (" (an inline test is: stop it with Ctrl-C)"
                                                                     if m else ""))
    r = _systemctl("stop", unit, timeout=150)
    return {"stopped": unit, "rc": r.returncode, "stderr": r.stderr.strip()[-300:] or None,
            "state": stress.unit_state(unit)}


def dispatch(spec):
    """Entry point of a detached run (bin/ctune _run). Records a stop or a crash of ctune itself in the journal,
    because the marker is removed when a test ends by an exception."""
    d = json.loads(spec)
    kind, kw = d["kind"], d.get("kwargs", {})
    if kind == "mem_test":
        from ctune.mem import test as t
        fn, domain = t.test, "mem"
    elif kind == "signoff":
        from ctune.core import signoff
        fn, domain = signoff.run, "signoff"
    elif kind == "cpu_test":
        from ctune.cpu import api as cpu
        fn, domain = cpu.test, "cpu"
    elif kind == "gpu_test":
        from ctune.gpu import api as gpu
        fn, domain = gpu.test, "gpu"
    else:
        raise ValueError(f"unknown run kind {kind}")
    try:
        return fn(**kw)
    except KeyboardInterrupt:
        journal.add(DATA, "note", domain=domain, text=f"{kind} stopped before it finished (systemctl stop or Ctrl-C)",
                    unit=os.environ.get("CTUNE_UNIT"), kwargs=kw)
        raise
    except Exception as e:
        journal.add(DATA, "note", domain=domain, text=f"{kind} failed with a ctune error, not a stability result: "
                    f"{type(e).__name__}: {e}", unit=os.environ.get("CTUNE_UNIT"), kwargs=kw,
                    traceback=traceback.format_exc()[-3000:])
        raise


def boot_check():
    """Run once per boot (ctune-boot-check.service): record a test cut short by a crash, and keep this boot's
    hardware-error and reset-reason lines in data/boots/ before the busy journal rotates them away."""
    rec = stress.check_interrupted()
    lines = stress._kernel_lines("0", r"Hardware Error|Machine check|mce:|reset reason") or []
    d = os.path.join(DATA, "boots")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{time.strftime('%Y%m%d-%H%M%S')}_{stress.boot_id()}.txt")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return {"interrupted_recorded": rec, "saved": path, "lines": len(lines)}


UNIT_FILE = os.path.expanduser("~/.config/systemd/user/ctune-boot-check.service")


def install_boot_check():
    """Write and enable the user unit that runs boot_check at every boot (lingering starts it without a login)."""
    os.makedirs(os.path.dirname(UNIT_FILE), exist_ok=True)
    with open(UNIT_FILE, "w") as f:
        f.write(f"""[Unit]
Description=ctune: record a test cut short by a crash; keep this boot's hardware-error lines

[Service]
Type=oneshot
WorkingDirectory={REPO}
ExecStart={sys.executable} -I -B {BIN} boot-check

[Install]
WantedBy=default.target
""")
    _systemctl("daemon-reload")
    r = _systemctl("enable", os.path.basename(UNIT_FILE))
    return {"unit_file": UNIT_FILE, "enabled": r.returncode == 0, "stderr": r.stderr.strip() or None}
