---
name: ctune-gpu
description: NVIDIA GPU tuning with ctune on Ampere under Linux. Use for an undervolt, memory offset or power limit step, boot persistence after a sign-off, or an Xid or GPU crash.
---

# GPU campaign

Read [the campaign frame](../../ctune/campaign.md) before the first step: journal, verdicts, long tests, sign-off and crash handling are defined there. [The screen](reference/screen.md) describes each part of `gpu_test` and how to read it; read it before judging a result. [Persistence](reference/persist.md) covers boot persistence after a green sign-off.

ctune sets everything through NVML at runtime; settings last until reboot or driver reload, so a crash undoes them. NVML exposes no voltage control, so the **undervolt** is a locked max clock plus a positive core offset: `max_clock` caps the top of the V/F curve, and `core_offset` shifts the curve up, so the capped clock runs at a lower voltage point. The card then holds the same or higher clocks at lower voltage, and stays under the power limit more often.

## Sequence
`mem_offset` stays 0 until the core is chosen: gpu-burn's results live in VRAM, so a memory overclock blurs where an error came from.
1. **Stock baseline.** `gpu_test` screen at stock. Done when it is green and has the fan fields (`fan_pct_end`); results without them do not compare.
2. **Reference.** A target clock (1800 to 1900 MHz is typical on a 3080 Ti) locked with `max_clock`, `core_offset` 0. Done when it is green; it is the baseline for the offsets.
3. **Offsets.** `core_offset` +100 first, then +30 per step while green. After the first red value, bisect between the highest green and the lowest red offset, in Ampere's 15 MHz bins. Done when they are one bin apart.
4. **Margin.** Two bins (−30 MHz) below the highest green offset, tested again. Done when it is green.
5. **Choose.** Compare the green candidates by FP32 score and `gflops_per_w`, and by FP64's `power_w_avg`, `temp_c_end` and `fan_pct_end`, since heat and noise are the goal of the undervolt. Done when one candidate goes to the sign-off.
6. **Memory offset** (optional). `mem_offset` in NVML units (half of what nvidia-settings shows), from +250 in steps of +125. GDDR6X retries corrected errors, so an unstable offset first shows as lower bandwidth or score, before memtest_vulkan reports errors. Keep the offset where `checked_gbps_avg` peaks.
7. **Power limit** (optional). Keep the default unless the user wants an efficiency target; below it (for example 320 W after a good undervolt) trades a few percent of performance for noise and heat.
8. **Sign-off**, then the user's own games for a few hours, then [persistence](reference/persist.md).

## A step
1. **Orient.** `journal_list` and `gpu_status`. Done when you can name the baseline and the settings running now.
2. **Plan.** One value from the sequence, in a `plan` entry.
3. **Apply.** `gpu_apply`.
4. **Verify.** `gpu_status`: the readback and `applied_by_ctune` show the planned values.
5. **Test.** `gpu_test` screen (about 44 min) on a quiet GPU. Done when the verdict is decided.
6. **Decide.** A `result` entry. Red: `gpu_apply` back to the baseline.

## Verdict
- **Score:** the FP32 median (`score_gflops`), with FP64's power, temperature and fan for the undervolt's goal.
- **Red:**
  - A load error, or an Xid: 13 (graphics exception), 31 (memory fault), 43 (GPU stopped), 45, 61 and 62 (microcontroller), 109 (context-switch timeout). After an Xid 79 (fell off the bus), the next step is a reboot.
  - A part listed in `clock_stretch.stretched`: the GPU stretched its clock for lack of voltage. The test's own verdict stays green, so write red in the step's `result` and keep the baseline. Confirm a quiet GPU first, since another program drawing on it gives the same signature.

## Guardrails
Power limit at or below the default unless the user asks for more, core temperature below 83 °C, and the caps `gpu_apply` enforces.
