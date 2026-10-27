---
name: ctune
description: Hardware tuning campaigns with ctune. Use when planning or running a RAM, GPU or CPU tuning step (timings, FCLK, Curve Optimizer, undervolt, clock or memory offsets, power limits), verifying what was applied, comparing before and after a BIOS update, or diagnosing instability.
---

# ctune

You run a **campaign** in one **domain** (mem, gpu or cpu): a chain of single-variable **steps**, each applied, verified, tested and recorded in the journal. The tools are the `ctune` MCP server, or `bin/ctune <command>` from the repo root. Read the domain's reference before planning:
- mem: [RAM and fabric rules](reference/mem-zen3-ddr4.md), [test protocol](reference/mem-test-protocol.md)
- cpu: [Curve Optimizer](reference/cpu-curve-optimizer.md)
- gpu: [NVIDIA undervolt and overclock](reference/gpu-nvidia.md)
- all: [machine facts and history](reference/this-machine.md)

## A step

1. **Orient.** Read `journal_list` and the domain status (`mem_status`, `cpu_status` or `gpu_status`). Done when you can name the current **baseline** (the last green configuration and its result), the outcome of the last step, and what is applied right now.
2. **Plan one change.** Pick one variable or one coupled group from the domain reference. Write a `plan` journal entry (`data.domain` set) with the setting, the current value, the new value, the reason, and the rollback value. Done when the change is fully specified.
3. **Apply.**
   - mem: the user types the values in the BIOS and reboots. Give them the menu path from `mem_bios_search`.
   - cpu: `cpu_probe` once per boot, then `cpu_apply` for one core.
   - gpu: call `gpu_apply`.
   A tool whose description says root opens a password prompt. Tell the user before each call. Done when the change is in place, or the user reports a failed boot.
4. **Verify.**
   - mem: `mem_snapshot`, then `mem_verify` (BIOS configuration against the running hardware) and `mem_compare` against the baseline snapshot. After a BIOS flash, `mem_checklist` on the last pre-flash snapshot lists what to re-enter. If the first new snapshot returns `bios_key_problems`, a key setting changed its menu location: fix `KEY` in `bios_settings_decode.py` before trusting its focused table.
   - cpu: the `cpu_apply` readback equals the planned margin, and `cpu_status` shows it with `co_source` ctune.
   - gpu: `gpu_status`; the readback and `applied_by_ctune` show the new values.
   Done when every planned value is confirmed and every other difference is explained. A reverted or mismatched setting makes the step **red**.
5. **Test.**
   - mem: `mem_test` (screen, or fabric for FCLK steps and after a BIOS flash), then `errors` after 300 s.
   - cpu: `cpu_test` with `cores` set to the changed core, then `errors` after 300 s.
   - gpu: `gpu_test` (screen, about 36 min). A part listed in `clock_stretch.stretched` counts as a lower score.
   **Green** means no crash, zero corrected machine-check errors, no new Xid or load errors, and a score at least as good as the baseline's, within about 1 %. The cpu score is `eff_ghz_avg` of the `p95` phase of the same core, compared only with a baseline result of the same `workload`. **Red** means anything else, and you record the error source. **Inconclusive** means the test could not run or was too short to judge (`blocked_by` or the result's `inconclusive` says why): fix that and run it again, with no rollback.
6. **Decide.** Write a `result` entry. Green becomes the new baseline. Red rolls back to the baseline value (`cpu_apply`, `gpu_apply` or the BIOS) and records the limit. A campaign ends with a **sign-off** of its final configuration: `signoff` (about 5 h 30 min, detached, poll `test_status`), then a few days of the user's real workload. After a green sign-off of a GPU campaign, `gpu_persist` re-applies its settings at every boot (root, password prompt). It refuses settings no green sign-off covers, so experiments never persist.

## Guardrails

These are ceilings for every step:
- mem: VDIMM 1.60 V measured at most (1.55 V preferred for daily B-die), VSOC 1.20 V measured at most, VDDG at least 0.04 V below VSOC, VDDP at least 0.1 V below VDIMM and 1.05 V at most.
- cpu: margin -30 to +30, at most 5 counts per step, one core per step (`cpu_apply` enforces all three).
- gpu: power limit at or below the default unless the user asks for more, core temperature below 83 °C, and the caps `gpu_apply` enforces. After an Xid 79 (GPU fell off the bus), the next step is a reboot.
- One variable group per step, one domain per campaign. A crash during a cpu or gpu step clears the runtime settings on reboot; record it as red. When the crash interrupts `cpu_test`, the next cpu call writes that red result.
