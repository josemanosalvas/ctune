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
4. Compare the green candidates by FP32 score, average power, gflops_per_w and temperature.

## Memory overclock
- Raise `mem_offset` (NVML units, half of what nvidia-settings shows) in steps of +125, starting at +250.
- GDDR6X corrects errors by retrying, so an unstable offset first shows up as **lower bandwidth or score**, before memtest_vulkan reports errors. Keep the offset where `checked_gbps_avg` peaks, not where errors start.
- Tune memory after the core, one at a time.

## Reading results
- `limiter_share.power cap` near 1 means the power limit sets the clock. An undervolt lowers it.
- Xid errors: 13 (graphics exception), 31 (memory fault), 43 (GPU stopped), 45, 61/62 (microcontroller) mark a red step. 79 (fell off the bus) needs a reboot.
- `gpu_test` core runs gpu-burn: cuBLAS matrix products whose results are compared bitwise, so an undervolt that computes wrong without crashing turns the step red.
- FP32 draws a lot of power and is expected to sit at the power cap. FP64 draws little and is expected to sit at the locked clock, which tests the top V/F point. Both are expectations, not yet measured on this card. Check `core.fp64.telemetry.at_lock_share`: the top point counts as tested only when it is near 1. If it is low, `limiter_share` shows what held the clock down. `at_lock_share` appears only when a `max_clock` is applied this boot.
- Before the first undervolt step, record a stock baseline with `gpu_test`. Old vkpeak scores do not compare with gpu-burn's `score_gflops`.
- Tune the core with `mem_offset` at 0: gpu-burn's results sit in VRAM, so a memory overclock blurs where an error came from.
- Verdict `inconclusive` means the test could not run, usually because another app holds VRAM (`blocked_by` names it). Ask the user to close it and run the test again. Do not roll back.
- gpu-burn never touches the RT cores, ROPs or TMUs. Games, ray tracing especially, stay the strictest test, so after `signoff` the user plays their own games for a few hours.

## Persisting (after the sign-off)
- `gpu_persist` writes the settings applied now to `/etc/ctune/gpu.json`. A root-owned boot unit re-applies them at boot and after resume with nvidia_oc, a pinned build in `/usr/local/libexec/ctune`.
- It refuses unless the latest sign-off with exactly these settings and a full gpu phase, on this GPU and driver, was green. Use `dry_run` to see the reasons.
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
