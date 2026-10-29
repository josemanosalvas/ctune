"""One pass of a graphics load (a game or benchmark that checks nothing it renders) as a child process group: it
runs until it exits, a timeout or a guard stops it. A program blocked in a modal error dialog ignores SIGTERM, so a
stop sends SIGTERM, then SIGKILL after term_grace_s. Used by q2rtx (RT cores) and superposition (raster)."""
import os
import signal
import subprocess
import time


def run(argv, env, cwd, out_path, timeout_s, guard, term_grace_s):
    """(rc, seconds, why it was stopped or None). Its stdout and stderr go to out_path; guard() returning a reason
    stops it (the temperature guardrail)."""
    t0 = time.monotonic()
    with open(out_path, "w") as out:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                             cwd=cwd if cwd and os.path.isdir(cwd) else None, env=env, start_new_session=True)
    why = None
    try:
        while p.poll() is None:
            if time.monotonic() - t0 > timeout_s:
                why = "timeout"
                break
            why = guard() if guard else None
            if why:
                break
            time.sleep(0.2)
    finally:   # also on a stop (KeyboardInterrupt): SIGTERM, then SIGKILL, because a modal dialog ignores SIGTERM
        old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})   # a second Ctrl-C must not leave it running
        try:
            for sig, wait in ((signal.SIGTERM, term_grace_s), (signal.SIGKILL, 10)):
                if p.poll() is not None:
                    break
                try:
                    os.killpg(p.pid, sig)
                    p.wait(wait)
                except ProcessLookupError:
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old)
    return p.returncode, time.monotonic() - t0, why


def read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""
