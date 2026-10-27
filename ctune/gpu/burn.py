"""gpu-burn: the GPU core test that checks its results, run in a rootless podman container on the NVIDIA CDI device.

gpu-burn (github.com/wilicc/gpu-burn) multiplies the same pair of seeded random 8192x8192 matrices with cuBLAS into
many VRAM slots and compares every slot with the first, element by element. cuBLAS gives bitwise-identical results
on every run (cuBLAS docs, "Results reproducibility"), so any mismatch is a hardware fault, not rounding. ctune's
build sets both compare tolerances to 0 (gpu-burn.Containerfile), so FP32, FP64 and tensor-core checks are bitwise.
vkpeak only measures, and an unstable undervolt can pass it with silent compute errors.

Facts from the gpu-burn source at the pinned commit (research notes in docs/research/reuse-tools.md):
- It always exits 0, OK or FAULTY (issue #12), so the verdict comes from its output only.
- Fewer than 2 result slots means nothing is compared: -m must be at least 1024 MB (FP32) or 2048 MB (FP64).
- The printed error counters reset every 10 %; the final "GPU 0: OK/FAULTY" line keeps any fault.
- Inside the container gpu_burn is PID 1 and ignores signals without --init (pid_namespaces(7)).
- A worker keeps two batches in flight (gpu_burn-drv.cpp L353-367): a frozen host process stops the GPU only after
  both finish. At 8 GiB a FP32 batch is 30 products, about 1.2 s, so the transient run uses TRANSIENT_MB.
"""
import os
import re
import statistics
import subprocess
import threading
import time

from ctune.core.paths import REPO, TOOLS

COMMIT = "0d19c94dcb2e9b6858b7586a4c327674cd67113b"
CUDA = "13.4.3"   # matches driver branch R615 (CUDA 13.4); a newer toolkit than the driver supports fails to load
IMAGE = f"localhost/gpu-burn:{COMMIT[:7]}-bitwise-cuda{CUDA}"
SRC = os.path.join(TOOLS, "gpu-burn", "src")
CONTAINERFILE = os.path.join(REPO, "ctune", "gpu", "gpu-burn.Containerfile")
NAME = "ctune-gpu-burn"
MIN_MB = {"fp32": 1024, "tc": 1024, "fp64": 2048, "transient": 1024}
# FP64 batches take about 30 s at 8 GB: shorter runs compare nothing. A transient run needs a few pause cycles.
MIN_SECONDS = {"fp32": 30, "tc": 30, "fp64": 90, "transient": 60}
FLAGS = {"fp32": [], "tc": ["-tc"], "fp64": ["-d"], "transient": []}
TRANSIENT_MB = 1024     # two compared slots, so a batch takes about 0.1 s and a 1 s pause really idles the GPU
RUN_S, PAUSE_S = 9, 1   # cpu_test's pattern (CoreCycler's suspendPeriodically): 9 s of load, then 1 s frozen
BUSY = f"another gpu-burn container ({NAME}) exists: another ctune test is using the GPU"


def image_present():
    return subprocess.run(["podman", "image", "exists", IMAGE], capture_output=True).returncode == 0


def setup():
    """Clone the pinned commit, build the image, drop the build stage (about 4 GB), write tools/gpu-burn/VERSION."""
    if not os.path.isdir(os.path.join(SRC, ".git")):
        subprocess.run(["git", "clone", "-q", "https://github.com/wilicc/gpu-burn", SRC], check=True)
    subprocess.run(["git", "-C", SRC, "fetch", "-q", "origin"], check=False)
    subprocess.run(["git", "-C", SRC, "checkout", "-q", "--detach", COMMIT], check=True)
    r = subprocess.run(["podman", "build", "--pull=missing", "--build-arg", f"CUDA_VERSION={CUDA}", "-f", CONTAINERFILE,
                        "-t", IMAGE, SRC], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"podman build failed ({r.returncode}): {(r.stdout + r.stderr)[-1500:]}")
    subprocess.run(["podman", "rmi", f"docker.io/nvidia/cuda:{CUDA}-devel-ubi9"], capture_output=True)
    subprocess.run(["podman", "image", "prune", "-f"], capture_output=True)
    digest = subprocess.run(["podman", "image", "inspect", "--format", "{{.Digest}}",
                             f"docker.io/nvidia/cuda:{CUDA}-runtime-ubi9"], capture_output=True, text=True).stdout.strip()
    with open(os.path.join(TOOLS, "gpu-burn", "VERSION"), "w") as f:
        f.write(f"gpu-burn {COMMIT} (github.com/wilicc/gpu-burn, BSD-2-Clause), compare tolerances patched to 0 "
                f"(bitwise), image {IMAGE}, built with {os.path.relpath(CONTAINERFILE, REPO)} on "
                f"docker.io/nvidia/cuda:{CUDA}-devel-ubi9; runtime base {digest}\n")
    return {"image": IMAGE, "runtime_base": digest}


