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
"""
import os
import re
import statistics
import subprocess

from ctune.core.paths import REPO, TOOLS

COMMIT = "0d19c94dcb2e9b6858b7586a4c327674cd67113b"
CUDA = "13.4.3"   # matches driver branch R615 (CUDA 13.4); a newer toolkit than the driver supports fails to load
IMAGE = f"localhost/gpu-burn:{COMMIT[:7]}-bitwise-cuda{CUDA}"
SRC = os.path.join(TOOLS, "gpu-burn", "src")
CONTAINERFILE = os.path.join(REPO, "ctune", "gpu", "gpu-burn.Containerfile")
NAME = "ctune-gpu-burn"
MIN_MB = {"fp32": 1024, "tc": 1024, "fp64": 2048}
MIN_SECONDS = {"fp32": 30, "tc": 30, "fp64": 90}   # FP64 batches take about 30 s at 8 GB: shorter runs compare nothing
FLAGS = {"fp32": [], "tc": ["-tc"], "fp64": ["-d"]}


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


def cmd(mode, seconds, mb):
    """podman run for one gpu-burn run. Options go before the time, which must be last."""
    # --replace: a container of this name can only be a stale orphan, because ctune runs one test at a time.
    # --timeout: conmon stops the container even if ctune dies (it runs outside the test's cgroup).
    return ["podman", "run", "--rm", "--init", "--replace", f"--timeout={seconds + 120}", "--name", NAME,
            "--pull=never", "--network=none",
            "--device", "nvidia.com/gpu=all", "--security-opt", "label=disable", IMAGE,
            "./gpu_burn", "-i", "0", *FLAGS[mode], "-stts", "10", "-m", str(mb), str(seconds)]


def cleanup():
    subprocess.run(["podman", "rm", "-f", NAME], capture_output=True)


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
    if no_image:
        errs.append("the gpu-burn image is missing: run `ctune gpu burn-setup`")
    elif rc in (125, 126, 127):
        errs.append(f"podman failed (exit {rc}): {' | '.join(l.strip() for l in lines[-3:])}")
    elif rc not in (0, None) and not stopped:
        errs.append(f"gpu-burn exit status {rc}")
    gf = [float(m.group(3)) for m in prog if float(m.group(1)) >= 20]   # after the first 20 %: heat soak
    # out of VRAM at start-up or no image: the test never ran, which says nothing about stability
    unavailable = bool(no_image) or any("out of memory" in l for l in fatal)
    return {"ok": not errs, "unavailable": unavailable, "iterations": iterations, "final": final, "errors": errs,
            "gflops_median": round(statistics.median(gf), 1) if gf else None, "progress_lines": len(prog),
            "finished_on_its_own": not stopped}
