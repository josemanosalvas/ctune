---
name: ctune-cpu
description: Curve Optimizer tuning with ctune on Zen 3. Use when planning, applying, testing or signing off a per-core margin, when entering the final margins in the BIOS, or when a core logs machine-check errors or the machine crashes at light load or idle.
---

# Curve Optimizer campaign

Read [the campaign frame](../../ctune/campaign.md) before the first step: journal, verdicts, long tests, sign-off and crash handling are defined there.

Curve Optimizer (CO) shifts each core's voltage-frequency curve by a **margin**: a negative margin means less voltage at every clock, about 3 to 5 mV per count. ctune sets margins at runtime through the SMU, and they last until reboot. For the SMU command IDs, the encoding and the sources, read `docs/research/cpu-co-smu.md` when a probe or readback does not match, or before you change the SMU code.

## Sequence
1. **Screen at −30.** Each core on its own at its current margin; all-core loads pass margins that fail single-core. A red core goes to −25, then −27 or −28. Done when every core is green at a margin.
2. **Lower** the cores with headroom by 5 counts per step. Done when each has a first red value.
3. **Refine** between the last green and the first red value in steps of 1 or 2. Done when every core's final margin is green and at least 2 counts less negative than its first red value.
4. **Sign-off.** `signoff` with `minutes` {"cpu": 80}, so each core gets 10 minutes, in random order. Done when it is green.
5. **Persist.** The user enters each final margin in the BIOS as a sign and a magnitude, under the path `mem_bios_search` "Curve Optimizer" returns. Then `mem_snapshot` and `cpu_probe`. Done when the probe matches the new values.
6. **Real use.** The user's own games and overnight idle for a few days. A crash moves the suspect core 2 counts toward 0 in the BIOS. Done when the user reports no crash.

## A step
1. **Orient.** `journal_list` and `cpu_status`. Done when you can name each core's baseline margin and result, and the margins running now.
2. **Plan.** One core, at most 5 counts from its current margin, in a `plan` entry. Record an mprime baseline of the same workload on that core before its first step.
3. **Apply.** `cpu_probe` once per boot, then `cpu_apply` for the core. Done when the readback equals the planned margin.
4. **Verify.** `cpu_status` shows the margin with `co_source` ctune.
5. **Test.** `cpu_test` with `cores` set to the changed core. Done when the verdict is decided.
6. **Decide.** A `result` entry. Red: `cpu_apply` back to the baseline margin.

## Testing
CO errors show up in single-thread boost and in load-to-idle transitions; all-core loads run lower clocks with more voltage headroom and miss most of them. `cpu_test` runs mprime on one thread per core, the way CoreCycler does, and suspends it 1 s in every 10 so the core keeps dropping out of boost and coming back. One core takes about 6 minutes, all eight about 50.
- **`sse-huge`** (the default) for every step: the least heat, so the highest boost.
- **`avx2-1344k`** once on a core's final value, as the heavy-load check, and **`full_cycle`** (every FFT length once, up to 40 min) for the last check of a core before the sign-off. Judge both on errors and machine-check counts only, since AVX2 boosts lower by design.

## Verdict
- **Score:** `eff_ghz_avg` of the `p95` phase, against a baseline of the same `workload` on the same core. `voltage_v_avg` and `temp_c_max` fall as the margin deepens. The `suspended` phase is the 1 s pause, not a score. The pm_table averages over its sample window, so it shows sustained clocks rather than the single-core peak. mprime's `passed`, `passed_sizes` and `tests_completed` count work done; only a failure counts against a core.
- **Red:**
  - An mprime error: `FATAL ERROR`, a hardware failure at an FFT size (`failed_size_k`), a warning in the summary, an mprime that exits on its own, or threads that left the core (`affinity_ok` false).
  - A corrected machine-check error, usually in the core or core/l3 subsystem. `mce_delta_by_cpu` names the logical CPU and `cpu_status` maps it to a core. An error on an idle core during another core's test makes the idle core the suspect.
  - A freeze or reboot. The next ctune call, or the boot-check unit, writes the red result with the core and phase that were running, `crashed_boot_error_lines`, `crashed_boot_kernel_tail`, and `next_boot_hardware_lines` with the `Previous system reset reason` line.

## Probe mismatch
`cpu_probe` compares the SMU's margins with the BIOS values in the latest full mem snapshot. A mismatch has two causes:
- The snapshot is older than the BIOS settings, for example right after a flash. The user re-enters CO from the re-entry checklist, then `mem_snapshot` and `cpu_probe` again.
- Something else changed a margin this boot. Ask the user what else writes to the SMU, or reboot.

## Guardrails
Margin −30 to +30, at most 5 counts per step, one core per step; `cpu_apply` enforces all three.

## Payoff on a 5800X3D
The X3D has no boost override, so the clock tops out near 4.55 GHz single-core and 4.45 GHz all-core at any margin. CO lowers voltage and temperature, so heavy loads hold their clocks longer before the temperature limit. Games gain about 0 to 2 %. Tell the user this when they weigh effort against gain.
