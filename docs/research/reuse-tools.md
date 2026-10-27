# Reusing standard tools: mprime, systemd-run, gpu-burn

The facts the code relies on, with their sources. The design decisions built on them are in [design.md](../design.md).

## mprime (Prime95 for Linux), the per-core Curve Optimizer load
- **Version.** 30.19 build 20, `p95v3019b20.linux64.tar.gz` from https://www.mersenne.org/download/ (served by download.mersenne.ca). SHA256 is 4ce2377e03deb4cf189523136e26401ba08f67857a128e420dd030d00cdca601, and the published MD5 and SHA1 also match. The source is `p95v3019b21.source.zip` from the same page. Its `linux64/prime.c` is identical to `linux/prime.c`.
- **Licence.** The EULA allows use on machines you own and grants no right to redistribute. The archive goes into the gitignored `tools/mprime/` and is never committed.
- **Settings follow CoreCycler**, with the same keys written independently because CoreCycler is CC BY-NC-SA 4.0. The source is sp00n/corecycler a95b523, `script-corecycler.ps1`:
  - FFT presets: L7316-7361.
  - TortureWeak masks: L6135-6194 (SSE 1097728, AVX2 1048576).
  - prime.txt keys: L7530-7639.
  - The default is SSE with Huge FFTs, 8960-32768K (`configs/default.config.ini` L247, L265). It makes the least heat and so the highest boost, which catches edges that AVX loads miss. CoreCycler's readme asks for both kinds of load eventually.
- **Affinity: `EnableSetAffinity=0` is required.** Without it, mprime binds its torture worker to the whole cpuset of hwloc core 0 and ignores the taskset mask (`commonb.c` L414-423, L601-633). hwloc does not shrink the topology for a taskset mask.
  - Checked live: with `EnableSetAffinity=0` and `taskset -c 2`, all four mprime threads had `Cpus_allowed_list: 2`.
- **Command line.**
  - `-t` runs the torture test, with no PrimeNet connection and no renice.
  - `-W<dir>` must be absolute and joined to the flag, because mprime first changes into its own executable's directory (`prime.c` L191-200, L306-309).
  - `TortureHyperthreading=0` gives one compute thread per worker.
  - `TortureTime=1` runs one test per FFT length, so each length passes once per cycle (`commonb.c` L8066-8133).
- **Output.**
  - A pass is `Self-test 17920K passed!`, written to results.txt and stdout.
  - Failure strings come from `commonb.c` L7194-7200, L7695-7765 and `commonc.c` L2280-2286: `FATAL ERROR`, `Hardware failure detected running NK FFT size`, `ERROR: ILLEGAL SUMOUT`, `Possible hardware failure` (a warning; the test restarts), `Maximum number of warnings exceeded`, and `TORTURE TEST FAILED on worker #N` (stdout only).
  - The summary is `Torture Test completed N tests in M minutes - E errors, W warnings.`
  - SSE2 FFT descriptions carry no ISA prefix; AVX2 ones say `FMA3`.
  - mprime exits 0 even after a failure.
  - Checked live: SIGTERM stops it cleanly with the summary line. SIGSTOP/SIGCONT suspend and resume it. One SSE Huge test takes about 30 s on a 5800X3D.
- **Not measured yet.** How long one full SSE Huge cycle takes on a 5800X3D. CoreCycler's 13-19 min is from a 5900X, and the 96 MiB L3 makes the smaller Huge lengths partly cache-resident.

## nvidia_oc, the boot-time GPU applier
- **Source.** github.com/Dreaming-Codes/nvidia_oc, tag 0.1.24 (commit 54f59c3, 2026-01-23), MIT, 226 stars. One file, `src/main.rs`, on nvml-wrapper 0.11.
- **What it calls.** `set` makes the same NVML calls as ctune's `apply.py`:
  - `set_gpc_clock_vf_offset` and `set_mem_clock_vf_offset` (the offsets);
  - `set_power_management_limit` (in milliwatts);
  - `set_gpu_locked_clocks` (min and max).
  - It has no range checks of its own; NVML refuses values out of range.
  - It panics on the first failing setter, so a failure can leave the earlier settings applied. `boot_apply.py` reads back after every run.
