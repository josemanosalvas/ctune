"""Detached test runs: a long test keeps running when the agent session or terminal ends.

start() launches `bin/ctune _run <json>` in its own session with output in data/runs/. The test writes the shared
crash marker (ctune.core.stress) and its result to the journal, so status() needs only the marker and the journal.
"""
import json
import os
import subprocess
import sys
import time

from ctune.core import journal, stress
from ctune.core.paths import DATA, REPO

RUNS = os.path.join(DATA, "runs")
BIN = os.path.join(REPO, "bin", "ctune")


def start(kind, **kwargs):
    stress.check_interrupted()
    busy = stress.running()
    if busy:
        raise RuntimeError(f"a {busy.get('test')} test is already running (pid {busy.get('pid')}, phase {busy.get('phase')})")
    os.makedirs(RUNS, exist_ok=True)
    log = os.path.join(RUNS, f"{time.strftime('%Y%m%d-%H%M%S')}_{kind}.log")
    with open(log, "w") as f:
        p = subprocess.Popen([sys.executable, "-I", "-B", BIN, "_run", json.dumps({"kind": kind, "kwargs": kwargs})],
                             stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                             cwd=REPO)
    for _ in range(30):   # wait until the test has written its marker
        time.sleep(0.5)
        m = stress.marker_read()
        if m and m.get("pid") == p.pid:
            return {"started": kind, "pid": p.pid, "log": log, "marker": m,
                    "next": "poll test_status; the result lands in the journal"}
        if p.poll() is not None:
            break
    tail = open(log).read()[-1500:]
    raise RuntimeError(f"the {kind} run did not start (exit {p.poll()}): {tail}")


def status():
    """The running test (phase, elapsed) or, if none, the latest test result in the journal."""
    interrupted = stress.check_interrupted()
    m = stress.running()
    out = {"running": m, "interrupted_recorded": interrupted}
    if m:
        started = time.mktime(time.strptime(m["started"][:19], "%Y-%m-%dT%H:%M:%S"))
        out["elapsed_min"] = round((time.time() - started) / 60, 1)
    logs = sorted(os.listdir(RUNS)) if os.path.isdir(RUNS) else []
    if logs:
        with open(os.path.join(RUNS, logs[-1])) as f:
            out["log"] = {"file": logs[-1], "tail": f.read()[-800:]}
    last = [r for r in journal.tail(DATA, 50, "result") if r.get("test") or r.get("verdict")]
    if last:
        r = last[-1]
        out["last_result"] = {k: r.get(k) for k in ("ts", "domain", "text", "test", "verdict", "red_phases",
                                                    "red_cores", "red_sources") if r.get(k) is not None}
    return out


def dispatch(spec):
    """Entry point of a detached run (bin/ctune _run)."""
    d = json.loads(spec)
    kind, kw = d["kind"], d.get("kwargs", {})
    if kind == "mem_test":
        from ctune.mem import test as t
        fn = t.test
    elif kind == "signoff":
        from ctune.core import signoff
        fn = signoff.run
    elif kind == "cpu_test":
        from ctune.cpu import api as cpu
        fn = cpu.test
    elif kind == "gpu_test":
        from ctune.gpu import api as gpu
        fn = gpu.test
    else:
        raise ValueError(f"unknown run kind {kind}")
    return fn(**kw)   # every test writes the crash marker as it starts
