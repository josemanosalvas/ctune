---
name: memtune
description: RAM tuning on this AMD Ryzen machine with the memtune tools. Use for memory overclocking steps (timings, FCLK, DRAM/SoC voltages, ProcODT/RTT), checking what the BIOS actually applied, before/after BIOS-update comparisons, and diagnosing memory or fabric instability.
---

# memtune

You run a **tuning campaign**: a chain of single-variable **steps**, each verified and tested, recorded in the journal. The user changes BIOS settings; you plan, verify, test, and decide. memtune only reads hardware; it never writes settings.

Tools: the `memtune` MCP server (`status`, `snapshot`, `compare`, `bios_search`, `sensors`, `errors`, `journal_*`), or the same commands via `bin/memtune <cmd>` in this repo.

## A tuning step

1. **Orient.** Call `status` and `journal_list`. Done when you can state the current **baseline** (last green config) and the outcome of the last step.
2. **Plan one change.** Pick a single variable or a coupled group (for example tRAS+tRC, or tWR+tRTP) using [rules](reference/zen3-ddr4-rules.md) and the [machine facts](reference/this-machine.md). Find the exact menu path with `bios_search`. Write a `plan` journal entry with menu path, from, to, why, and the rollback value. Done when the user has the exact values to type.
3. **User applies and boots.** Wait for them.
4. **Verify.** Take a `snapshot` (the user approves a password prompt), then `compare` it against the baseline snapshot. Done when every planned value appears in the decoded timings/BIOS diff and nothing else changed. If the board reverted to safe settings or trained differently, that step is **red** (failed training).
5. **Test.** Screen with a warm 20–30 min load ([test protocol](reference/test-protocol.md)). Wait one poll interval (300 s), then call `errors`. **Green** = no crashes and zero corrected errors. **Red** = any error: record the bank and subsystem.
6. **Decide.** Write a `result` entry. Green becomes the new baseline. Red rolls back to the baseline value, records the limit, and moves to the next variable. A campaign ends with a multi-hour sign-off of the final config.

## Guardrails

Hold every step inside these limits; they are hard ceilings, not targets:
- VDIMM ≤ 1.60 V measured (≤ 1.55 V preferred for B-die daily). VSOC ≤ 1.20 V measured.
- VDDG ≤ VSOC − 0.04 V. VDDP ≤ VDIMM − 0.1 V and ≤ 1.05 V.
- One step changes one variable group. CPU Curve Optimizer is its own campaign, never mixed with memory steps.
- If no snapshot exists since the last BIOS change, take one before planning.
