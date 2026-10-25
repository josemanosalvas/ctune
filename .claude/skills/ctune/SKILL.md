---
name: ctune
description: Hardware tuning campaigns with ctune. Use when planning or running a RAM, GPU or CPU tuning step (timings, FCLK, undervolt, clock or memory offsets, power limits), verifying what was applied, comparing before and after a BIOS update, or diagnosing instability.
---

# ctune

You run a **campaign** in one **domain** (mem, gpu or cpu): a chain of single-variable **steps**, each applied, verified, tested and recorded in the journal. The tools are the `ctune` MCP server, or `bin/ctune <command>` from the repo root. Read the domain's reference before planning:
- mem: [RAM and fabric rules](reference/mem-zen3-ddr4.md), [test protocol](reference/mem-test-protocol.md)
- gpu: [NVIDIA undervolt and overclock](reference/gpu-nvidia.md)
- all: [machine facts and history](reference/this-machine.md)

## A step

1. **Orient.** Read `journal_list` and the domain status (`mem_status` or `gpu_status`). Done when you can name the current **baseline** (the last green configuration and its result), the outcome of the last step, and what is applied right now.
2. **Plan one change.** Pick one variable or one coupled group from the domain reference. Write a `plan` journal entry (`data.domain` set) with the setting, the current value, the new value, the reason, and the rollback value. Done when the change is fully specified.
3. **Apply.**
   - mem: the user types the values in the BIOS and reboots. Give them the menu path from `mem_bios_search`.
   - gpu: call `gpu_apply`. The user approves a password prompt.
   Done when the change is in place, or the user reports a failed boot.
4. **Verify.**
   - mem: `mem_snapshot`, then `mem_compare` against the baseline snapshot.
   - gpu: `gpu_status`; the readback and `applied_by_ctune` show the new values.
   Done when every planned value is confirmed and every other difference is explained. A reverted or mismatched setting makes the step **red**.
5. **Test.**
   - mem: a warm **screen** from the test protocol, then `errors` after one poll interval (300 s).
   - gpu: `gpu_test` (screen).
   **Green** means no crash, zero corrected machine-check errors, no new Xid errors, and a score at least as good as the baseline's. **Red** means anything else, and you record the error source.
6. **Decide.** Write a `result` entry. Green becomes the new baseline. Red rolls back to the baseline value (`gpu_apply` or the BIOS) and records the limit. A campaign ends with a multi-hour **sign-off** of its final configuration under the user's real workload.

## Guardrails

These are ceilings for every step:
- mem: VDIMM 1.60 V measured at most (1.55 V preferred for daily B-die), VSOC 1.20 V measured at most, VDDG at least 0.04 V below VSOC, VDDP at least 0.1 V below VDIMM and 1.05 V at most.
- gpu: power limit at or below the default unless the user asks for more, core temperature below 83 °C, and the caps `gpu_apply` enforces. After an Xid 79 (GPU fell off the bus), the next step is a reboot.
- One variable group per step, one domain per campaign. A crash during a gpu step clears the runtime settings on reboot; record it as red.
