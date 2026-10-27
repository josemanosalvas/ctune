# NVIDIA undervolt and overclock (Ampere, Linux, NVML)

ctune sets everything through NVML at runtime. Settings last until reboot or driver reload, so a crash undoes them.

## The undervolt
NVML exposes no voltage control. The undervolt is a **locked max clock plus a positive core offset**:
- `max_clock` caps the core clock (the top of the V/F curve).
- `core_offset` shifts the whole V/F curve up, so the capped clock is reached at a lower voltage point.
- The result runs the same or higher clocks at lower voltage, so it draws less power and stays under the power limit more often.

Search, in this order:
1. Choose a target clock. On a 3080 Ti, 1800 to 1900 MHz is typical. Lock it with `max_clock` and keep `core_offset` 0 for a reference test.
2. Raise `core_offset` in steps of +15 MHz (Ampere clock bins), testing each step. Start around +100 if the reference test is green.
3. At the first red step, go back two steps (−30 MHz) for margin, and test that value again.
4. Compare the green candidates by FP32 score and gflops_per_w, and by FP64's `power_w_avg`, `temp_c_end` and `fan_pct_end` (heat and noise, the goal of the undervolt).

## Memory overclock
- Raise `mem_offset` (NVML units, half of what nvidia-settings shows) in steps of +125, starting at +250.
- GDDR6X corrects errors by retrying, so an unstable offset first shows up as **lower bandwidth or score**, before memtest_vulkan reports errors. Keep the offset where `checked_gbps_avg` peaks, not where errors start.
- Tune memory after the core, one at a time.

## Reading results
- `limiter_share.power cap` near 1 means the power limit sets the clock. An undervolt lowers it.
- Xid errors: 13 (graphics exception), 31 (memory fault), 43 (GPU stopped), 45, 61/62 (microcontroller) and 109 (context-switch timeout) mark a red step. 79 (fell off the bus) needs a reboot.
- **The screen** (`gpu_test` screen, about 36 min) runs these parts in order. Each part has its own telemetry, with `at_lock_share` once a `max_clock` is applied this boot:
  - `fp32` (600 s): gpu-burn SGEMM, results compared bitwise, so an undervolt that computes wrong without crashing turns the step red. It sits at the 400 W cap in every step (stock: 1682 MHz, `at_lock_share` 0), so an undervolt shows there as more MHz at the same heat. Its median is the score (`score_gflops`).
  - `fp64` (600 s): the only load at the lock below the cap (1905 MHz at 153 W, `at_lock_share` 1.0). It tests the top V/F point, and it is where an undervolt shows as **less heat and noise**: compare `power_w_avg`, `temp_c_end` and `fan_pct_end` between steps. `*_end` averages the part's last 60 s, once the cooler has caught up.
  - `tc` (120 s): tensor cores, results compared. Also at the cap.
  - `transient` (180 s): FP32 with the container frozen 1 s in every 10, as `cpu_test` suspends mprime. In each pause the GPU idles (about 120 W) and boosts to the lock, and each thaw steps the load straight back to full at that clock, where undervolts tend to crash. `transient.pauses` counts the cycles; a run that never paused is inconclusive. `telemetry.paused.core_mhz_avg` is the clock the load stepped back on at.
  - `graphics` (300 s): Quake II RTX path tracing on the RT cores, headless in gamescope at 2560×1440. It is the closest to a game at the lock: 1896 MHz at 381 W, `at_lock_share` 0.9. It still touches the power cap, so like FP32 it shows an undervolt as clock rather than heat. Nothing it renders is checked, so it is red only on `Device lost!`, a crash, a hang or an Xid. `fps_median` is for information.
  - `vram` (300 s): memtest_vulkan.
- **`clock_stretch`** compares GFLOPS per MHz of `fp32`, `fp64` and `tc` with the last green result at the same `max_clock`. A part in `stretched` did more than 1 % less work per MHz: the GPU is stretching its clock for lack of voltage. That counts as a lower score, so the step is red even without an error: the test's own verdict stays green, so write red in the step's `result` entry. Step 3 (+70 at 1905 MHz, 2026-10-27) had no error, yet FP64 fell 4.2 %. A result with a stretched part is never used as the next baseline.
- Before the first undervolt step, record a stock baseline with `gpu_test`. Old vkpeak scores do not compare with gpu-burn's `score_gflops`, and results before 2026-10-27 have no fan fields: re-run the baseline before comparing heat and noise.
- Tune the core with `mem_offset` at 0: gpu-burn's results sit in VRAM, so a memory overclock blurs where an error came from.
- Verdict `inconclusive` means a part could not run, and `blocked_by` says why. Fix it and run the test again; do not roll back:
  - Another app holds VRAM: ask the user to close it.
  - Another ctune test holds the GPU: wait for it to end.
  - Quake II RTX is missing: run `ctune gpu q2rtx-setup` once.
- The graphics part covers the RT cores and the texture units, not the ROPs, and checks nothing it draws. Games stay the last check, so after `signoff` the user plays their own games for a few hours.

## Persisting (after the sign-off)
- `gpu_persist` writes the settings applied now to `/etc/ctune/gpu.json`. A root-owned boot unit re-applies them at boot and after resume with nvidia_oc, a pinned build in `/usr/local/libexec/ctune`.
- It refuses unless the latest sign-off with exactly these settings and a full gpu phase (90 min, Quake II RTX included), on this GPU and driver, was green. Use `dry_run` to see the reasons.
- The unit sets the power limit and the clock lock first, then the offsets, and reads them back. On any failure the GPU goes back to stock.
- The unit skips the settings in four cases, and says why in `gpu_status` → `persistence.this_boot`:
  - **A new driver.** Apply the settings with `gpu_apply`, run `signoff`, then `gpu_persist` again.
  - **A value over ctune's caps.**
  - **The crash-loop guard.** In two boots in a row the GPU failed after the apply: a crash within 10 minutes, or an Xid error. Find the cause, then clear it with `gpu_persist` `reset_guard`.
  - **Resume after a boot that did not apply them.**
- **Escape at the boot menu:** add `ctune.gpu=off` to the kernel command line.
- `gpu_unpersist` stops it; the settings applied now stay until reboot. `gpu_reset` and `gpu_apply` still change the running values on top of the persisted ones.
- During later RAM or CPU campaigns the persisted GPU settings stay active. A crash there is not a GPU result.

## Power limit
Keep the default unless the user wants an efficiency target. Lowering it (for example to 320 W) after a good undervolt trades a few percent of performance for noise and heat.