- **Why not nvidia-smi.** `nvidia-smi` 615.78 has `-pl` and `-lgc`, but no clock offsets and no query field for them. `nvidia_oc get` reads them back.
- **Build.** No signed or attested release binary; the release workflow builds `master` with `cargo build --release` on ubuntu-latest. ctune builds the pinned commit with `cargo build --release --locked` in `docker.io/library/rust:1-slim-bookworm` (`ctune/gpu/nvidia_oc.Containerfile`). The digest and rustc version go in `tools/nvidia_oc/VERSION`.
  - Two builds gave the same sha256, bda6f0eb…, with rustc 1.99.1. It links only glibc and libgcc_s, and loads `libnvidia-ml.so.1` at run time.
- **Checked live** on an RTX 3080 Ti. `nvidia_oc get --index 0` without root read the offsets at 0 and the power limit at 400 W. As root, through the installed copy, a no-op `set` read back unchanged.
- **Root.** It escalates itself through sudo, doas or pkexec when it does not run as root. The boot unit runs it as root, so that path is never taken.

## systemd-run --user, the detached runner
Settings checked against the systemd 259.9 man pages and probe units on Bazzite (Fedora 44):
- **Unit type.** A transient service lands in `app.slice` under `user@UID.service`, outside the calling agent's scope (`/proc/self/cgroup`), so stopping the agent's scope does not kill the test.
  - `--service-type=exec` fails at start when the binary cannot run.
  - `--expand-environment=no` stops systemd from expanding `${...}` and `$$` in the JSON spec.
  - `python3 -I -u`, because `-I` ignores PYTHONUNBUFFERED.
- **Stopping.** The default stop sends SIGTERM, which kills Python without running `finally`. `KillMode=mixed` with `KillSignal=SIGINT` raises KeyboardInterrupt, so `finally` runs, and then systemd SIGKILLs the rest of the cgroup.
  - A host can lower the stop timeout (for example `DefaultTimeoutStopSec=15s` in `/etc/systemd/user.conf.d/`), and Fedora sets `TimeoutStopFailureMode=abort` (`/usr/lib/systemd/user/service.d/10-timeout-abort.conf`). ctune sets 90 s with `kill`.
- **OOM.** The default `OOMPolicy=stop` stopped the whole unit when the kernel OOM-killed one child, so ctune sets `continue`.
  - systemd-oomd watches `app.slice` and `user@UID.service` at 80 % memory pressure for 20 s, so it can kill a unit under heavy pressure. That is one more reason the RAM tests size themselves below MemAvailable.
- **Retention.** Without `--collect`, a failed unit stays loaded with Result, ExecMainCode and ExecMainStatus. A clean one unloads at once. Transient units live in `/run/user/UID` and vanish at reboot, so the fsync'd crash marker stays the record of a crash.
- **Durability.** journald syncs non-critical messages only every 5 min, so the marker also fsyncs its directory after the rename.
- **Environment.** `/dev/nvidia*` and `/dev/dri/renderD128` are mode 0666 on Bazzite, so GPU and Vulkan tests work from a unit without a desktop session. With lingering on, the user manager runs from boot.

