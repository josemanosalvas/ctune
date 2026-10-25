# Curve Optimizer (Zen 3, runtime through the SMU)

Curve Optimizer (CO) shifts each core's voltage/frequency curve by a **margin**: negative means less voltage at every clock, about 3 to 5 mV per count. Zen 3 allows -30 to +30. ctune sets the margin at runtime through the SMU, one core at a time. A reboot or a crash restores the BIOS margins, so a failed step costs a reboot and nothing else. Sources, command IDs and encoding are in `docs/research/cpu-co-smu.md`.

## Before the first write in a boot
- `cpu_probe` reads every core's margin through the SMU and compares it with the BIOS values from the latest mem snapshot. Writes are refused until a probe in the same boot matches.
- A mismatch means the snapshot is stale (take `mem_snapshot`) or something else changed a margin. After a BIOS flash, the user re-enters CO from the re-entry checklist, then you take `mem_snapshot` and `cpu_probe`.

## Search
1. **Validate the cores at -30 first.** They sit at the floor, so they cannot go lower. An all-core stress test passes on margins that fail single-core, so screen each one on its own. A red core moves toward 0 (-25, then -27 or -28 to refine).
2. **Push the cores with headroom** in steps of -5 (`cpu_apply` allows at most 5 per step), one core per step, with a screen after each.
3. At the first red value, refine between it and the last green value in steps of 1 or 2. Keep the final margin at least 2 counts above (less negative than) the first red value.
4. **Sign-off.** Run every core in random order with `seconds_per_core` 600 or more. Then the user runs their own games and leaves the machine idle for a few hours, overnight if possible.
5. **Persist.** Runtime margins vanish at reboot, so the user enters the final values in the BIOS (`mem_bios_search` "Curve Optimizer" gives the menu path; each core has a sign and a magnitude). Then take `mem_snapshot` and run `cpu_probe` to confirm.

## Testing
CO errors show up in single-thread boost and in load-to-idle transitions. All-core loads run lower clocks with more voltage headroom, so they miss most of them. `cpu_test` pins one verified stress-ng thread to the core under test, cycles through integer, FP, vector, FMA and hash loads, then a bursty light load, with idle gaps in between. A step's screen is `cpu_test` with `cores` set to the changed core: about 6 minutes. All eight cores take about 50 minutes; tell the user before a long run.

A step is **red** on any of these:
- A stress-ng verification error or crash.
- A corrected machine-check error, usually in a core bank (load_store, insn_fetch, l2_cache, decode_unit, execution_unit, floating_point) or L3. `mce_delta_by_cpu` names the CPU: CPU N and N+8 are core N. An error on an idle core during another core's test points at the idle core.
- A freeze or reboot. The next cpu call records the cut-short test as red, with the core and phase that were running and this boot's "Hardware Error" lines. Read `journalctl -k -b -1` next.

## Reading results
- Compare `voltage_v_avg` and `eff_ghz_avg` per phase against the baseline at the same phase. A more negative margin shows as lower voltage at the same clock, and lower `temp_c_max`.
- The pm_table averages its clocks over its sample window, so it shows sustained clocks, not the instantaneous single-core peak.
- `bogo_ops_s` moves only when clocks move. Treat a few percent as noise.

## Payoff on a 5800X3D
The X3D has no boost override: the clock tops out near 4.55 GHz single-core and 4.45 GHz all-core, whatever the margin. CO lowers voltage and temperature, so heavy loads hold clocks longer before the temperature limit. Games gain about 0 to 2 %. Tell the user this when they weigh effort against gain.
