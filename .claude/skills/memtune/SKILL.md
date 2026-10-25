---
name: memtune
description: RAM tuning campaigns with memtune. Use when planning a timing, FCLK or memory-voltage step, verifying what the BIOS applied, comparing before and after a BIOS update, or diagnosing memory or fabric instability.
---

# memtune

You run a **campaign**: a chain of single-variable **steps**. Each step is planned, applied by the user in the BIOS, verified, tested, and recorded in the journal. memtune reads hardware. The user makes every BIOS change.

The tools are the `memtune` MCP server, or `bin/memtune <command>` from the repo root.

## A step

1. **Orient.** Call `status` and `journal_list`. Done when you can name the current **baseline** (the last green configuration and its snapshot), the outcome of the last step, and a snapshot taken since the last BIOS change. Take a `snapshot` if that last one is missing.
2. **Plan one change.** Pick one variable, or one coupled group such as tRAS with tRC, using the [rules](reference/zen3-ddr4-rules.md) and the [machine history](reference/this-machine.md). Look up the exact menu path with `bios_search`. Write a `plan` journal entry with the menu path, the current value, the new value, the reason, and the rollback value. Done when the user has every value to type.
3. **Apply.** The user enters the values and boots. Done when the user reports a boot, or a failed boot that the board recovered from.
4. **Verify.** Take a `snapshot`, then `compare` it with the baseline snapshot. Done when every planned value shows in the diff and every other difference is explained. A board that reverted to safe settings, or values that differ from the plan, make the step **red**.
5. **Test.** Run a warm **screen** from the [test protocol](reference/test-protocol.md). Wait one poll interval (300 s), then call `errors`. **Green** means no crash and zero corrected errors. **Red** means anything else, and you record the error bank and subsystem.
6. **Decide.** Write a `result` journal entry. A green step becomes the new baseline. A red step goes back to the baseline value and records the limit it found. A campaign ends with a multi-hour **sign-off** of its final configuration.

## Guardrails

These are ceilings for every step:
- VDIMM 1.60 V measured at most, 1.55 V preferred for daily B-die. VSOC 1.20 V measured at most.
- VDDG at least 0.04 V below VSOC. VDDP at least 0.1 V below VDIMM, and 1.05 V at most.
- One variable group per step. CPU Curve Optimizer gets its own campaign, run while the memory configuration is green.