## gpu-burn, the GPU core test that checks results
- **Source.** github.com/wilicc/gpu-burn at commit 0d19c94 (2026-10-02), BSD-2-Clause. There is no upstream binary or image. ctune builds `ctune/gpu/gpu-burn.Containerfile` with podman: upstream's stages with fully qualified images, because podman's `short-name-mode` is enforcing on Fedora, and with `make COMPUTE= NVCCFLAGS='-gencode=arch=compute_86,code=sm_86'`, the maintainer's fix for PTX JIT failures (issue #145).
- **How it checks.** cuBLAS SGEMM (DGEMM with `-d`, tensor cores with `-tc`) runs on the same seeded 8192x8192 matrices into many VRAM slots, and a compare kernel counts elements that differ from slot 0 (`gpu_burn-drv.cpp` L193-236, `compare.cu`). cuBLAS gives bitwise-identical results run to run (cuBLAS docs §2.1.4), so a mismatch is a fault.
  - Upstream compares with tolerances: EPSILON 0.001f for FP32, below one ULP at these values (about 2e5), and EPSILOND 1e-7 for FP64, which leaves the low ~12 bits of each result unchecked. Upstream's own comment in `compare.cu` (L30-31) says 0 is fine, so ctune's build sets both to 0.
  - A run with no progress line has compared nothing. A FP64 batch takes about 30 s at 8 GB, so FP64 runs need at least 90 s.
  - The container runs in podman's own scope, outside the ctune unit's cgroup. `--timeout` caps it if ctune dies.
- **Pitfalls.**
  - With fewer than 2 slots nothing is compared: `-m` must be at least 1024 MB for FP32 or 2048 MB for FP64.
  - It always exits 0 (issue #12), so ctune reads the `GPU 0: OK/FAULTY` line. Counters reset every 10 %, but FAULTY stays.
  - In a container it is PID 1 and ignores signals without `--init`.
  - The time argument must come last.
- **Image.** CUDA 13.4.3, matching the R615 driver (615.78.08, CUDA 13.4); a newer toolkit than the driver supports fails to load. The image is 3.33 GB and shares its layers with `docker.io/nvidia/cuda:13.4.3-runtime-ubi9`. It runs rootless with `--device nvidia.com/gpu=all --security-opt label=disable`.
  - Checked live: `gpu_burn -l` lists the RTX 3080 Ti. A run with 1.7 GB of VRAM free failed at allocation with `out of memory`, which ctune reports as inconclusive.
- **Alternatives considered.**
  - OCCT for Linux checks for errors, but only the paid Enterprise CLI can be scripted.
  - DCGM stress tests are not available on GeForce.
  - cuda_memtest and memtest_vulkan test VRAM only.
  - Unigine and FurMark do not check results.
  - Games, ray tracing especially, remain the final check, because gpu-burn never touches the RT cores, ROPs or TMUs.
- **Example measurements** on an RTX 3080 Ti at its 400 W limit:
  - FP32 never reaches the lock: stock 1682 MHz at 397 W, `at_lock_share` 0 to 0.03 at a 1905 MHz lock.
  - FP64 (1/64 rate on GA102) holds the lock below the cap: 1905 MHz at 153 W, `at_lock_share` 1.0.
  - Tensor cores (`-tc`: FP32 GEMM on FP16 tensor cores, cuBLAS `CUBLAS_TENSOR_OP_MATH`) also sit at the cap: 62.6 TFLOPS at 1772 MHz and 380 W, with a 1905 lock and +70.
- **Pausing for load transients.** The container is frozen with `podman pause` (the cgroup freezer). Rootless it takes about 25 ms, and a signal sent while frozen arrives right after the thaw. A worker keeps two batches in flight (`gpu_burn-drv.cpp` L353-367), so the GPU idles only after both finish. At 8 GiB an FP32 batch takes about 1.2 s, so the transient run uses 1 GiB: two compared slots, about 0.1 s per batch. Measured at 5 Hz: in each 1 s pause the GPU fell to 2 % utilisation and about 120 W instantaneous with the clock at the lock, and each thaw stepped back to about 400 W at that clock.
- **Name clashes.** gpu-burn runs without `--replace`, so a run from another data directory cannot replace a running container. A run refuses to start beside a running or paused `ctune-gpu-burn`, removes a stopped one left by a crash, and every later podman call names the run's container ID (`--cidfile`).
- Tune the core with the memory offset at 0, because gpu-burn's result buffers sit in VRAM.
