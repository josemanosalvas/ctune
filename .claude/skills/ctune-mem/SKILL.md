---
name: ctune-mem
description: RAM tuning with ctune on Zen 3 and DDR4. Use when planning, applying, testing or signing off a step on memory timings, FCLK or memory voltages, after a BIOS flash, or when a memory test fails, the machine crashes or files show corruption.
---

# RAM and fabric campaign

Read [the campaign frame](../../ctune/campaign.md) before the first step: journal, verdicts, long tests, sign-off and crash handling are defined there. Read [the rules](reference/rules.md) before planning: presets, timing formulas, voltages and error signatures. [The tests](reference/tests.md) cover what each RAM test does, TM5, and how to read a TM5 failure.

## Sequence
The integralfx DDR4 OC guide's order: frequency before tighter timings. A stage is done when its next step is red and rolled back.
1. Frequency and FCLK at 1:1:1, with GDM on and loose primaries. FCLK steps use the fabric test.
2. Secondaries: tRRDS, tRRDL and tFAW; then tWR and tRTP; then tRFC; then tWTRS and tWTRL; then tCWL.
3. Tertiaries: tRDRDSCL and tWRWRSCL, then tRDWR, tWRRD and the DD pair.
4. Primaries: tCL, then tRCD, then tRP.
5. tRAS, then tRC.
6. GDM off at 1T.
7. The final candidate: TM5 3 cycles, then `signoff`.

Every 2 or 3 green timing steps, run a **checkpoint**: `mem_test` tm5 with `cycles` 1 (about 30 min). A 25 min screen misses errors TM5 finds after half an hour, so the checkpoint limits the suspects to the steps since the last clean one.

## A step
1. **Orient.** `journal_list` and `mem_status`. Done when you can name the baseline snapshot and its result, the last step's outcome, and the settings running now.
2. **Plan.** One variable or coupled group from the sequence, checked against the rules and guardrails, in a `plan` entry. Done when the BIOS values and the rollback values are fully specified.
3. **Apply.** The user enters the values in the BIOS and reboots; give them the menu path from `mem_bios_search`. A failed POST needs a CMOS clear, so the baseline should sit in a BIOS profile slot. Done when the user reports a boot, or a failed one (red).
4. **Verify.** `mem_snapshot`, then `mem_verify` and `mem_compare` against the baseline snapshot. Done when every planned value is confirmed and every other difference is explained; a reverted or mismatched value makes the step red. After a BIOS flash, `mem_checklist` on the last snapshot before the flash lists what to re-enter, and a first snapshot with `bios_key_problems` means a key setting moved: fix `KEY` in `bios_settings_decode.py` before trusting its focused table.
5. **Test.** `mem_test` screen (25 min, with the GPU heating the case), or fabric for an FCLK step and after a BIOS flash; a checkpoint when one is due. Done when the verdict is decided.
6. **Decide.** A `result` entry. Green: the new baseline, and the user saves it to a BIOS profile slot. Red: the user restores the baseline profile.

## Verdict
- **Score:** stressapptest `mb_s`. Runs vary by about 0.3 GB/s.
- **Red:** stressapptest or TM5 errors; a new corrected machine-check error (`umc` points at the memory, `fabric` at FCLK, VDDG or VSOC); a VRAM or Xid error from the heat load; a failed POST; a crash; a btrfs checksum error.
- **A red checkpoint:** roll back the newest step since the last clean checkpoint and run the checkpoint again, until it is clean; the step rolled back last is red. One clean cycle is a checkpoint, not a pass.

## Guardrails
- VDIMM 1.60 V measured at most, and 1.55 V for daily B-die.
- VSOC 1.20 V measured at most.
- VDDG at least 0.04 V below VSOC.
- VDDP at least 0.1 V below VDIMM, and 1.05 V at most.
