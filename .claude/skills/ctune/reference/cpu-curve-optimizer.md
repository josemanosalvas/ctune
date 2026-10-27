# Curve Optimizer (Zen 3, runtime through the SMU)

Curve Optimizer (CO) shifts each core's voltage/frequency curve by a **margin**. A negative margin means less voltage at every clock, about 3 to 5 mV per count. When a probe or a readback does not match, or before you change the SMU code, read `docs/research/cpu-co-smu.md` for the command IDs, the encoding and the sources.

## Probe mismatch
`cpu_probe` compares the SMU's margins with the BIOS values in the latest mem snapshot. A mismatch has two causes:
- The snapshot is older than the BIOS settings, for example right after a flash. The user re-enters CO from the re-entry checklist, you take `mem_snapshot`, then run `cpu_probe` again.
- Something else changed a margin this boot. Ask the user what else writes to the SMU, or reboot.

## Search
1. **Screen the cores at -30 first**, each on its own at its current margin. All-core stress tests pass on margins that fail single-core. A red core goes to -25, then -27 or -28 to refine. Done when each of them is green at its final margin.
2. **Lower the cores with headroom** by 5 counts per step, the most `cpu_apply` allows, and screen after each. Done when each has a first red value.
3. **Refine** between the last green and the first red value in steps of 1 or 2. Done when every core has a green final margin at least 2 counts less negative than its first red value.
4. **Sign-off.** Run `signoff` with `minutes` {"cpu": 80}, so each core gets 10 minutes in random order. Done when it is green.
5. **Persist.** Runtime margins last until reboot. The user enters the final values in the BIOS, a sign and a magnitude per core, under the menu path that `mem_bios_search` "Curve Optimizer" returns. Then take `mem_snapshot` and run `cpu_probe`. Done when the probe matches the new values.
6. **Real use.** The user plays their own games and leaves the machine idle overnight for a few days. A crash moves the suspect core 2 counts toward 0 in the BIOS. Done when the user reports no crash.

## Testing
CO errors show up in single-thread boost and in load-to-idle transitions. All-core loads run lower clocks with more voltage headroom, so they miss most of them. `cpu_test` therefore runs Prime95 (mprime) on one thread per core, the way CoreCycler does, and suspends it for 1 s in every 10 so the core keeps dropping out of boost and coming back.
- **Workload.** Use `sse-huge` (the default) for every step: it makes the least heat, so the core boosts highest. Run `avx2-1344k` once on a core's final value, as the heavy-load check. `full_cycle` runs a core until every FFT length has passed once (up to 40 min); use it for the last check of a core before the sign-off. Judge both on errors and machine-check counts only: AVX2 boosts lower by design.
- **Time.** One core takes about 6 minutes and all eight about 50.

A step is **red** on any of these:
- An mprime error: `FATAL ERROR`, a hardware failure at an FFT size (`failed_size_k`), or a warning in the summary. Also an mprime that exits on its own, or threads that left the core (`affinity_ok` false).
- A corrected machine-check error, usually in the core or core/l3 subsystem. `mce_delta_by_cpu` names the logical CPU, and `cpu_status` maps it to a core. An error on an idle core during another core's test makes the idle core the suspect.
- A freeze or reboot. The next ctune call (or the boot-check unit, if installed) writes the red result: the core and phase that were running, `crashed_boot_error_lines` and `crashed_boot_kernel_tail` from the crashed boot, and `next_boot_hardware_lines` from the boot after it, including the `Previous system reset reason` line. Read those first, then run the record's `next_step` (`journalctl -k -b <crashed_boot_id>`). If `journal_lost` is true, the journal has already rotated; check `saved_boot_files`.

## Reading results
- Compare `voltage_v_avg` and `eff_ghz_avg` of the `p95` phase with a baseline of the same `workload` on the same core. The `suspended` phase is the 1 s pause, so it is not a score. A more negative margin gives lower voltage at the same clock and a lower `temp_c_max`. Record an mprime baseline before the first step.
- The pm_table averages clocks over its sample window. It shows sustained clocks, not the momentary single-core peak.
- mprime's `passed`, `passed_sizes` and `tests_completed` count work done, not speed; only a failure (`failed_size_k`, errors) counts against a core.

## Payoff on a 5800X3D
The X3D has no boost override, so the clock tops out near 4.55 GHz single-core and 4.45 GHz all-core at any margin. CO lowers voltage and temperature, so heavy loads hold their clocks longer before the temperature limit. Games gain about 0 to 2 %. Tell the user this when they weigh effort against gain.