def cmd(mode, seconds, mb, cidfile):
    """podman run for one gpu-burn run. Options go before the time, which must be last."""
    # No --replace, and every later podman call names this run's container ID (cidfile), never NAME: on 2026-10-27
    # a ctune run from another data directory replaced the campaign's running container and turned its step red.
    # --timeout: conmon stops the container even if ctune dies (it runs outside the test's cgroup).
    return ["podman", "run", "--rm", "--init", f"--cidfile={cidfile}", f"--timeout={seconds + 120}", "--name", NAME,
            "--pull=never", "--network=none",
            "--device", "nvidia.com/gpu=all", "--security-opt", "label=disable", IMAGE,
            "./gpu_burn", "-i", "0", *FLAGS[mode], "-stts", "10", "-m", str(mb), str(seconds)]


def busy():
    """None if a gpu-burn run can start, else why not. A stopped container of this name (left by a crash) is removed;
    a running or paused one belongs to another ctune test, so it is left alone (its --timeout ends it)."""
    try:
        r = subprocess.run(["podman", "container", "inspect", "--format", "{{.State.Status}}", NAME],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return f"podman could not be asked whether another gpu-burn runs: {e}"
    state = r.stdout.strip() if r.returncode == 0 else ""
    if state in ("running", "paused", "stopping"):
        return f"{BUSY}: it is {state}"
    if state:
        subprocess.run(["podman", "rm", "--ignore", NAME], capture_output=True, timeout=30)   # never --force
    return None


def cleanup(cidfile):
    """Remove this run's container (also when frozen), by its ID only, then the cidfile and its directory."""
    if os.path.exists(cidfile):
        subprocess.run(["podman", "rm", "-f", "--ignore", f"--cidfile={cidfile}"], capture_output=True, timeout=60)
    for rm, path in ((os.remove, cidfile), (os.rmdir, os.path.dirname(cidfile))):
        try:
            rm(path)
        except OSError:
            pass


def _podman(verb, cidfile):
    try:
        return subprocess.run(["podman", verb, f"--cidfile={cidfile}"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:   # a hung podman is a failed call, not a dead pause thread
        return subprocess.CompletedProcess([verb], 1, "", f"podman {verb} timed out or failed: {e}")


class Pauser(threading.Thread):
    """Load transients: freezes the gpu-burn container for pause_s in every run_s + pause_s, as cpu_test suspends
    mprime. In each pause the GPU drops to idle and boosts toward the top of its V/F curve; the thaw steps it back to
    full load at that clock, where undervolts tend to crash. `podman pause` is the cgroup freezer, so every process in
    the container stops at once; a stop signal sent while frozen lands after the thaw. mark("paused" | "load") tags
    the telemetry. It never freezes within tail_s of gpu-burn's own timer, so the final verdict line is printed."""

    def __init__(self, load, seconds, cidfile, run_s=RUN_S, pause_s=PAUSE_S, tail_s=15, mark=None):
        super().__init__(daemon=True)
        self.load, self.cidfile, self.run_s, self.pause_s = load, cidfile, run_s, pause_s
        self.mark = mark or (lambda state: None)
        self.end = time.monotonic() + seconds - tail_s
        self.stop, self.paused, self.pauses, self.failures = threading.Event(), False, 0, []
        self.stuck = False   # the container could not be thawed: the run says nothing about the GPU

    def _done(self):
        return self.stop.is_set() or self.load.stopped or not self.load.alive()

    def run(self):
        try:
            while not self.stop.wait(self.run_s):
                if self._done() or time.monotonic() + self.pause_s > self.end:
                    return
                r = _podman("pause", self.cidfile)
                if r.returncode:
                    self.failures.append(f"podman pause exit {r.returncode}: {r.stderr.strip()[-200:]}")
                    continue
                self.paused = True
                self.mark("paused")
                thaw = time.monotonic() + self.pause_s
                while time.monotonic() < thaw and not self.load.stopped and not self.stop.wait(0.02):
                    pass
                if not self._thaw():
                    return
                self.pauses += 1
        finally:
            if self.paused:
                self._thaw()

    def _thaw(self):
        for _ in range(3):
            r = _podman("unpause", self.cidfile)
            if r.returncode == 0 or not self.load.alive():
                self.paused = False
                self.mark("load")
                return True
        self.failures.append(f"podman unpause exit {r.returncode}: {r.stderr.strip()[-200:]}")
        self.stuck = True
        return False

    def summary(self):
        return {"pauses": self.pauses, "run_s": self.run_s, "pause_s": self.pause_s, "pause_errors": self.failures[:5]}


ITER = re.compile(r"Results are (\d+) bytes each, thus performing (\d+) iterations")
PROG = re.compile(r"^\s*([\d.]+)%\s+proc'd: (-?\d+) \(([\d.]+) Gflop/s\)\s+errors: (-?\d+)\s*(?:\((WARNING|DIED)!\))?")
FINAL = re.compile(r"^\s*GPU 0: (OK|FAULTY)\s*$")
FATAL = re.compile(r"Couldn't init|Failure during compute|No clients are alive|No CUDA devices|Syntax error|Low mem|"
                   r"couldn't find compare kernel|CUDA error|cuBLAS error")


def parse(rc, out, stopped):
    lines = [l for l in out.replace("\r", "\n").splitlines()]
    it = next((m for l in lines if (m := ITER.search(l))), None)
    prog = [m for l in lines if (m := PROG.search(l))]
    final = next((m.group(1) for l in lines if (m := FINAL.search(l))), None)
    fatal = [l.strip() for l in lines if FATAL.search(l) and not PROG.search(l)][:10]
    errs = list(fatal)
    iterations = int(it.group(2)) if it else None
    if iterations is not None and iterations < 2:
        errs.append(f"only {iterations} result slot: nothing was compared (raise -m)")
    if any(int(m.group(4)) != 0 for m in prog):
        errs.append(f"compute errors: {max(abs(int(m.group(4))) for m in prog)} in a 10 % window")
    if any(m.group(5) == "DIED" or int(m.group(2)) < 0 for m in prog):
        errs.append("a gpu-burn worker died")
    if not prog or int(prog[-1].group(2)) <= 0:
        errs.append("no batch was compared (the run was too short, or the worker hung)")
    if final != "OK":
        errs.append(f"final verdict {final or 'missing'}")
    no_image = rc == 125 and re.search(r"image not known|no such image|unable to find", out, re.I)
    name_taken = rc == 125 and re.search(r"name .* is already in use", out)
    if no_image:
        errs.append("the gpu-burn image is missing: run `ctune gpu burn-setup`")
    elif name_taken:
        errs.append(BUSY)
    elif rc in (125, 126, 127):
        errs.append(f"podman failed (exit {rc}): {' | '.join(l.strip() for l in lines[-3:])}")
    elif rc not in (0, None) and not stopped:
        errs.append(f"gpu-burn exit status {rc}")
    gf = [float(m.group(3)) for m in prog if float(m.group(1)) >= 20]   # after the first 20 %: heat soak
    # out of VRAM at start-up, no image or the GPU taken: the test never ran, which says nothing about stability
    unavailable = bool(no_image or name_taken) or any("out of memory" in l for l in fatal)
    return {"ok": not errs, "unavailable": unavailable, "iterations": iterations, "final": final, "errors": errs,
            "gflops_median": round(statistics.median(gf), 1) if gf else None, "progress_lines": len(prog),
            "finished_on_its_own": not stopped}
