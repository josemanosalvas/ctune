# RAM tests

`mem_test` runs each of these over the free RAM while memtest_vulkan heats the case, so the DIMMs warm up the way they do in a game.

- **Screen** (every step, 25 min). stressapptest -W. Its `mb_s` is the step's score.
- **Fabric** (FCLK steps, and the check after a BIOS flash). The screen with NVMe reads for 90 min, then 20 min idle, because random reboots at idle are the classic FCLK symptom.
- **TM5** (checkpoints with `cycles` 1, and the final pass with the profile's 3 cycles). TestMem5 with the anta777 Extreme1 profile under Proton; three cycles of it is the integralfx guide's minimum for a stable result. It runs as long as TM5 needs for the RAM it covers, and `minutes` only caps it. Install it once with `ctune mem tm5-setup`. Swap is off for TM5, so ask the user to close large apps first.
  - Inconclusive: the scope ran out of memory, or TM5 had not started testing after 5 min.
  - Red: TM5's log reports errors, or a TM5 message box stopped it mid-test (a worker that died); the result quotes the box.
- **memtest86+** needs a boot from USB, so ctune cannot run it. It adds coverage of the memory the running OS holds; offer it when the user wants a full-coverage pass.

## Reading a TM5 failure
TM5's log gives only the failing test's number and the time; it records no address, bit or worker.
- Look the test up in `tools/tm5/extreme@anta777.cfg` (`[TestN]`: its `Function` and pattern), and in the profile's `Test Sequence` to see where it runs in each cycle. Community error tables number the tests of the 1usmus_V3 profile, so their numbers mean other tests in this profile.
- Errors that come and go between cycles point at a marginal timing, voltage or heat.
- TM5 loads every core, so a Curve Optimizer margin too deep for that load miscomputes a comparison, and TM5 counts it as a memory error with no machine check. When TM5 fails with no `umc` or core bank, rule the CPU out first: soften the suspect core by 10 (`cpu_apply`, two steps of 5), keep the timings, and run one TM5 cycle.
- The suspects are the steps since the last clean TM5. With no clean TM5 yet, test the campaign's starting configuration first: a pass there limits the suspects to the campaign's steps, and a failure there points at the CPU, tRRD and tFAW, tRFC or heat.
