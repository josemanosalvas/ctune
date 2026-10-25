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
- vkpeak FP32 is power-heavy and short. Games, ray tracing especially, are the strictest test: the sign-off is a few hours of the user's own games.

## Power limit
Keep the default unless the user wants an efficiency target. Lowering it (for example to 320 W) after a good undervolt trades a few percent of performance for noise and heat.
